import os
from itertools import cycle
from urllib.parse import urlparse


class ProxyManager:
    """Manages rotating proxy list for web scraping."""

    def __init__(self, proxies=None):
        if proxies is None:
            # Load from environment
            proxies = [p for p in [os.getenv('PROXY_1'), os.getenv('PROXY_2')] if p]
        self._proxies = proxies
        self._cycle = cycle(proxies) if proxies else None

    def get(self):
        """Return next proxy URL string, or None for direct connection."""
        if self._cycle:
            return next(self._cycle)
        return None

    def playwright_config(self):
        """Return Playwright proxy dict or None."""
        url = self.get()
        if not url:
            return None
        parsed = urlparse(url)
        config = {'server': f'{parsed.scheme}://{parsed.hostname}:{parsed.port}'}
        if parsed.username:
            config['username'] = parsed.username
        if parsed.password:
            config['password'] = parsed.password
        return config

    def httpx_proxies(self):
        """Return httpx proxies dict or None."""
        url = self.get()
        if not url:
            return None
        return {'http://': url, 'https://': url}
