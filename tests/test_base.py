from datetime import date
from models import Listing, PriceHistory
from scrapers.base import BaseScraper


def _make_data(**kwargs):
    defaults = dict(
        source='rentfaster', external_id='abc1', title='Test',
        address='123 Main St', city='Calgary', province='AB',
        rent=1500, phone='4031234567', url='https://example.com',
        posted_date=None,
    )
    defaults.update(kwargs)
    return defaults


def test_new_listing_inserted(db_session, log_buffer, no_proxy):
    scraper = BaseScraper(db_session, log_buffer)
    scraper.upsert_listing(_make_data())
    db_session.commit()
    assert db_session.query(Listing).count() == 1
    assert scraper.new_count == 1
    assert scraper.updated_count == 0


def test_existing_listing_updated(db_session, log_buffer, no_proxy):
    scraper = BaseScraper(db_session, log_buffer)
    scraper.upsert_listing(_make_data(rent=1500))
    db_session.commit()
    scraper.upsert_listing(_make_data(rent=1500, title='Updated Title'))
    db_session.commit()
    listing = db_session.query(Listing).first()
    assert listing.title == 'Updated Title'
    assert scraper.updated_count == 1


def test_price_drop_creates_history(db_session, log_buffer, no_proxy):
    scraper = BaseScraper(db_session, log_buffer)
    scraper.upsert_listing(_make_data(rent=1500))
    db_session.commit()
    scraper.upsert_listing(_make_data(rent=1300))
    db_session.commit()
    history = db_session.query(PriceHistory).first()
    assert history is not None
    assert history.old_rent == 1500
    assert history.new_rent == 1300


def test_no_price_history_when_rent_unchanged(db_session, log_buffer, no_proxy):
    scraper = BaseScraper(db_session, log_buffer)
    scraper.upsert_listing(_make_data(rent=1500))
    db_session.commit()
    scraper.upsert_listing(_make_data(rent=1500))
    db_session.commit()
    assert db_session.query(PriceHistory).count() == 0


def test_first_seen_not_overwritten(db_session, log_buffer, no_proxy):
    scraper = BaseScraper(db_session, log_buffer)
    scraper.upsert_listing(_make_data())
    db_session.commit()
    original_first_seen = db_session.query(Listing).first().first_seen
    scraper.upsert_listing(_make_data(rent=1400))
    db_session.commit()
    assert db_session.query(Listing).first().first_seen == original_first_seen
