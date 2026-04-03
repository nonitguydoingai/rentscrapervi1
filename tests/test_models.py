from datetime import date, datetime
from models import Listing, PriceHistory, ScrapeLog


def test_listing_upsert_key(db_session):
    """Listing is uniquely identified by (source, external_id)."""
    listing = Listing(
        source='rentfaster',
        external_id='12345',
        title='Test Apartment',
        city='Calgary',
        province='AB',
        rent=1500,
        first_seen=date.today(),
        last_seen=date.today(),
        is_active=True,
    )
    db_session.add(listing)
    db_session.commit()
    found = db_session.query(Listing).filter_by(source='rentfaster', external_id='12345').first()
    assert found is not None
    assert found.rent == 1500


def test_price_history_linked(db_session):
    """PriceHistory links back to its Listing."""
    listing = Listing(source='rentfaster', external_id='999', rent=1200,
                      first_seen=date.today(), last_seen=date.today(), is_active=True)
    db_session.add(listing)
    db_session.flush()

    history = PriceHistory(listing_id=listing.id, old_rent=1200, new_rent=1100,
                           changed_at=datetime.utcnow())
    db_session.add(history)
    db_session.commit()

    assert len(listing.price_history) == 1
    assert listing.price_history[0].old_rent == 1200


def test_scrape_log_defaults(db_session):
    """ScrapeLog gets a run_id and timestamps."""
    log = ScrapeLog(source='all', started_at=datetime.utcnow())
    db_session.add(log)
    db_session.commit()
    assert log.run_id is not None
    assert log.new_count == 0
