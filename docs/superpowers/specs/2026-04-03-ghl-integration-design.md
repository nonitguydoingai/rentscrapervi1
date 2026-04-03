# GHL Integration Design Spec
**Date:** 2026-04-03
**Status:** Approved

---

## 1. Overview

After every scrape run, push active rental listings from 6 target cities into GoHighLevel CRM. Each listing becomes one GHL contact, placed into the pipeline matching its property type. Runs automatically in a background thread after the scraper finishes — non-blocking, fully isolated from the scrape result.

---

## 2. Architecture

```
scrapers/__init__.py → run_all() completes
                     → daemon thread: sync_to_ghl()

ghl/
├── __init__.py      # exports sync_to_ghl()
├── client.py        # GHL API v2 wrapper — auth, rate limiting, retry
├── pipeline.py      # pipeline cache — validate, load, store in DB
└── sync.py          # main sync — contact upsert per listing, log results
```

**GHL API:** `https://services.leadconnectorhq.com`
**Auth:** `Authorization: Bearer {GHL_API_KEY}` + `Version: 2021-07-28` header

---

## 3. Target Cities

Only listings from these cities are pushed to GHL:

```python
GHL_CITIES = ['Calgary', 'Airdrie', 'Chestermere', 'Okotoks', 'Cochrane', 'Balzac']
```

