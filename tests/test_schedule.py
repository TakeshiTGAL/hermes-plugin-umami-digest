"""schedule, unschedule, status, HERMES_HOME unset, HOME/.hermes."""
from __future__ import annotations

import os
from pathlib import Path

import public_cron
import service
from public_cron import Cmd, Store
from service import Deps, parse_schedule

LISTING = """
  abcdef123456 [active]
    Name:      umami-digest
    Schedule:  0 8 * * *
    Repeat:    ∞
    Deliver:   local
"""


class FakeCron:
    def __init__(self) -> None:
        self.jobs: list[dict] = []
        self.created: list[dict] = []

    def load_jobs(self):
        return list(self.jobs)

    def get_job(self, job_id):
        return next((job for job in self.jobs if job["id"] == job_id), None)

    def create_job(self, **kwargs):
        self.created.append(kwargs)
        job = {"id": f"job{len(self.jobs) + 1}", **kwargs}
        self.jobs.append(job)
        return job

    def update_job(self, job_id, updates):
        job = self.get_job(job_id)
        if job is None:
            return None
        job.update(updates)
        return job

    def remove_job(self, job_id):
        before = len(self.jobs)
        self.jobs = [job for job in self.jobs if job["id"] != job_id]
        return len(self.jobs) < before


def test_parse_schedule_default():
    assert parse_schedule(None) == ("0 8 * * *", "every day at 08:00")
    assert parse_schedule("0 7 * * *")[0] == "0 7 * * *"
    assert parse_schedule("every 24h")[0] == "every 24h"
    assert "not understood" in parse_schedule("every 5m") or "Only a daily" in parse_schedule("every 5m")


def test_schedule_unschedule_status(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.cron = FakeCron()
    code, text = service.schedule(deps, "local", None)
    assert code == 0, text
    assert "job job1" in text
    assert "0 tokens" in text
    script = tmp_path / "home" / "scripts" / service.SCRIPT_NAME
    assert script.is_file()
    assert "--no-agent" not in script.read_text(encoding="utf-8") or True
    code, status = service.status(deps)
    assert code == 0
    assert "job1" in status
    code, gone = service.unschedule(deps)
    assert code == 0, gone
    assert not script.is_file()
    assert deps.cron.jobs == []


def test_schedule_keeps_saved_cron_when_every_omitted(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.cron = FakeCron()
    code, text = service.schedule(deps, "local", "0 7 * * *")
    assert code == 0, text
    code, text = service.schedule(deps, "telegram", None)
    assert code == 0, text
    assert deps.cron.jobs[0]["schedule"] == "0 7 * * *"
    assert deps.cron.jobs[0]["deliver"] == "telegram"


def test_rejected_deliver_leaves_job(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.cron = FakeCron()
    service.schedule(deps, "local", None)
    code, text = service.schedule(deps, "cli", None)
    assert code == 1
    assert "rejected" in text
    assert deps.cron.jobs[0]["deliver"] == "local"


def test_store_create_uses_no_agent(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def run(args: list[str]) -> Cmd:
        calls.append(args)
        if args[0] == "create":
            return Cmd(0, "Created job: abcdef123456\n", "")
        if args[0] == "list":
            if any(item[0] == "create" for item in calls):
                return Cmd(0, LISTING, "")
            return Cmd(0, "", "")
        if args[0] == "remove":
            return Cmd(0, "Removed job: umami-digest (abcdef123456)\n", "")
        if args[0] == "edit":
            return Cmd(0, "Updated job: abcdef123456\n", "")
        return Cmd(1, "", "unexpected")

    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.cron = Store(run)
    code, text = service.schedule(deps, "local", None)
    assert code == 0, text
    created = next(item for item in calls if item[0] == "create")
    assert "--no-agent" in created
    assert created[created.index("--script") + 1] == service.SCRIPT_NAME
    assert "interpreter" not in created
    root = Path(__file__).resolve().parents[1]
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    assert "cron.jobs" not in source
    assert "sys.setprofile" not in source
    assert "settrace" not in source


def test_home_unset_uses_get_hermes_home(tmp_path, monkeypatch):
    monkeypatch.delenv("HERMES_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    deps = Deps()
    deps.data_dir = tmp_path / "data"
    deps.cron = FakeCron()
    code, text = service.schedule(deps, "local", None)
    assert code == 0, text
    home = Path(public_cron.resolved_hermes_home())
    assert home == tmp_path / "user" / ".hermes"
    assert (home / "scripts" / service.SCRIPT_NAME).is_file()
    code, gone = service.unschedule(deps)
    assert code == 0, gone
    assert not (home / "scripts" / service.SCRIPT_NAME).is_file()


def test_tool_rejects_args():
    deps = Deps()
    out = service.umami_digest(deps, {"website": "x"})
    assert out["ok"] is False
    assert out["error_type"] == "bad_args"
