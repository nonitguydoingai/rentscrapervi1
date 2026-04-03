import pytest
import respx
import httpx
from scrapers.rentfaster import RentFasterScraper, _parse_listing


SAMPLE_LISTING = {
    'ref_id': 12345,
    'title': 'Modern 1BR Downtown',
    'address': '100 Main Street SW',
    'city': 'Calgary',
    'type': 'Apartment',
    'price': 1650,
    'beds': '1',
    'baths': '1',
    'sq_feet': 650,
    'phone': '4031234567',
    'postal': 'T2P 1A1',
}


def test_parse_listing_maps_fields():
    result = _parse_listing(SAMPLE_LISTING, 'Calgary')
    assert result['source'] == 'rentfaster'
    assert result['external_id'] == '12345'
    assert result['rent'] == 1650
    assert result['beds'] == 1.0
    assert result['baths'] == 1.0
    assert result['city'] == 'Calgary'
    assert result['property_type'] == 'Apartment'
    assert result['phone'] == '4031234567'


def test_parse_listing_handles_missing_phone():
    item = {**SAMPLE_LISTING, 'phone': None}
    result = _parse_listing(item, 'Calgary')
    assert result['phone'] is None


def test_parse_listing_handles_missing_sqft():
    item = {**SAMPLE_LISTING, 'sq_feet': None}
    result = _parse_listing(item, 'Calgary')
    assert result['sqft'] is None


@respx.mock
async def test_fetch_city_inserts_listings(db_session, log_buffer, no_proxy):
    respx.get('https://www.rentfaster.ca/api/search.json').mock(
        return_value=httpx.Response(200, json={'listings': [SAMPLE_LISTING]})
    )
    scraper = RentFasterScraper(db_session, log_buffer, no_proxy)
    # Patch out phone reveal — listing already has a phone, so reveal won't be called
    await scraper._fetch_city(city_name='Calgary', city_id=1)
    db_session.commit()
    assert scraper.new_count == 1
