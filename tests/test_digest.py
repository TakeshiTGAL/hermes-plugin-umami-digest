"""Yesterday window, surge constant, unknown vs zero, path escaping."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import digest
from digest import ConfigError, DigestError
from service import Deps, digest_stdout, umami_digest

SITE = "11111111-1111-1111-1111-111111111111"
SITE2 = "22222222-2222-2222-2222-222222222222"
KEY = "umami_secret_key_value_9f3a"


def secrets(**extra):
    base = {
        "UMAMI_URL": "http://127.0.0.1:3000",
        "UMAMI_API_KEY": KEY,
        "UMAMI_WEBSITE_ID": SITE,
        "UMAMI_TIMEZONE": "UTC",
    }
    base.update(extra)
    return lambda name: base.get(name)


def stats(pageviews, visitors, prev_pv, prev_visitors):
    return {
        "pageviews": pageviews,
        "visitors": visitors,
        "visits": pageviews,
        "bounces": 0,
        "totaltime": 0,
        "comparison": {
            "pageviews": prev_pv,
            "visitors": prev_visitors,
            "visits": prev_pv,
            "bounces": 0,
            "totaltime": 0,
        },
    }


class Router:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.stats_body = stats(12, 4, 5, 2)
        self.metrics_body = [
            {"name": "/home", "pageviews": 8},
            {"name": "/blog", "pageviews": 4},
        ]
        self.by_path: dict[str, tuple[int, object]] = {}

    def __call__(self, url: str, token: str) -> tuple[int, bytes]:
        self.calls.append(url)
        assert token == KEY
        import json
        from urllib.parse import urlsplit

        path = urlsplit(url).path
        if path in self.by_path:
            status, payload = self.by_path[path]
            raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
            return status, raw
        if path.endswith("/stats"):
            return 200, json.dumps(self.stats_body).encode("utf-8")
        if path.endswith("/metrics/expanded"):
            return 200, json.dumps(self.metrics_body).encode("utf-8")
        return 404, b'{"error":{"message":"missing"}}'


def test_yesterday_window_tokyo():
    now = datetime(2026, 10, 29, 8, 15, tzinfo=ZoneInfo("Asia/Tokyo"))
    start, end, day = digest.yesterday_window(now, ZoneInfo("Asia/Tokyo"))
    assert day == "2026-10-28"
    # 2026-10-28 00:00 JST = 2026-10-27 15:00 UTC
    assert start == int(datetime(2026, 10, 27, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
    assert end == int(datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)


def test_yesterday_window_utc():
    now = datetime(2026, 10, 29, 0, 1, tzinfo=timezone.utc)
    start, end, day = digest.yesterday_window(now, ZoneInfo("UTC"))
    assert day == "2026-10-28"
    assert end - start == 24 * 60 * 60 * 1000


def test_count_value_shapes():
    assert digest.count_value(12) == 12
    assert digest.count_value({"value": 7}) == 7
    assert digest.count_value(None) is None
    assert digest.count_value(True) is None
    assert digest.count_value("nope") is None
    assert digest.count_value(3.2) is None
    assert digest.count_value(0) == 0


def test_surge_constant_two():
    assert digest.SURGE_RATIO == 2.0
    assert digest.NEW_TRAFFIC == "new traffic"
    assert digest.surge_line(10, 4) == (
        "Surge: yes (pageviews 2.5x versus the previous day, threshold 2.0x)."
    )
    assert digest.surge_line(4, 2) == (
        "Surge: yes (pageviews 2.0x versus the previous day, threshold 2.0x)."
    )
    assert digest.surge_line(9, 5) == "Surge: no."
    assert digest.surge_line(None, 4) == "Surge: unknown (comparison pageviews were not readable)."
    assert digest.surge_line(10, None) == "Surge: unknown (comparison pageviews were not readable)."
    zero_prev = digest.surge_line(4, 0)
    assert zero_prev == "Surge: new traffic (pageviews 4 vs 0)."
    assert "threshold" not in zero_prev
    assert "yes" not in zero_prev
    assert digest.surge_line(0, 0) == "Surge: no."


def test_zero_traffic_is_still_a_letter():
    router = Router()
    router.stats_body = stats(0, 0, 0, 0)
    router.metrics_body = []
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert out["ok"] is True
    assert "Pageviews: 0 vs 0" in out["message"]
    assert "none recorded" in out["message"]
    assert "unknown" not in out["message"]
    assert "among the first" not in out["message"]


def test_unread_stats_are_unknown_not_zero():
    router = Router()
    router.stats_body = {"visitors": 3, "comparison": {"visitors": 1}}
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert out["ok"] is True
    assert "Pageviews: unknown" in out["message"]
    assert "Pageviews: 0" not in out["message"]
    assert "Surge: unknown" in out["message"]


def test_missing_comparison_is_unknown():
    router = Router()
    router.stats_body = {"pageviews": 4, "visitors": 2}
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert "previous-day comparison was missing" in out["message"]
    assert "Surge: unknown" in out["message"]


def test_markdown_and_control_in_paths():
    router = Router()
    router.metrics_body = [
        {"name": "/ok", "pageviews": 3},
        {"name": "/x\n*bold*`tick`", "pageviews": 9},
        {"name": "/p](http://evil)", "pageviews": 1},
    ]
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "`/x?*bold*'tick'`" in text
    assert "\n*bold*" not in text
    assert KEY not in text


def test_top_five_only():
    router = Router()
    router.metrics_body = [{"name": f"/p{i}", "pageviews": i} for i in range(8)]
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "`/p7`" in text
    assert "`/p3`" in text
    assert "`/p2`" not in text
    assert text.count("\n1. ") == 1
    assert "among the first" not in text


def test_partial_site_failure_does_not_call_unread_zero():
    router = Router()
    router.by_path = {
        f"/api/websites/{SITE}/stats": (200, stats(2, 1, 1, 1)),
        f"/api/websites/{SITE}/metrics/expanded": (200, [{"name": "/", "pageviews": "2"}]),
        f"/api/websites/{SITE2}/stats": (500, {"error": {"message": "down"}}),
        f"/api/websites/{SITE2}/metrics/expanded": (500, {"error": {"message": "down"}}),
    }
    deps = Deps()
    deps.secrets = secrets(UMAMI_WEBSITE_ID=f"{SITE},{SITE2}")
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "Pageviews: 2 vs 1" in text
    assert "1. `/` — 2" in text
    assert "Could not read stats" in text
    assert "HTTP 500" in text
    assert f"Website `{SITE2}`" in text
    assert "zero-traffic" not in text.lower() or "not a zero" in text.lower() or "unknown" in text


def test_all_sites_fail_is_error_not_zero_day():
    router = Router()
    router.by_path = {
        f"/api/websites/{SITE}/stats": (401, {"error": {"message": "no"}}),
        f"/api/websites/{SITE}/metrics/expanded": (401, {"error": {"message": "no"}}),
    }
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert out["ok"] is False
    assert out["error_type"] == "all_failed"
    assert "not a zero-traffic day" in out["message"]
    assert KEY not in out["message"]


def test_metrics_failure_keeps_stats_and_unknown_pages():
    router = Router()
    router.by_path = {
        f"/api/websites/{SITE}/stats": (200, stats(4, 2, 2, 1)),
        f"/api/websites/{SITE}/metrics/expanded": (503, {"error": {"message": "busy"}}),
    }
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "Pageviews: 4 vs 2" in text
    assert "Top pages: unknown" in text
    assert "none recorded" not in text


def test_invalid_timezone_fails_closed():
    deps = Deps()
    deps.secrets = secrets(UMAMI_TIMEZONE="Not/A_Zone")
    deps.transport = Router()
    out = umami_digest(deps, {})
    assert out["ok"] is False
    assert out["error_type"] == "bad_tz"
    assert deps.transport.calls == []


def test_digest_stdout_writes_letter(capsys):
    router = Router()
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = digest_stdout(deps)
    assert text.startswith("Umami digest for 2026-10-28")
    assert "Surge: yes" in text


def test_digest_stdout_failure_exits(capsys):
    deps = Deps()
    deps.secrets = secrets(UMAMI_URL="")
    with pytest.raises(SystemExit) as raised:
        digest_stdout(deps)
    assert raised.value.code == 1
    err = capsys.readouterr().err
    assert "UMAMI_URL" in err
    assert KEY not in err


def test_compare_prev_is_sent():
    router = Router()
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    umami_digest(deps, {})
    assert any("compare=prev" in url and "/stats" in url for url in router.calls)
    assert any("type=path" in url and "/metrics/expanded" in url for url in router.calls)
    assert any("limit=500" in url and "offset=0" in url for url in router.calls)
    assert not any(url.split("?", 1)[0].endswith("/metrics") for url in router.calls)


def test_nested_value_pageviews():
    router = Router()
    router.stats_body = {
        "pageviews": {"value": 6},
        "visitors": {"value": 2},
        "comparison": {"pageviews": {"value": 3}, "visitors": {"value": 1}},
    }
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "Pageviews: 6 vs 3" in text


def _expanded_transport(pages):
    """pages is a function(offset) -> list. Stats are a small successful day."""
    calls = []

    def transport(url, token):
        import json
        from urllib.parse import parse_qs, urlsplit

        calls.append(url)
        assert token == KEY
        parts = urlsplit(url)
        if parts.path.endswith("/stats"):
            return 200, json.dumps(stats(9, 3, 1, 1)).encode()
        query = parse_qs(parts.query)
        assert query["type"] == ["path"]
        assert int(query["limit"][0]) == digest.EXPANDED_PAGE_LIMIT
        offset = int(query["offset"][0])
        body = pages(offset)
        return 200, json.dumps(body).encode()

    return calls, transport


def test_top_five_walks_past_one_page():
    """A high pageview path on the second visitor-ordered page outranks page one."""
    def pages(offset):
        if offset == 0:
            rows = [
                {"name": f"/low/{i}", "pageviews": "1", "visitors": 1000 - i}
                for i in range(digest.EXPANDED_PAGE_LIMIT)
            ]
            rows[0] = {"name": "/popular-visitors", "pageviews": "2", "visitors": 9999}
            return rows
        if offset == digest.EXPANDED_PAGE_LIMIT:
            return [{"name": "/buried", "pageviews": "40", "visitors": 1}]
        raise AssertionError(offset)

    calls, transport = _expanded_transport(pages)
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = transport
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "1. `/buried` — 40" in text
    assert text.index("`/buried`") < text.index("`/popular-visitors`")
    assert "among the first" not in text
    assert KEY not in text
    metric_calls = [url for url in calls if "/metrics/expanded" in url]
    assert len(metric_calls) == 2
    assert "offset=0" in metric_calls[0]
    assert "offset=500" in metric_calls[1]


def test_top_five_stops_at_page_cap():
    def pages(offset):
        if offset >= digest.EXPANDED_PAGE_LIMIT * digest.EXPANDED_MAX_PAGES:
            raise AssertionError(offset)
        rows = [
            {"name": f"/p/{offset + i}", "pageviews": "1"}
            for i in range(digest.EXPANDED_PAGE_LIMIT)
        ]
        if offset == 0:
            rows[0] = {"name": "/kept", "pageviews": "7"}
        return rows

    calls, transport = _expanded_transport(pages)
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = transport
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    window = digest.EXPANDED_PAGE_LIMIT * digest.EXPANDED_MAX_PAGES
    assert f"top 5 among the first {window} paths" in text
    assert "`/kept`" in text
    assert "/should-not-see" not in text
    assert KEY not in text
    metric_calls = [url for url in calls if "/metrics/expanded" in url]
    assert len(metric_calls) == digest.EXPANDED_MAX_PAGES
    assert all("limit=500" in url for url in metric_calls)
    assert "offset=9500" in metric_calls[-1]
    assert not any("offset=10000" in url for url in metric_calls)


def test_empty_expanded_page_is_none_recorded():
    def pages(offset):
        assert offset == 0
        return []

    calls, transport = _expanded_transport(pages)
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = transport
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    text = umami_digest(deps, {})["message"]
    assert "Top pages: none recorded." in text
    assert "among the first" not in text
    assert "Pageviews:" in text
    assert KEY not in text
    metric_calls = [url for url in calls if "/metrics/expanded" in url]
    assert len(metric_calls) == 1
    assert "offset=0" in metric_calls[0]
    assert "limit=500" in metric_calls[0]
