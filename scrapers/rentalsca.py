import asyncio
import re
from playwright.async_api import async_playwright, Page
from scrapers.base import BaseScraper
from scrapers.proxy import ProxyManager

BASE_URL = 'https://rentals.ca'
SEARCH_URL = 'https://rentals.ca/alberta?p={page}'

ALBERTA_CITIES = [
    'Calgary', 'Edmonton', 'Red Deer', 'Lethbridge', 'Medicine Hat',
    'Grande Prairie', 'Airdrie', 'Sherwood Park', 'St. Albert',
    'Fort McMurray', 'Okotoks', 'Cochrane', 'Canmore', 'Brooks', 'Camrose',
]

HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    )
}


def _clean_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r'\D', '', raw)
    return digits if len(digits) >= 10 else None


def _parse_rent(raw: str | None) -> int | None:
    if not raw:
        return None
    digits = re.sub(r'[^\d]', '', raw)
    return int(digits) if digits else None


class RentalsСaScraper(BaseScraper):
    def __init__(self, session, log_buffer, proxy: ProxyManager):
        super().__init__(session, log_buffer)
        self.proxy = proxy

    async def run(self):
        proxy_config = self.proxy.playwright_config()
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, proxy=proxy_config)
            context = await browser.new_context(
                user_agent=HEADERS['User-Agent'],
                extra_http_headers={'Accept-Language': 'en-CA,en;q=0.9'},
            )
            try:
                listing_urls = await self._collect_listing_urls(context)
                self.log.append(f'[Rentals.ca] Found {len(listing_urls)} listings')
                for url in listing_urls:
                    try:
                        page = await context.new_page()
                        data = await self._scrape_listing_page(page, url)
                        await page.close()
                        if data:
                            self.upsert_listing(data)
                    except Exception as e:
                        self.log.append(f'[Rentals.ca] ERROR {url}: {e}')
                        self.error_count += 1
                    await asyncio.sleep(0.5)
            finally:
                await browser.close()
        self.session.commit()

    async def _collect_listing_urls(self, context) -> list[str]:
        """Paginate through Alberta search results and collect all listing URLs."""
        urls = []
        page_num = 1
        max_pages = getattr(self, '_max_pages', None)  # allows smoke-test limiting
        page = await context.new_page()

        while True:
            if max_pages and page_num > max_pages:
                break
            await page.goto(
                SEARCH_URL.format(page=page_num),
                wait_until='networkidle',
                timeout=30000,
            )
            cards = await page.locator('a[href*="/listing/"]').all()
            if not cards:
                break

            batch = []
            for card in cards:
                href = await card.get_attribute('href')
                if href and '/listing/' in href:
                    full = href if href.startswith('http') else BASE_URL + href
                    if full not in urls:
                        batch.append(full)

            if not batch:
                break

            urls.extend(batch)
            self.log.append(f'[Rentals.ca] Page {page_num}: {len(batch)} listings')

            next_btn = page.locator('[aria-label="Next page"], a:has-text("Next")').first
            if await next_btn.count() == 0:
                break
            page_num += 1
            await asyncio.sleep(1)

        await page.close()
        return urls

    async def _scrape_listing_page(self, page: Page, url: str) -> dict | None:
        """Scrape a single Rentals.ca listing page."""
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)

        external_id = url.rstrip('/').split('/')[-1]

        async def text(selector: str) -> str | None:
            el = page.locator(selector).first
            if await el.count() > 0:
                return (await el.inner_text()).strip()
            return None

        title = await text('h1')
        rent_raw = await text('[class*="price"], [class*="rent"]')
        address = await text('[class*="address"]')
        beds_raw = await text('[class*="beds"]')
        baths_raw = await text('[class*="baths"]')
        sqft_raw = await text('[class*="sqft"], [class*="sq-ft"]')
        prop_type = await text('[class*="property-type"], [class*="listing-type"]')

        # Phone — try tel: link first, then reveal button
        phone = None
        tel_link = page.locator('a[href^="tel:"]').first
        if await tel_link.count() > 0:
            href = await tel_link.get_attribute('href')
            phone = _clean_phone(href.replace('tel:', ''))
        else:
            reveal_btn = page.locator(
                'button:has-text("Show"), button:has-text("Reveal"), '
                '[class*="show-phone"], [class*="reveal"]'
            ).first
            if await reveal_btn.count() > 0:
                await reveal_btn.click()
                await page.wait_for_timeout(1000)
                tel_link2 = page.locator('a[href^="tel:"]').first
                if await tel_link2.count() > 0:
                    href = await tel_link2.get_attribute('href')
                    phone = _clean_phone(href.replace('tel:', ''))

        # Parse city from address
        city = None
        if address:
            for c in ALBERTA_CITIES:
                if c.lower() in address.lower():
                    city = c
                    break

        beds_val = None
        if beds_raw:
            m = re.search(r'[\d.]+', beds_raw)
            beds_val = float(m.group()) if m else None

        baths_val = None
        if baths_raw:
            m = re.search(r'[\d.]+', baths_raw)
            baths_val = float(m.group()) if m else None

        sqft_val = None
        if sqft_raw:
            m = re.search(r'\d+', sqft_raw.replace(',', ''))
            sqft_val = int(m.group()) if m else None

        return {
            'source': 'rentalsca',
            'external_id': external_id,
            'title': title,
            'address': address,
            'city': city,
            'province': 'AB',
            'postal_code': None,
            'property_type': prop_type,
            'beds': beds_val,
            'baths': baths_val,
            'sqft': sqft_val,
            'rent': _parse_rent(rent_raw),
            'phone': phone,
            'url': url,
            'posted_date': None,
        }
