"""
Gunicorn entry point.
Initialises the DB, creates the Flask app, and starts the APScheduler
in a single background thread. Use --workers 1 — the scraper runs in
daemon threads that don't survive a fork into multiple workers.
"""
from dotenv import load_dotenv
load_dotenv()

from database import init_db
from app import create_app, _scraper_lock
import app as _app_module

init_db()
application = create_app()


def _scheduled_run():
    """Called by APScheduler — respects the same mutex as /api/run."""
    with _scraper_lock:
        if _app_module._scraper_running:
            return
        _app_module._scraper_running = True
    try:
        from scrapers import run_all
        run_all()
    finally:
        _app_module._scraper_running = False


from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

scheduler = BackgroundScheduler()
scheduler.add_job(
    func=_scheduled_run,
    trigger=CronTrigger(hour=6, minute=0, timezone=pytz.timezone('America/Edmonton')),
    id='daily_scrape',
    replace_existing=True,
)
scheduler.start()
