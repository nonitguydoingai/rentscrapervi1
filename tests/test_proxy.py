from scrapers.proxy import ProxyManager


def test_no_proxies_returns_none():
    pm = ProxyManager(proxies=[])
    assert pm.get() is None


def test_round_robin():
    pm = ProxyManager(proxies=['http://proxy1:8080', 'http://proxy2:8080'])
    assert pm.get() == 'http://proxy1:8080'
    assert pm.get() == 'http://proxy2:8080'
    assert pm.get() == 'http://proxy1:8080'  # wraps around


def test_playwright_config_none_when_no_proxies():
    pm = ProxyManager(proxies=[])
    assert pm.playwright_config() is None


def test_playwright_config_with_auth():
    pm = ProxyManager(proxies=['http://user:pass@proxy1.test:8080'])
    config = pm.playwright_config()
    assert config['server'] == 'http://proxy1.test:8080'
    assert config['username'] == 'user'
    assert config['password'] == 'pass'


def test_playwright_config_without_auth():
    pm = ProxyManager(proxies=['http://proxy1.test:8080'])
    config = pm.playwright_config()
    assert config['server'] == 'http://proxy1.test:8080'
    assert 'username' not in config


def test_httpx_proxies_with_proxy():
    pm = ProxyManager(proxies=['http://user:pass@proxy1.test:8080'])
    result = pm.httpx_proxies()
    assert result == {'http://': 'http://user:pass@proxy1.test:8080', 'https://': 'http://user:pass@proxy1.test:8080'}


def test_httpx_proxies_no_proxy():
    pm = ProxyManager(proxies=[])
    assert pm.httpx_proxies() is None
