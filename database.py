import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

_engine = None
_Session = None


def _get_engine():
    global _engine
    if _engine is None:
        url = os.getenv('DATABASE_URL', 'postgresql://urbanlease:password@localhost/urbanlease')
        _engine = create_engine(url)
    return _engine


def init_db():
    """Create all tables. Call once at app startup."""
    Base.metadata.create_all(_get_engine())


def get_session():
    global _Session
    if _Session is None:
        _Session = sessionmaker(bind=_get_engine())
    return _Session()
