import asyncio
import logging
from datetime import datetime, timezone

from database import get_session
from models import ScrapeLog
from scrapers.proxy import ProxyManager
from scrapers.rentfaster import RentFasterScraper
from scrapers.rentalsca import RentalsСaScraper


def run_all():
    """Entry point called by APScheduler and /api/run. Runs synchronously."""
    asyncio.run(_run_all_async())


async def _run_all_async():
    session = get_session()
    log_buffer = []
    proxy = ProxyManager()

    scrape_log = ScrapeLog(
        source='all',
        started_at=datetime.now(timezone.utc),
        log_text=''
    )
    session.add(scrape_log)
    session.commit()

    try:
        rf = RentFasterScraper(session, log_buffer, proxy)
        await rf.run()

        rc = RentalsСaScraper(session, log_buffer, proxy)
        await rc.run()

        scrape_log.new_count = rf.new_count + rc.new_count
        scrape_log.updated_count = rf.updated_count + rc.updated_count
        scrape_log.error_count = rf.error_count + rc.error_count
    except Exception as e:
        log_buffer.append(f"FATAL: {e}")
        scrape_log.error_count += 1
    finally:
        scrape_log.finished_at = datetime.now(timezone.utc)
        scrape_log.log_text = '\n'.join(log_buffer)
        session.commit()
        session.close()
