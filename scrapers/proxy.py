class ProxyManager:
    """Manages rotating proxy list for web scraping."""

    def __init__(self, proxies=None):
        self.proxies = proxies or []
