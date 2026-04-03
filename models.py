import uuid
from datetime import date, datetime, timezone
from sqlalchemy import (Column, Integer, String, Text, Boolean, Date,
                        DateTime, Numeric, ForeignKey, UniqueConstraint)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


class Listing(Base):
    __tablename__ = 'listings'

    id = Column(Integer, primary_key=True)
    source = Column(String(20), nullable=False)       # rentfaster | rentalsca
    external_id = Column(String(100), nullable=False)
    title = Column(Text)
    address = Column(Text)
    city = Column(String(100))
    province = Column(String(10), default='AB')
    postal_code = Column(String(10))
    property_type = Column(String(50))
    beds = Column(Numeric(3, 1))
    baths = Column(Numeric(3, 1))
    sqft = Column(Integer)
    rent = Column(Integer)
    phone = Column(String(30))
    url = Column(Text)
    first_seen = Column(Date, nullable=False, default=date.today)
    last_seen = Column(Date, nullable=False, default=date.today)
    posted_date = Column(Date)
    is_active = Column(Boolean, default=True)

    price_history = relationship('PriceHistory', back_populates='listing',
                                 cascade='all, delete-orphan')

    __table_args__ = (UniqueConstraint('source', 'external_id', name='uq_source_external'),)


class PriceHistory(Base):
    __tablename__ = 'price_history'

    id = Column(Integer, primary_key=True)
    listing_id = Column(Integer, ForeignKey('listings.id'), nullable=False)
    old_rent = Column(Integer)
    new_rent = Column(Integer)
    changed_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    listing = relationship('Listing', back_populates='price_history')


class ScrapeLog(Base):
    __tablename__ = 'scrape_logs'

    id = Column(Integer, primary_key=True)
    run_id = Column(String(36), nullable=False, default=lambda: str(uuid.uuid4()))
    source = Column(String(20))
    started_at = Column(DateTime(timezone=True))
    finished_at = Column(DateTime(timezone=True))
    new_count = Column(Integer, default=0)
    updated_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    log_text = Column(Text, default='')
