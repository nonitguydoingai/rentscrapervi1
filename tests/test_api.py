import pytest
import json
from datetime import date, timedelta
from unittest.mock import patch
from models import Listing, PriceHistory, ScrapeLog


@pytest.fixture
def client(db_session):
    from app import create_app
    app = create_app(db_session=db_session)
    app.config['TESTING'] = True
    with app.test_client() as c:
        yield c


def _add_listing(db_session, **kwargs):
    defaults = dict(
        source='rentfaster', external_id='test1', title='Test',
        city='Calgary', province='AB', rent=1500,
        beds=2, baths=1, first_seen=date.today(),
        last_seen=date.today(), is_active=True,
    )
    defaults.update(kwargs)
    l = Listing(**defaults)
    db_session.add(l)
    db_session.commit()
    return l


def test_index_returns_html(client):
    resp = client.get('/')
    assert resp.status_code == 200
    assert b'UrbanLease' in resp.data


def test_api_listings_returns_json(client, db_session):
    _add_listing(db_session)
    resp = client.get('/api/listings')
    assert resp.status_code == 200
    data = json.loads(resp.data)
    assert data['total'] == 1
    assert len(data['listings']) == 1


def test_api_listings_filter_by_city(client, db_session):
    _add_listing(db_session, external_id='c1', city='Calgary')
    _add_listing(db_session, external_id='e1', city='Edmonton')
    resp = client.get('/api/listings?city=Calgary')
    data = json.loads(resp.data)
    assert data['total'] == 1
    assert data['listings'][0]['city'] == 'Calgary'


def test_api_listings_filter_by_rent(client, db_session):
    _add_listing(db_session, external_id='r1', rent=800)
    _add_listing(db_session, external_id='r2', rent=2000)
    resp = client.get('/api/listings?rent_min=1000&rent_max=2500')
    data = json.loads(resp.data)
    assert data['total'] == 1
    assert data['listings'][0]['rent'] == 2000


def test_api_daily_new_landlords(client, db_session):
    _add_listing(db_session, external_id='new1', phone='4031111111',
                 first_seen=date.today())
    resp = client.get('/api/daily?filter=new_landlords')
    data = json.loads(resp.data)
    assert data['count'] >= 1


def test_api_daily_price_drops(client, db_session):
    listing = _add_listing(db_session, external_id='pd1', rent=1300)
    ph = PriceHistory(listing_id=listing.id, old_rent=1500, new_rent=1300)
    db_session.add(ph)
    db_session.commit()
    resp = client.get('/api/daily?filter=price_drops')
    data = json.loads(resp.data)
    assert data['count'] >= 1


def test_api_status_idle(client):
    resp = client.get('/api/status')
    data = json.loads(resp.data)
    assert data['status'] in ('idle', 'running')


def test_api_export_csv(client, db_session):
    _add_listing(db_session)
    resp = client.get('/api/export')
    assert resp.status_code == 200
    assert b'Calgary' in resp.data
