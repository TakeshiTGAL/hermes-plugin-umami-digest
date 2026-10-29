"""Skill and README mention there is no approval because the tool is read-only."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_skill_and_readme_say_no_approval():
    skill = (ROOT / "skills" / "umami-digest" / "SKILL.md").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "approval" in skill.lower()
    assert "does not ask for approval" in skill.lower() or "no approval" in skill.lower()
    assert "approval" in readme.lower()
    assert "umami_digest" in skill
    assert readme.splitlines()[2].startswith("Each morning")


def test_readme_first_sentence_and_hermes_seo_line():
    lines = [line for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert "pageviews" in lines[0].lower() or "pageviews" in lines[1].lower()
    joined = "\n".join(lines[:8])
    assert "hermes-seo" in joined
    assert "Matomo" in joined


def test_readme_mentions_env_names():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    for name in ("UMAMI_URL", "UMAMI_API_KEY", "UMAMI_WEBSITE_ID"):
        assert name in text
    assert "umami_…" not in text
    assert "sk-xxxxxxxx" not in text
