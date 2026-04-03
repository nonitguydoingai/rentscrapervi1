import csv
import io
import os
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


def create_app(db_session: Session = None):
    app = Flask(__name__)
    app.secret_key = os.getenv('SECRET_KEY', 'dev')

    def _session():
        return db_session if db_session is not None else get_session()

    # ── Helpers ─────────────────────────────────────────────────────────── #

    def _apply_listing_filters(query, args):
        keyword = args.get('keyword', '').strip()
        if keyword:
            like = f'%{keyword}%'
            query = query.filter(
                or_(Listing.title.ilike(like),
                    Listing.address.ilike(like),
                    Listing.postal_code.ilike(like))
            )
        if args.get('city'):
            query = query.filter(Listing.city == args['city'])
        if args.get('property_type'):
            query = query.filter(Listing.property_type == args['property_type'])
        if args.get('beds'):
            query = query.filter(Listing.beds == float(args['beds']))
        if args.get('baths'):
            query = query.filter(Listing.baths == float(args['baths']))
        if args.get('source'):
            query = query.filter(Listing.source == args['source'])
        if args.get('rent_min'):
            query = query.filter(Listing.rent >= int(args['rent_min']))
        if args.get('rent_max'):
            query = query.filter(Listing.rent <= int(args['rent_max']))
        if args.get('sqft_min'):
            query = query.filter(Listing.sqft >= int(args['sqft_min']))
        if args.get('sqft_max'):
            query = query.filter(Listing.sqft <= int(args['sqft_max']))
        if args.get('date_seen_from'):
            query = query.filter(
                Listing.last_seen >= date.fromisoformat(args['date_seen_from'])
            )
        if args.get('date_posted_after'):
            query = query.filter(
                Listing.posted_date >= date.fromisoformat(args['date_posted_after'])
            )
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
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 25))
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

            three_plus = session.query(Listing.phone).filter(
                Listing.phone != None, Listing.is_active == True
            ).group_by(Listing.phone).having(
                func.count(Listing.id) >= 3
            ).count()

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
            query = session.query(Listing).filter(Listing.is_active == True)
            query = _apply_listing_filters(query, request.args)
            query = _sort_query(query, request.args.get('sort_by', 'newest_first'))
            listings = query.all()

            output = io.StringIO()
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

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        func=_run_all,
        trigger=CronTrigger(hour=6, minute=0, timezone=pytz.timezone('America/Edmonton')),
        id='daily_scrape',
        replace_existing=True,
    )
    scheduler.start()

    application.run(host='0.0.0.0', port=5000, debug=False)
