from datetime import date, datetime, timezone


class BaseScraper:
    def __init__(self, session, log_buffer):
        self.session = session
        self.log = log_buffer
        self.new_count = 0
        self.updated_count = 0
        self.error_count = 0

    def upsert_listing(self, data: dict):
        """Insert or update a listing. Records price history if rent changed."""
        from models import Listing, PriceHistory  # avoid circular at module level

        today = date.today()
        existing = (
            self.session.query(Listing)
            .filter_by(source=data['source'], external_id=data['external_id'])
            .first()
        )

        if existing is None:
            listing = Listing(
                **{k: v for k, v in data.items()},
                first_seen=today,
                last_seen=today,
                is_active=True,
            )
            self.session.add(listing)
            self.session.flush()
            self.new_count += 1
            self.log.append(
                f"NEW [{data['source']}] {data['external_id']} — "
                f"{str(data.get('title', ''))[:60]}"
            )
        else:
            # Detect rent change
            new_rent = data.get('rent')
            if existing.rent and new_rent and existing.rent != new_rent:
                history = PriceHistory(
                    listing_id=existing.id,
                    old_rent=existing.rent,
                    new_rent=new_rent,
                    changed_at=datetime.now(timezone.utc),
                )
                self.session.add(history)
                self.log.append(
                    f"PRICE CHANGE [{data['source']}] {data['external_id']} "
                    f"${existing.rent} → ${new_rent}"
                )

            # Update all fields except identity + first_seen
            skip = {'source', 'external_id', 'first_seen'}
            for key, value in data.items():
                if key not in skip and value is not None:
                    setattr(existing, key, value)
            existing.last_seen = today
            existing.is_active = True
            self.updated_count += 1