**Scraper additions required before GHL sync:**
- **Chestermere** — add to RentFaster `ALBERTA_CITIES` dict (city ID ~109, verify against live API) and Rentals.ca `ALBERTA_CITIES` detection list
- **Balzac** — add to Rentals.ca `ALBERTA_CITIES` detection list only (no RentFaster city ID — it's a hamlet, captured via address string matching)

---

## 4. GHL Structure

### Pipelines (14 total — created manually by user)
One pipeline per property type. Names must match exactly:

```
Acreage | Apartment | Townhome | Duplex | MainFloor | Full Home | Basement
Loft | Garage Suite/mobile-home | Office Space | Other | Parking Spot | Storage | Private/Sharing Room
```

Each pipeline needs one entry stage (e.g. "New Lead"). Stage ID is stored in the cache.

### Contacts
**1 contact per listing** (not per phone number).

| GHL Field | Source | Notes |
|---|---|---|
| `firstName` | `listing.title` | Full title as first name |
| `phone` | `listing.phone` | Normalized to E.164 (`+1XXXXXXXXXX`) |
| `address1` | `listing.address` | |
| Custom: `city` | `listing.city` | |
| Custom: `rent` | `listing.rent` | Monthly CAD |
| Custom: `beds` | `listing.beds` | |
| Custom: `baths` | `listing.baths` | |
| Custom: `source` | `listing.source` | `rentfaster` or `rentalsca` |
| Custom: `listing_url` | `listing.url` | |
| Custom: `external_id` | `listing.source + listing.external_id` | Dedup key |

**Unique identifier:** `external_id` custom field. On each sync, search GHL by this field — update if found, create if not.

---

## 5. Database Changes

### New column on `listings`
```sql
-- migrate.sql (run once on VPS before deploying new code)
ALTER TABLE listings ADD COLUMN IF NOT EXISTS ghl_contact_id VARCHAR(50);
CREATE INDEX IF NOT EXISTS idx_listings_ghl_contact_id ON listings(ghl_contact_id);
```

SQLAlchemy model gets `ghl_contact_id = Column(String(50))` added to `Listing`.

### New table: `ghl_pipelines`
```python
class GhlPipeline(Base):
    __tablename__ = 'ghl_pipelines'
    property_type = Column(String(50), primary_key=True)
    pipeline_id   = Column(String(50), nullable=False)
    stage_id      = Column(String(50), nullable=False)
    synced_at     = Column(DateTime(timezone=True))
    is_valid      = Column(Boolean, default=True)
```

Created automatically by `create_all()` on first deploy (it's a new table).

---

## 6. New .env Variables

```
GHL_API_KEY=your_location_private_integration_key
GHL_LOCATION_ID=your_sub_account_location_id
```

**Important:** Use a **Location Private Integration Key**, not an Agency key. A startup health check (`GET /locations/{GHL_LOCATION_ID}`) confirms the token has correct scope on first run.

---

## 7. Sync Flow

### Trigger
`run_all()` in `scrapers/__init__.py` launches `sync_to_ghl()` in a new daemon thread after both scrapers complete and their sessions close. The scraper's `_scraper_running` mutex is released before the thread starts — the dashboard shows "idle" as soon as scraping finishes.

### Step-by-step

```
1. Check GHL_API_KEY + GHL_LOCATION_ID set → skip entirely if missing (no error)
2. Health check: GET /locations/{GHL_LOCATION_ID} → log error + abort if 401/403
3. Pipeline validation:
   a. Query ghl_pipelines table — if cache < 24h old AND all 14 valid, use cache
   b. Otherwise: GET /opportunities/pipelines from GHL
   c. Match returned pipelines by name against 14 expected types
   d. Store matches in ghl_pipelines (pipeline_id + first stage_id)
   e. If any pipeline missing → log exactly which ones, skip sync, exit
4. Query listings: is_active=True, city IN GHL_CITIES, property_type NOT NULL
   (property_type IS NULL listings fall back to 'Other' pipeline)
5. For each listing (batches of 10, 1s sleep between batches):
   a. Resolve pipeline: ghl_pipelines[listing.property_type or 'Other']
   b. Normalize phone to E.164 using phonenumbers library
      - Invalid/unresolvable phone → store None, still create contact
   c. Build external_id key: f"{listing.source}:{listing.external_id}"
   d. If listing.ghl_contact_id set:
        PUT /contacts/{ghl_contact_id} with updated fields
        If 404 → clear stored ID, fall through to search
   e. If no ghl_contact_id:
        Search: GET /contacts/?customField[external_id]={key}
        If found → store ID, do PUT
        If not found → POST /contacts/ → store returned ID
   f. Assign to pipeline: POST /contacts/{id}/pipeline (or via opportunity)
   g. Save ghl_contact_id to listing, commit every 10 listings
6. Log: contacts created, contacts updated, errors, skipped
   → appended to existing ScrapeLog.log_text, never increments error_count
```

---

## 8. Error Handling

| Scenario | Behaviour |
|---|---|
| GHL_API_KEY not set | Skip sync silently |
| Wrong token type (403 on health check) | Log clear error, abort sync |
| Pipeline missing in GHL | Log which ones, skip entire sync |
| Pipeline cache invalid (404 on contact create) | Mark `is_valid=False` in cache, skip that property type |
| Individual contact upsert fails | Log URL + error, skip listing, continue |
| Rate limit 429 | Wait 30s, retry up to 3× |
| 5xx from GHL | Wait 10s, retry up to 3× |
| GHL fully down | All contacts fail gracefully, logged, scrape log unaffected |

Sync failure **never** raises to the scraper run — `run_all()` always completes successfully regardless of GHL state.

---

## 9. Rate Limiting

- GHL allows ~100 req/10s per location
- Sync processes in batches of 10 contacts with 1s sleep = ~10 req/s sustained
- Estimated 1,000–2,000 contacts for 6 cities → 100–200s (~2–3 min) under normal conditions
- 429 backoff adds time but sync is in its own thread, not blocking anything

---

## 10. New Dependency

```
phonenumbers==8.13.37
```

Used only in `ghl/sync.py` for E.164 normalization. Not imported anywhere else.

---

## 11. File Structure

```
ghl/
├── __init__.py      # sync_to_ghl() entry point
├── client.py        # GHLClient: post/put/get with auth + retry + rate limit
├── pipeline.py      # load_pipeline_cache(), validate_pipelines()
└── sync.py          # sync_to_ghl(): full sync orchestration

migrate.sql          # one-time DB migration (run on VPS before deploy)
```

---

## 12. One-Time Setup Steps (on VPS)

1. Create 14 pipelines in GHL with exact names listed in Section 4
2. Each pipeline needs one entry stage
3. Run `psql -d urbanlease -f migrate.sql` to add `ghl_contact_id` column
4. Add `GHL_API_KEY` and `GHL_LOCATION_ID` to `.env`
5. Create GHL custom fields: `city`, `rent`, `beds`, `baths`, `source`, `listing_url`, `external_id`
6. Restart the service — sync will validate pipelines on first run

---

## 13. Non-Goals

- No auto-creation of GHL pipelines or custom fields (manual setup required)
- No two-way sync (GHL → scraper)
- No deletion of GHL contacts when listings go inactive
- No Toronto/Vancouver (Alberta only at this stage)
