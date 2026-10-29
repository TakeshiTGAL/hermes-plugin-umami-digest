"""Secrets come from get_secret. The key never appears in output."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import service
from service import Deps, umami_digest

KEY = "super-secret-umami-key-do-not-leak"
SITE = "11111111-1111-1111-1111-111111111111"
ROOT = Path(__file__).resolve().parents[1]
_ENV_READ = re.compile(
    r"""os\.environ(?:\.get\(|\[)\s*['"]UMAMI_|os\.getenv\(\s*['"]UMAMI_"""
)


def test_plugin_code_does_not_read_umami_from_environ():
    for path in sorted(ROOT.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert _ENV_READ.search(text) is None, path.name


def test_key_not_in_error_text():
    deps = Deps()
    deps.secrets = lambda name: {
        "UMAMI_URL": "http://127.0.0.1:9",
        "UMAMI_API_KEY": KEY,
        "UMAMI_WEBSITE_ID": SITE,
        "UMAMI_TIMEZONE": "UTC",
    }.get(name)
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)

    def boom(url, token):
        raise service.client.ClientError("http_401", f"HTTP 401 token={token} url={url}")

    deps.transport = boom
    out = umami_digest(deps, {})
    blob = str(out)
    assert KEY not in blob
    assert "super-secret" not in blob


def test_unscoped_multiplex_raises_and_does_not_call_upstream(monkeypatch):
    scope = pytest.importorskip("agent.secret_scope")
    monkeypatch.setenv("UMAMI_URL", "http://127.0.0.1:3000")
    monkeypatch.setenv("UMAMI_API_KEY", KEY)
    monkeypatch.setenv("UMAMI_WEBSITE_ID", SITE)
    calls = []

    def transport(url, token):
        calls.append((url, token))
        raise AssertionError("upstream was called")

    deps = Deps()
    deps.transport = transport
    deps.secrets = None
    scope.set_multiplex_active(True)
    token = scope.set_secret_scope(None)
    try:
        with pytest.raises(scope.UnscopedSecretError):
            umami_digest(deps, {})
    finally:
        try:
            scope.reset_secret_scope(token)
        except Exception:
            try:
                scope.set_secret_scope(token)
            except Exception:
                pass
        try:
            scope.set_multiplex_active(False)
        except Exception:
            pass
    assert calls == []


def test_redact_helper():
    from gates import redact

    assert KEY not in redact(f"Bearer {KEY} failed", KEY)
    assert "[redacted]" in redact(KEY, KEY)
