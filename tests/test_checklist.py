"""HTTP error kinds stay distinct, and unread is never written as zero."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from service import Deps, umami_digest
from tests.test_digest import KEY, Router, secrets, stats

ROOT = Path(__file__).resolve().parents[1]


def test_http_error_types_are_not_collapsed():
    deps = Deps()
    deps.secrets = secrets()
    deps.now = lambda: datetime(2026, 10, 29, tzinfo=timezone.utc)
    router = Router()
    router.by_path = {
        "/api/websites/11111111-1111-1111-1111-111111111111/stats": (401, {"error": {"message": "no"}}),
        "/api/websites/11111111-1111-1111-1111-111111111111/metrics/expanded": (401, {"error": {"message": "no"}}),
    }
    deps.transport = router
    a = umami_digest(deps, {})
    router.by_path = {
        "/api/websites/11111111-1111-1111-1111-111111111111/stats": (404, {"error": {"message": "gone"}}),
        "/api/websites/11111111-1111-1111-1111-111111111111/metrics/expanded": (404, {"error": {"message": "gone"}}),
    }
    b = umami_digest(deps, {})
    router.by_path = {
        "/api/websites/11111111-1111-1111-1111-111111111111/stats": (503, {"error": {"message": "busy"}}),
        "/api/websites/11111111-1111-1111-1111-111111111111/metrics/expanded": (503, {"error": {"message": "busy"}}),
    }
    c = umami_digest(deps, {})
    assert "401" in a["message"]
    assert "404" in b["message"]
    assert "503" in c["message"]
    assert a["message"] != b["message"] != c["message"]
    assert KEY not in a["message"] + b["message"] + c["message"]


def test_200_error_field_is_not_zero_traffic():
    router = Router()
    router.stats_body = {"error": "nope", "pageviews": 0}
    deps = Deps()
    deps.secrets = secrets()
    deps.transport = router
    deps.now = lambda: datetime(2026, 10, 29, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert out["ok"] is False
    assert "error" in out["message"].lower() or "failure" in out["message"].lower()
    assert "0 vs 0" not in out["message"]


def test_readme_schedule_matches_code():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    yaml = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
    assert "UMAMI_URL" in text and "UMAMI_URL" not in yaml.split("description")[0]
    assert "0 8 * * *" in text
    assert "umami_digest" in yaml
    assert "provides_hooks" not in yaml


def test_no_japanese_in_public_files():
    import re
    jp = re.compile(r"[\u3040-\u30ff\u4e00-\u9faf]")
    for path in [ROOT / "README.md", ROOT / "plugin.yaml", ROOT / "CHANGELOG.md", ROOT / "cli.py"]:
        text = path.read_text(encoding="utf-8")
        assert not jp.search(text), path.name
