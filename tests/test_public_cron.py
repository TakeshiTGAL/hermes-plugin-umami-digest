"""The public hermes cron command, not the cron job store."""
from __future__ import annotations

from public_cron import Cmd, Store, parse_created_id, parse_listed_jobs

LISTING = """
  5e66b0eee729 [active]
    Name:      umami-digest
    Schedule:  0 8 * * *
    Repeat:    ∞
    Deliver:   local

  digest999999 [active]
    Name:      daily-digest
    Schedule:  every 1d
    Deliver:   telegram
"""


def test_list_parser_matches_hermes_0216_and_0222():
    jobs = parse_listed_jobs(LISTING)
    assert jobs[0] == {
        "id": "5e66b0eee729",
        "name": "umami-digest",
        "schedule": "0 8 * * *",
        "deliver": "local",
    }
    assert jobs[1]["id"] == "digest999999"
    assert parse_created_id("Created job: 5e66b0eee729\n  Name: umami-digest\n") == "5e66b0eee729"


def test_store_create_edit_remove_uses_the_public_argv():
    calls: list[list[str]] = []

    def run(args: list[str]) -> Cmd:
        calls.append(args)
        if args[0] == "create":
            return Cmd(0, "Created job: abcdef123456\n  Name: umami-digest\n", "")
        if args[0] == "list":
            return Cmd(0, LISTING.replace("5e66b0eee729", "abcdef123456"), "")
        if args[0] == "edit":
            return Cmd(0, "Updated job: abcdef123456\n", "")
        if args[0] == "remove":
            return Cmd(0, "Removed job: umami-digest (abcdef123456)\n", "")
        return Cmd(1, "", "unexpected")

    store = Store(run)
    created = store.create_job(
        schedule="0 8 * * *",
        name="umami-digest",
        deliver="signal",
        script="umami-digest.py",
        no_agent=True,
    )
    assert created["id"] == "abcdef123456"
    assert calls[0][:2] == ["create", "0 8 * * *"]
    assert "--no-agent" in calls[0]
    assert "--script" in calls[0]
    assert "interpreter" not in calls[0]
    assert store.update_job("abcdef123456", {"schedule": "0 7 * * *", "deliver": "local"})["id"] == "abcdef123456"
    assert store.remove_job("abcdef123456") is True
    assert calls[-1] == ["remove", "abcdef123456"]
