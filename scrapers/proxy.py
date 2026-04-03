import os
from itertools import cycle
from urllib.parse import urlparse


class ProxyManager:
    """Manages rotating proxy list with fallback to direct connection."""

    def __init__(self, proxies=None):
        if proxies is None:
            proxies = [p for p in [os.getenv('PROXY_1'), os.getenv('PROXY_2')] if p]
        self._proxies = proxies
        self._cycle = cycle(proxies) if proxies else None

    def get(self):
        """Return next proxy URL string, or None for direct connection."""
        if self._cycle:
            return next(self._cycle)
        return None

    def get_all(self):
        """Return all proxy URLs followed by None (direct) as a fallback sequence."""
        return list(self._proxies) + [None]

    def playwright_config(self, url=None):
        """Return Playwright proxy dict or None. Pass url to use a specific proxy."""
        url = url if url is not None else self.get()
        if not url:
            return None
        parsed = urlparse(url)
        host = parsed.hostname
        port_part = f':{parsed.port}' if parsed.port else ''
        config = {'server': f'{parsed.scheme}://{host}{port_part}'}
        if parsed.username:
            config['username'] = parsed.username
        if parsed.password:
            config['password'] = parsed.password
        return config

    def httpx_proxy_url(self):
        """Return proxy URL string for httpx client construction, or None."""
        return self.get()
