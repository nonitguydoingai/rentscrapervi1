import csv
import io
import os
import secrets
import threading
from datetime import date, timedelta, datetime, timezone

from dotenv import load_dotenv
load_dotenv()

from flask import Flask, jsonify, render_template, request, Response
from sqlalchemy import or_, func
from sqlalchemy.orm import Session

from database import init_db, get_session
from models import Listing, PriceHistory, ScrapeLog

_scraper_running = False
_scraper_lock = threading.Lock()


def _safe_int(val, default=None):
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def _safe_float(val, default=None):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_date(val, default=None):
    try:
        return date.fromisoformat(val)
    except (TypeError, ValueError):
        return default


def create_app(db_session: Session = None):
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY') or secrets.token_hex(32)

    def _session():
        return db_session if db_session is not None else get_session()

    # ── Helpers ─────────────────────────────────────────────────────────── #

    def _apply_listing_filters(query, args):
        keyword = args.get('keyword', '').strip()
        if keyword:
            # Escape LIKE special characters to prevent wildcard injection
            safe_kw = keyword.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
            like = f'%{safe_kw}%'
            query = query.filter(
                or_(Listing.title.ilike(like),
                    Listing.address.ilike(like),
                    Listing.postal_code.ilike(like))
            )
        if args.get('city'):
            query = query.filter(Listing.city == args['city'])
        if args.get('property_type'):
            query = query.filter(Listing.property_type == args['property_type'])
        beds = _safe_float(args.get('beds'))
        if beds is not None:
            query = query.filter(Listing.beds == beds)
        baths = _safe_float(args.get('baths'))
        if baths is not None:
            query = query.filter(Listing.baths == baths)
        if args.get('source'):
            query = query.filter(Listing.source == args['source'])
        rent_min = _safe_int(args.get('rent_min'))
        if rent_min is not None:
            query = query.filter(Listing.rent >= rent_min)
        rent_max = _safe_int(args.get('rent_max'))
        if rent_max is not None:
            query = query.filter(Listing.rent <= rent_max)
        sqft_min = _safe_int(args.get('sqft_min'))
        if sqft_min is not None:
            query = query.filter(Listing.sqft >= sqft_min)
        sqft_max = _safe_int(args.get('sqft_max'))
        if sqft_max is not None:
            query = query.filter(Listing.sqft <= sqft_max)
        date_seen = _safe_date(args.get('date_seen_from'))
        if date_seen is not None:
            query = query.filter(Listing.last_seen >= date_seen)
        date_posted = _safe_date(args.get('date_posted_after'))
        if date_posted is not None:
            query = query.filter(Listing.posted_date >= date_posted)
        return query

    def _sort_query(query, sort_by):
        sorts = {
            'newest_first': Listing.first_seen.desc(),
            'oldest_first': Listing.first_seen.asc(),
            'rent_low': Listing.rent.asc(),
            'rent_high': Listing.rent.desc(),
            'recent_seen': Listing.last_seen.desc(),
        }
        return query.order_by(sorts.get(sort_by, Listing.first_seen.desc()))

    def _listing_to_dict(l: Listing) -> dict:
        return {
            'id': l.id,
            'source': l.source,
            'title': l.title,
            'address': l.address,
            'city': l.city,
            'property_type': l.property_type,
            'beds': float(l.beds) if l.beds else None,
            'baths': float(l.baths) if l.baths else None,
            'sqft': l.sqft,
            'rent': l.rent,
            'phone': l.phone,
            'url': l.url,
            'first_seen': l.first_seen.isoformat() if l.first_seen else None,
            'last_seen': l.last_seen.isoformat() if l.last_seen else None,
            'posted_date': l.posted_date.isoformat() if l.posted_date else None,
        }

    # ── Routes ──────────────────────────────────────────────────────────── #

    @app.route('/')
    def index():
        return render_template('index.html')

    @app.route('/api/listings')
    def api_listings():
        session = _session()
        try:
            query = session.query(Listing).filter(Listing.is_active == True)
            query = _apply_listing_filters(query, request.args)
            query = _sort_query(query, request.args.get('sort_by', 'newest_first'))
            total = query.count()
            page = max(1, _safe_int(request.args.get('page'), 1))
            per_page = min(max(1, _safe_int(request.args.get('per_page'), 25)), 100)
            listings = query.offset((page - 1) * per_page).limit(per_page).all()
            return jsonify({
                'total': total,
                'page': page,
                'per_page': per_page,
                'listings': [_listing_to_dict(l) for l in listings],
            })
        finally:
            if db_session is None:
                session.close()

    @app.route('/api/daily')
    def api_daily():
        session = _session()
        try:
            today = date.today()
            filter_type = request.args.get('filter', 'new_landlords')

            if filter_type == 'new_landlords':
                rows = (session.query(Listing)
                        .filter(Listing.first_seen == today, Listing.is_active == True)
                        .order_by(Listing.first_seen.desc()).all())
                count = len(rows)
                data = [_listing_to_dict(l) for l in rows]

            elif filter_type == 'three_plus':
                subq = (session.query(Listing.phone,
                                      func.count(Listing.id).label('cnt'))
                        .filter(Listing.phone != None, Listing.is_active == True)
                        .group_by(Listing.phone)
                        .having(func.count(Listing.id) >= 3)
                        .subquery())
                phones = session.query(subq.c.phone, subq.c.cnt).all()
                count = len(phones)
                data = [{'phone': p, 'listing_count': c} for p, c in phones]

            elif filter_type == 'fourteen_plus':
                cutoff = today - timedelta(days=14)
                rows = (session.query(Listing)
                        .filter(Listing.first_seen <= cutoff,
                                Listing.is_active == True)
                        .order_by(Listing.first_seen.asc()).all())
                count = len(rows)
                data = [
                    {**_listing_to_dict(l),
                     'days_listed': (today - l.first_seen).days}
                    for l in rows
                ]

            elif filter_type == 'price_drops':
                today_start = datetime.combine(today, datetime.min.time()).replace(
                    tzinfo=timezone.utc)
                rows = (session.query(Listing, PriceHistory)
                        .join(PriceHistory)
                        .filter(PriceHistory.changed_at >= today_start,
                                PriceHistory.new_rent < PriceHistory.old_rent)
                        .all())
                count = len(rows)
                data = [
                    {**_listing_to_dict(l),
                     'old_rent': ph.old_rent,
                     'new_rent': ph.new_rent}
                    for l, ph in rows
                ]
            else:
                return jsonify({'error': 'invalid filter'}), 400

            return jsonify({'filter': filter_type, 'count': count, 'data': data})
        finally:
            if db_session is None:
                session.close()

    @app.route('/api/daily/counts')
    def api_daily_counts():
        """Returns all 4 badge counts + new_today for the dashboard header."""
        session = _session()
        try:
            today = date.today()
            cutoff_14 = today - timedelta(days=14)
            today_start = datetime.combine(today, datetime.min.time()).replace(
                tzinfo=timezone.utc)

            new_today = session.query(func.count(Listing.id)).filter(
                Listing.first_seen == today, Listing.is_active == True
            ).scalar() or 0

            new_landlords = new_today  # same definition for the badge

            three_plus_subq = (
                session.query(Listing.phone)
                .filter(Listing.phone != None, Listing.is_active == True)
                .group_by(Listing.phone)
                .having(func.count(Listing.id) >= 3)
                .subquery()
            )
            three_plus = session.query(func.count()).select_from(three_plus_subq).scalar() or 0

            fourteen_plus = session.query(func.count(Listing.id)).filter(
                Listing.first_seen <= cutoff_14, Listing.is_active == True
            ).scalar() or 0

            price_drops = session.query(func.count(PriceHistory.id)).filter(
                PriceHistory.changed_at >= today_start,
                PriceHistory.new_rent < PriceHistory.old_rent
            ).scalar() or 0

            return jsonify({
                'new_today': new_today,
                'new_landlords': new_landlords,
                'three_plus': three_plus,
                'fourteen_plus': fourteen_plus,
                'price_drops': price_drops,
            })
        finally:
            if db_session is None:
                session.close()

    @app.route('/api/logs')
    def api_logs():
        session = _session()
        try:
            logs = (session.query(ScrapeLog)
                    .order_by(ScrapeLog.started_at.desc())
                    .limit(50).all())
            return jsonify([{
                'run_id': l.run_id,
                'source': l.source,
                'started_at': l.started_at.isoformat() if l.started_at else None,
                'finished_at': l.finished_at.isoformat() if l.finished_at else None,
                'new_count': l.new_count,
                'updated_count': l.updated_count,
                'error_count': l.error_count,
                'log_text': l.log_text,
            } for l in logs])
        finally:
            if db_session is None:
                session.close()

    @app.route('/api/export')
    def api_export():
        session = _session()
        try:
            today = date.today()
            filter_type = request.args.get('filter')
            output = io.StringIO()

            if filter_type == 'three_plus':
                subq = (session.query(Listing.phone,
                                      func.count(Listing.id).label('cnt'),
                                      func.string_agg(Listing.address, ' | ').label('addrs'))
                        .filter(Listing.phone != None, Listing.is_active == True)
                        .group_by(Listing.phone)
                        .having(func.count(Listing.id) >= 3)
                        .all())
                writer = csv.DictWriter(output, fieldnames=['phone', 'listing_count', 'addresses'])
                writer.writeheader()
                for row in subq:
                    writer.writerow({'phone': row.phone, 'listing_count': row.cnt,
                                     'addresses': row.addrs or ''})

            elif filter_type == 'price_drops':
                today_start = datetime.combine(today, datetime.min.time()).replace(
                    tzinfo=timezone.utc)
                rows = (session.query(Listing, PriceHistory)
                        .join(PriceHistory)
                        .filter(PriceHistory.changed_at >= today_start,
                                PriceHistory.new_rent < PriceHistory.old_rent)
                        .all())
                writer = csv.DictWriter(output, fieldnames=[
                    'title', 'phone', 'address', 'city', 'old_rent', 'new_rent', 'beds', 'baths', 'url'])
                writer.writeheader()
                for l, ph in rows:
                    writer.writerow({
                        'title': l.title or '', 'phone': l.phone or '',
                        'address': l.address or '', 'city': l.city or '',
                        'old_rent': ph.old_rent, 'new_rent': ph.new_rent,
                        'beds': float(l.beds) if l.beds else '',
                        'baths': float(l.baths) if l.baths else '',
                        'url': l.url or '',
                    })

            elif filter_type == 'fourteen_plus':
                cutoff = today - timedelta(days=14)
                rows = (session.query(Listing)
                        .filter(Listing.first_seen <= cutoff, Listing.is_active == True)
                        .order_by(Listing.first_seen.asc()).all())
                writer = csv.DictWriter(output, fieldnames=[
                    'days_listed', 'title', 'phone', 'address', 'city', 'beds', 'baths', 'rent', 'url'])
                writer.writeheader()
                for l in rows:
                    writer.writerow({
                        'days_listed': (today - l.first_seen).days if l.first_seen else '',
                        'title': l.title or '', 'phone': l.phone or '',
                        'address': l.address or '', 'city': l.city or '',
                        'beds': float(l.beds) if l.beds else '',
                        'baths': float(l.baths) if l.baths else '',
                        'rent': l.rent or '', 'url': l.url or '',
                    })

            else:
                # new_landlords filter or standard main listings export
                query = session.query(Listing).filter(Listing.is_active == True)
                if filter_type == 'new_landlords':
                    query = query.filter(Listing.first_seen == today)
                else:
                    query = _apply_listing_filters(query, request.args)
                query = _sort_query(query, request.args.get('sort_by', 'newest_first'))
                listings = query.all()
                writer = csv.DictWriter(output, fieldnames=[
                    'title', 'address', 'city', 'property_type', 'beds', 'baths',
                    'sqft', 'rent', 'phone', 'source', 'first_seen', 'last_seen',
                    'posted_date', 'url',
                ])
                writer.writeheader()
                for l in listings:
                    writer.writerow({
                        'title': l.title, 'address': l.address, 'city': l.city,
                        'property_type': l.property_type,
                        'beds': float(l.beds) if l.beds else '',
                        'baths': float(l.baths) if l.baths else '',
                        'sqft': l.sqft or '', 'rent': l.rent or '',
                        'phone': l.phone or '', 'source': l.source,
                        'first_seen': l.first_seen.isoformat() if l.first_seen else '',
                        'last_seen': l.last_seen.isoformat() if l.last_seen else '',
                        'posted_date': l.posted_date.isoformat() if l.posted_date else '',
                        'url': l.url or '',
                    })

            return Response(
                output.getvalue(),
                mimetype='text/csv',
                headers={'Content-Disposition': 'attachment; filename=listings.csv'},
            )
        finally:
            if db_session is None:
                session.close()

    @app.route('/api/status')
    def api_status():
        return jsonify({'status': 'running' if _scraper_running else 'idle'})

    @app.route('/api/run', methods=['POST'])
    def api_run():
        global _scraper_running
        with _scraper_lock:
            if _scraper_running:
                return jsonify({'error': 'Scraper already running'}), 409
            _scraper_running = True

        def _run():
            global _scraper_running
            try:
                from scrapers import run_all
                run_all()
            finally:
                _scraper_running = False

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()
        return jsonify({'status': 'started'})

    return app


if __name__ == '__main__':
    init_db()
    application = create_app()

    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    import pytz
    from scrapers import run_all as _run_all

    def _scheduled_run():
        """Scheduled entry point — respects the same mutex as /api/run."""
        global _scraper_running
        with _scraper_lock:
            if _scraper_running:
                return  # manual run already in progress, skip this cycle
            _scraper_running = True
        try:
            _run_all()
        finally:
            _scraper_running = False

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=_scheduled_run,
        trigger=CronTrigger(hour=6, minute=0, timezone=pytz.timezone('America/Edmonton')),
        id='daily_scrape',
        replace_existing=True,
    )
    scheduler.start()

    application.run(host='0.0.0.0', port=5000, debug=False)
