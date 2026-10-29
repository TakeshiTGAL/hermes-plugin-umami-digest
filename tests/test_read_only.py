"""The digest tool does not write."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from service import Deps, umami_digest
from tests.test_digest import Router, secrets


def _fingerprint(root: Path) -> str:
    parts = []
    if not root.exists():
        return ""
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            parts.append(path.relative_to(root).as_posix())
            parts.append(hashlib.sha256(path.read_bytes()).hexdigest())
    return "\n".join(parts)


def test_tool_does_not_write(tmp_path):
    before = _fingerprint(tmp_path)
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.secrets = secrets()
    deps.transport = Router()
    deps.now = lambda: datetime(2026, 10, 29, 8, 0, tzinfo=timezone.utc)
    out = umami_digest(deps, {})
    assert out["ok"] is True
    assert _fingerprint(tmp_path) == before


def test_no_send_or_login_in_product():
    root = Path(__file__).resolve().parents[1]
    for path in root.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "/api/send" not in text
        assert "/api/auth/login" not in text
