import os
import threading
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from models import Base

_engine = None
_Session = None
_lock = threading.Lock()


def _get_engine():
    global _engine
    if _engine is None:
        with _lock:
            if _engine is None:  # double-checked locking
                url = os.getenv('DATABASE_URL')
                if not url:
                    raise EnvironmentError(
                        'DATABASE_URL environment variable is not set. '
                        'Copy .env.example to .env and fill in your credentials.'
                    )
                _engine = create_engine(url, pool_pre_ping=True)
    return _engine


def init_db():
    """Create all tables. Call once at app startup."""
    Base.metadata.create_all(_get_engine())


def get_session():
    global _Session
    with _lock:
        if _Session is None:
            _Session = sessionmaker(bind=_get_engine())
    return _Session()
