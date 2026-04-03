import asyncio
import httpx
from playwright.async_api import async_playwright
from scrapers.base import BaseScraper
from scrapers.proxy import ProxyManager

RENTFASTER_API = 'https://www.rentfaster.ca/api/search.json'
RENTFASTER_LISTING_URL = 'https://www.rentfaster.ca/ab/{city}/rentals/?v={ref_id}'

# Verified against live API — update if city IDs change
ALBERTA_CITIES = {
    'Calgary': 1,
    'Edmonton': 6,
    'Red Deer': 10,
    'Lethbridge': 8,
    'Medicine Hat': 9,
    'Grande Prairie': 15,
    'Airdrie': 102,
    'Sherwood Park': 21,
    'St. Albert': 22,
    'Fort McMurray': 14,
    'Okotoks': 103,
    'Cochrane': 104,
    'Canmore': 35,
    'Brooks': 47,
    'Camrose': 52,
}

# Map RentFaster API type values → canonical property types
RENTFASTER_TYPE_MAP = {
    'apartment': 'Apartment',
    'condo': 'Apartment',
    'loft': 'Loft',
    'townhouse': 'Townhome',
    'townhome': 'Townhome',
    'duplex': 'Duplex',
    'house': 'Full Home',
    'full home': 'Full Home',
    'main floor': 'MainFloor',
    'mainfloor': 'MainFloor',
    'basement': 'Basement',
    'basement suite': 'Basement',
    'room': 'Private/Sharing Room',
    'private room': 'Private/Sharing Room',
    'shared room': 'Private/Sharing Room',
    'acreage': 'Acreage',
    'farm': 'Acreage',
    'garage suite': 'Garage Suite/mobile-home',
    'garage': 'Garage Suite/mobile-home',
    'mobile home': 'Garage Suite/mobile-home',
    'office': 'Office Space',
    'office space': 'Office Space',
    'commercial': 'Office Space',
    'parking': 'Parking Spot',
    'parking spot': 'Parking Spot',
    'storage': 'Storage',
}

PAGE_SIZE = 25
HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'application/json',
}


def _parse_listing(item: dict, city_name: str) -> dict:
    """Map RentFaster API response item to our Listing field dict."""
    ref_id = str(item['ref_id'])
    city_slug = city_name.lower().replace(' ', '-')
    return {
        'source': 'rentfaster',
        'external_id': ref_id,
        'title': item.get('title', ''),
        'address': item.get('address', ''),
        'city': city_name,
        'province': 'AB',
        'postal_code': item.get('postal'),
        'property_type': RENTFASTER_TYPE_MAP.get(
            (item.get('type') or '').lower().strip(), 'Other'),
        'beds': float(item['beds']) if item.get('beds') not in (None, '', '-') else None,
        'baths': float(item['baths']) if item.get('baths') not in (None, '', '-') else None,
        'sqft': item.get('sq_feet') or None,
        'rent': item.get('price') or item.get('rent'),
        'phone': item.get('phone') or None,
        'url': RENTFASTER_LISTING_URL.format(city=city_slug, ref_id=ref_id),
        'posted_date': None,
    }


class RentFasterScraper(BaseScraper):
    def __init__(self, session, log_buffer, proxy: ProxyManager):
        super().__init__(session, log_buffer)
        self.proxy = proxy

    async def run(self):
        from datetime import date
        from models import Listing

        proxy_url = self.proxy.httpx_proxy_url()
        client_kwargs = {'headers': HEADERS, 'timeout': 30}
        if proxy_url:
            client_kwargs['proxy'] = proxy_url

        proxy_config = self.proxy.playwright_config()
        today = date.today()

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, proxy=proxy_config)
            context = await browser.new_context(
                user_agent=HEADERS['User-Agent'],
                extra_http_headers={'Accept-Language': 'en-CA,en;q=0.9'},
            )
            try:
                async with httpx.AsyncClient(**client_kwargs) as client:
                    for city_name, city_id in ALBERTA_CITIES.items():
                        self.log.append(f'[RentFaster] Scraping {city_name}...')
                        try:
                            await self._fetch_city(city_name=city_name, city_id=city_id,
                                                   client=client, pw_context=context)
                        except Exception as e:
                            self.log.append(f'[RentFaster] ERROR {city_name}: {e}')
                            self.error_count += 1
            finally:
                await browser.close()

        # Mark listings not seen today as inactive
        deactivated = (self.session.query(Listing)
                       .filter(Listing.source == 'rentfaster',
                               Listing.is_active == True,
                               Listing.last_seen < today)
                       .update({'is_active': False}, synchronize_session=False))
        self.log.append(f'[RentFaster] Marked {deactivated} listings inactive')
        self.session.commit()

    async def _fetch_city(self, city_name: str, city_id: int, client=None, pw_context=None):
        offset = 0
        _client = client or httpx.AsyncClient(headers=HEADERS, timeout=30)
        should_close = client is None

        try:
            while True:
                resp = await _client.get(
                    RENTFASTER_API,
                    params={'city_id': city_id, 'novac': offset},
                )
                resp.raise_for_status()
                data = resp.json()
                items = data.get('listings', [])

                if not items:
                    break

                for item in items:
                    try:
                        listing_data = _parse_listing(item, city_name)
                        # Reveal phone if not included in API response
                        if not listing_data['phone']:
                            listing_data['phone'] = await self._reveal_phone(
                                item['ref_id'], city_name, pw_context)
                        self.upsert_listing(listing_data)
                    except Exception as e:
                        self.log.append(
                            f'[RentFaster] Skipping listing {item.get("ref_id", "?")} '
                            f'in {city_name}: {e}')
                        self.error_count += 1

                if len(items) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE
                await asyncio.sleep(0.5)
        finally:
            if should_close:
                await _client.aclose()

    async def _reveal_phone(self, ref_id: int, city_name: str, context=None) -> str | None:
        """Open listing page in shared browser context, click Reveal, capture phone."""
        revealed_phone = None

        if context is None:
            return None  # no browser context available

        page = await context.new_page()

        async def on_response(response):
            nonlocal revealed_phone
            if 'phone' in response.url or 'contact' in response.url:
                try:
                    body = await response.json()
                    revealed_phone = (body.get('phone')
                                      or body.get('number')
                                      or body.get('tel'))
                except Exception:
                    pass

        page.on('response', on_response)

        try:
            city_slug = city_name.lower().replace(' ', '-')
            await page.goto(
                f'https://www.rentfaster.ca/ab/{city_slug}/rentals/?v={ref_id}',
                wait_until='domcontentloaded',
                timeout=20000,
            )
            reveal = page.locator(
                'button:has-text("Reveal"), '
                'a:has-text("Reveal"), '
                '[class*="reveal-phone"], '
                '[data-action*="reveal"]'
            ).first
            if await reveal.count() > 0:
                await reveal.click()
                await page.wait_for_timeout(1500)

            if not revealed_phone:
                tel = page.locator('a[href^="tel:"]').first
                if await tel.count() > 0:
                    href = await tel.get_attribute('href')
                    revealed_phone = href.replace('tel:', '').strip()
        except Exception as e:
            self.log.append(f'[RentFaster] Phone reveal failed for {ref_id}: {e}')
        finally:
            await page.close()

        return revealed_phone
