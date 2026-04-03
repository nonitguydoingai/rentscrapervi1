import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base
from scrapers.proxy import ProxyManager


@pytest.fixture
def db_session():
    engine = create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def log_buffer():
    return []


@pytest.fixture
def no_proxy():
    """ProxyManager with no proxies configured."""
    return ProxyManager(proxies=[])


@pytest.fixture
def mock_proxy():
    """ProxyManager with two fake proxies."""
    return ProxyManager(proxies=[
        'http://user:pass@proxy1.test:8080',
        'http://user:pass@proxy2.test:8080',
    ])
