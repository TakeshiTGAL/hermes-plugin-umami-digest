"""HTTP(S)_PROXY and friends must not receive the API key."""
from __future__ import annotations

import urllib.request
from datetime import datetime, timezone

from service import Deps, umami_digest

SITE = "11111111-1111-1111-1111-111111111111"
KEY = "proxy-secret-key-value"


def test_opener_uses_empty_proxy_handler(monkeypatch):
    import pytest
    from tests.conftest import network_blocked

    if network_blocked():
        pytest.skip("socket connect is blocked in this environment")
    seen = []
    real_build = urllib.request.build_opener

    def spy(*handlers):
        seen.extend(type(h).__name__ for h in handlers)
        for handler in handlers:
            if isinstance(handler, urllib.request.ProxyHandler):
                assert handler.proxies == {}
        return real_build(*handlers)

    monkeypatch.setattr(urllib.request, "build_opener", spy)
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("all_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("ftp_proxy", "http://127.0.0.1:9")
    deps = Deps()
    deps.secrets = lambda name: {
        "UMAMI_URL": "http://127.0.0.1:1",
        "UMAMI_API_KEY": KEY,
        "UMAMI_WEBSITE_ID": SITE,
        "UMAMI_TIMEZONE": "UTC",
    }.get(name)
    deps.now = lambda: datetime(2026, 10, 29, tzinfo=timezone.utc)
    umami_digest(deps, {})
    assert "ProxyHandler" in seen
    assert KEY not in str(seen)
