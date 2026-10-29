"""Schedule one no-agent morning digest and return the same letter from the tool."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

if __package__:
    from . import client, digest, gates
    from .digest import DigestError, live_secret
    from .gates import ConfigError
    from .public_cron import CronUnread, Store, resolved_hermes_home
    from .safe_write import WriteRefused, write_bytes
else:
    import client
    import digest
    import gates
    from digest import DigestError, live_secret
    from gates import ConfigError
    from public_cron import CronUnread, Store, resolved_hermes_home
    from safe_write import WriteRefused, write_bytes

TOOLSET = "umami_digest"
JOB_NAME = "umami-digest"
STATE_NAME = "state.json"
SCRIPT_NAME = "umami-digest.py"
DEFAULT_SCHEDULE = "0 8 * * *"
DEFAULT_WORDS = "every day at 08:00"
SELF_TARGETS = frozenset({"origin", "local", "all"})
_CRON_FIELD = re.compile(r"^[A-Za-z0-9*@,/\-]+$")
_EVERY = re.compile(
    r"^(?:every\s+)?(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)?$",
    re.IGNORECASE,
)
WATCH_SCRIPT = '''"""umami-digest. Hermes delivers stdout and sends nothing when it is empty."""
import sys
from hermes_constants import get_hermes_home

plugin = get_hermes_home() / "plugins" / "umami-digest"
if not (plugin / "service.py").is_file():
    print(
        "umami-digest is not installed under plugins/umami-digest, so the digest did not run.",
        file=sys.stderr,
    )
    raise SystemExit(1)
sys.path.insert(0, str(plugin))
import service

text = service.digest_stdout()
if text:
    sys.stdout.write(text)
    if not text.endswith("\\n"):
        sys.stdout.write("\\n")
'''
TOOL_NOTE = (
    " Each run costs 0 tokens. The unmanned run holds no tools. "
    "The job runs a script and does not call a model. Empty script output sends nothing. "
    "hermes cron create cannot set a per-job tool list, which does not matter here because this job holds no tools. "
    "interpreter is not passed. "
    "Running schedule again updates the interval and deliver target and keeps the script job."
)


class Deps:
    def __init__(self) -> None:
        self.transport: client.Transport | None = None
        self.now: Callable[[], datetime] | None = None
        self.cron: Any | None = None
        self.data_dir: Path | None = None
        self.secrets: Callable[[str], str | None] | None = None


def _now(deps: Deps) -> datetime:
    if deps.now is not None:
        return deps.now()
    return datetime.now(timezone.utc)


def _reader(deps: Deps) -> Callable[[str], str | None]:
    if deps.secrets is not None:
        return deps.secrets
    return live_secret


def _clock_dir(deps: Deps) -> Path:
    if deps.data_dir is not None:
        path = deps.data_dir
    else:
        path = gates.plugin_data_dir()
    if not isinstance(path, Path):
        path = Path(path)
    return path


def state_path(deps: Deps) -> Path:
    return _clock_dir(deps) / STATE_NAME


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _fail(message: str, *, error_type: str, next_step: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "message": message, "error_type": error_type}
    out["next_step"] = next_step or "Nothing was stored."
    return out


def load_state(deps: Deps) -> tuple[dict[str, Any] | None, str | None]:
    try:
        path = state_path(deps)
    except Exception:
        return None, "unreadable"
    try:
        if not path.is_file():
            return None, "missing"
        raw = path.read_bytes()
    except Exception:
        return None, "unreadable"
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None, "corrupt"
    if not isinstance(data, dict):
        return None, "corrupt"
    return data, None


def save_state(deps: Deps, data: dict[str, Any]) -> str | None:
    try:
        path = state_path(deps)
        directory = path.parent
    except Exception:
        return "The data folder could not be prepared, so the file was not saved."
    denied = gates.write_guard_error(str(path))
    if denied:
        return denied
    payload = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    try:
        write_bytes(directory, STATE_NAME, payload, create=True)
    except WriteRefused as exc:
        return f"The file was not saved ({exc.kind})."
    except Exception:
        return "The file could not be replaced, so the saved job was left as it was."
    return None


def watch_script_path() -> Path | None:
    try:
        home = resolved_hermes_home()
    except (OSError, RuntimeError, ImportError):
        return None
    if not home:
        return None
    return Path(home) / "scripts" / SCRIPT_NAME


def write_watch_script() -> str | None:
    path = watch_script_path()
    if path is None:
        return "The Hermes home could not be resolved, so the watch script was not written."
    try:
        write_bytes(path.parent, SCRIPT_NAME, WATCH_SCRIPT.encode("utf-8"), create=True)
    except WriteRefused as exc:
        return f"The watch script could not be written ({exc.kind}), so the job was not changed."
    except OSError:
        return "The watch script could not be written, so the job was not changed."
    return None


def remove_watch_script() -> str | None:
    path = watch_script_path()
    if path is None:
        return "The Hermes home could not be resolved, so the watch script was not removed."
    if not path.is_file():
        return None
    try:
        path.unlink()
    except OSError:
        return "The watch script could not be removed."
    return None


def _cron_module(deps: Deps) -> Any:
    if deps.cron is not None:
        return deps.cron
    return Store()


def parse_schedule(text: str | None) -> tuple[str, str] | str:
    if text is None or str(text).strip() == "":
        return DEFAULT_SCHEDULE, DEFAULT_WORDS
    raw = str(text).strip()
    if raw == DEFAULT_WORDS or raw.lower() in {"daily", "every morning"}:
        return DEFAULT_SCHEDULE, DEFAULT_WORDS
    if raw.lower().startswith("cron "):
        raw = raw[5:].strip()
    parts = raw.split()
    if len(parts) == 5 and all(_CRON_FIELD.fullmatch(part) for part in parts):
        return raw, f"cron {raw}"
    lowered = re.sub(r"^every\s+", "", raw.strip(), flags=re.IGNORECASE)
    if lowered.lower() in {"day", "1 day", "a day", "daily"}:
        return DEFAULT_SCHEDULE, DEFAULT_WORDS
    match = _EVERY.fullmatch(lowered)
    if not match:
        return (
            "The schedule was not understood. Use 0 8 * * * or every 24h. The job was not changed."
        )
    count = int(match.group(1))
    unit = (match.group(2) or "h").lower()
    if unit.startswith("d"):
        if count != 1:
            return "Only a daily digest is scheduled. Use 0 8 * * * or every 24h. The job was not changed."
        return "every 24h", "every 24 hours"
    if unit.startswith("h"):
        if count < 24 or count % 24 != 0:
            return "Only a daily digest is scheduled. Use 0 8 * * * or every 24h. The job was not changed."
        hours = count
        return f"every {hours}h", f"every {hours} hours"
    return "Only a daily digest is scheduled. Use 0 8 * * * or every 24h. The job was not changed."


def _job_by_id(mod: Any, job_id: str) -> dict[str, Any] | None:
    getter = getattr(mod, "get_job", None)
    if not callable(getter):
        return None
    try:
        job = getter(job_id)
    except CronUnread:
        raise
    except Exception:
        return None
    return job if isinstance(job, dict) else None


def _named_job_ids(mod: Any) -> list[str] | None:
    loader = getattr(mod, "load_jobs", None)
    if not callable(loader):
        return None
    try:
        jobs = loader()
    except CronUnread:
        raise
    except Exception:
        return None
    if not isinstance(jobs, list):
        return None
    return [
        str(job.get("id"))
        for job in jobs
        if isinstance(job, dict) and job.get("name") == JOB_NAME and job.get("id")
    ]


def _create_job(mod: Any, schedule: str, deliver: str) -> tuple[str | None, str | None]:
    create = getattr(mod, "create_job", None)
    if not callable(create):
        return None, "The cron job was not created because hermes cron could not be run."
    try:
        created = create(
            schedule=schedule,
            name=JOB_NAME,
            deliver=deliver,
            script=SCRIPT_NAME,
            no_agent=True,
        )
    except Exception as exc:
        return None, f"The cron job could not be created. {exc}"
    job_id = created.get("id") if isinstance(created, dict) else None
    if not job_id:
        return None, "The cron job did not return an id, so nothing was saved."
    return str(job_id), None


def _update_job(mod: Any, job_id: str, schedule: str, deliver: str) -> str | None:
    update = getattr(mod, "update_job", None)
    if not callable(update):
        return "The existing job could not be updated."
    try:
        result = update(
            job_id,
            {
                "schedule": schedule,
                "deliver": deliver,
                "script": SCRIPT_NAME,
                "no_agent": True,
            },
        )
    except Exception as exc:
        return f"The existing job could not be updated. {exc}"
    if result is None:
        return "The existing job could not be updated."
    return None


def _remove_job(mod: Any, job_id: str) -> bool:
    remove = getattr(mod, "remove_job", None)
    if not callable(remove):
        return False
    try:
        return bool(remove(job_id))
    except Exception:
        return False


def _cron_help(job_id: str | None) -> str:
    lines = gates.hermes_cron_lines(job_id=job_id)
    return "Hermes commands for this job, for you to run:\n" + "\n".join(lines)


def _deliver_caution(canon: str) -> str:
    notes = ""
    platforms = []
    for part in canon.split(","):
        head = part.split(":", 1)[0]
        if head in SELF_TARGETS or head == "bot-chat":
            continue
        platforms.append(head)
    if platforms:
        notes += (
            f" The name {platforms[0]} is a delivery target, not proof that this machine has it set up. "
            "On Hermes 0.21.6 and current main, a job whose target is not set up does not run: "
            "Hermes records it as blocked_config, which hermes cron runs shows, "
            "and hermes cron doctor reports it as last run failed."
        )
    if any(part == "bot-chat" or part.startswith("bot-chat:") for part in canon.split(",")):
        notes += (
            " bot-chat puts each report into another model turn that has the profile's tools."
        )
    return notes


def _chosen_every(every: str | None, state: dict[str, Any] | None) -> str | None:
    if every is not None and str(every).strip() != "":
        return every
    if isinstance(state, dict):
        saved_s = state.get("schedule")
        if isinstance(saved_s, str) and saved_s.strip():
            return saved_s
        saved = state.get("interval_words")
        if isinstance(saved, str) and saved.strip():
            return saved
    return None


def schedule(deps: Deps, deliver: str, every: str | None) -> tuple[int, str]:
    state, err = load_state(deps)
    parsed = parse_schedule(_chosen_every(every, state if err is None else None))
    if isinstance(parsed, str):
        return 1, parsed
    schedule_text, words = parsed
    canon = gates.canonical_deliver(deliver)
    previous = state.get("deliver") if isinstance(state, dict) else None
    saved_job = str(state.get("job_id") or "").strip() if isinstance(state, dict) else ""
    if canon is None:
        ident = saved_job or "<id>"
        hint = f" Set it on the job with hermes cron edit {ident} --deliver <target>."
        if previous:
            return 1, (
                f"Delivery target was rejected. The previous target remains {previous}. "
                f"The job was not changed.{hint}"
            )
        return 1, "Delivery target was rejected. No job was created." + hint
    if err == "corrupt":
        return 1, "The saved file could not be read, so it was not replaced. The job was not changed."
    if err == "unreadable":
        return 1, "The data folder could not be read, so nothing was requested."
    try:
        mod = _cron_module(deps)
    except Exception:
        return 1, "hermes cron could not be run, so the job was not saved."
    old = state if isinstance(state, dict) else None
    old_id = str(old.get("job_id") or "") if old else ""
    created_new = False
    named: list[str] = []
    try:
        found = _job_by_id(mod, old_id) if old_id else None
        job_id = old_id if found else ""
        if not job_id:
            named = _named_job_ids(mod) or []
    except CronUnread as exc:
        return 1, (
            f"hermes cron list could not be read ({exc.detail}), so the job was not saved. "
            "The job was not changed."
        )
    if not job_id and len(named) > 1:
        return 1, "More than one umami-digest job already exists, so another was not created. Ids: " + ", ".join(named)
    script_err = write_watch_script()
    if script_err:
        return 1, script_err + " The job was not changed."
    if not job_id:
        if len(named) == 1:
            job_id = named[0]
        else:
            job_id, create_err = _create_job(mod, schedule_text, canon)
            if create_err or not job_id:
                remove_watch_script()
                return 1, create_err or "The cron job was not created."
            created_new = True
    if not created_new:
        update_err = _update_job(mod, job_id, schedule_text, canon)
        if update_err:
            return 1, update_err + " The saved file was not changed."
    new_state = {
        "job_id": job_id,
        "deliver": canon,
        "schedule": schedule_text,
        "interval_words": words,
    }
    save_err = save_state(deps, new_state)
    if save_err:
        if created_new:
            removed = _remove_job(mod, job_id)
            if not removed:
                return 1, save_err + f" The leftover job id is {job_id}."
            remove_watch_script()
            return 1, save_err + " The new job was removed."
        return 1, save_err + " The saved file was not changed."
    replaced = ""
    if previous and previous != canon:
        replaced = f" Replaced deliver target {previous} with {canon}."
    text = (
        f"Scheduled umami-digest, {words}, deliver {canon}, job {job_id}."
        f"{replaced}{_deliver_caution(canon)}{TOOL_NOTE}\n"
        f"{_cron_help(job_id)}"
    )
    return 0, text


def unschedule(deps: Deps) -> tuple[int, str]:
    state, err = load_state(deps)
    if err == "corrupt":
        return 1, "The saved file could not be read, so it was not replaced. The job was not changed."
    if err == "unreadable":
        return 1, "The data folder could not be read."
    if not isinstance(state, dict) or not state.get("job_id"):
        script = watch_script_path()
        if script is not None and script.is_file():
            script_err = remove_watch_script()
            if script_err:
                return 1, script_err
        return 0, "No cron job is stored. The saved file was left in place."
    try:
        mod = _cron_module(deps)
    except Exception:
        return 1, "hermes cron could not be run, so the job was not removed. The saved file was left in place."
    job_id = str(state["job_id"])
    try:
        found = _job_by_id(mod, job_id)
    except CronUnread as exc:
        return 1, (
            f"hermes cron list could not be read ({exc.detail}), so the job was not removed. "
            "The saved file was left in place."
        )
    if found is None:
        state["job_id"] = None
        save_err = save_state(deps, state)
        if save_err:
            return 1, save_err + f" Job {job_id} is already gone. The saved id was left in place."
        script_err = remove_watch_script()
        if script_err:
            return 1, script_err + f" Job {job_id} is already gone. The saved id was cleared."
        return 0, f"Job {job_id} is already gone. The watch script is gone. The saved id was cleared."
    removed = _remove_job(mod, job_id)
    if not removed:
        try:
            still = _job_by_id(mod, job_id)
        except CronUnread as exc:
            return 1, (
                f"hermes cron list could not be read ({exc.detail}). "
                "The saved file was left in place."
            )
        if still is None:
            state["job_id"] = None
            save_err = save_state(deps, state)
            if save_err:
                return 1, save_err + f" Job {job_id} is already gone. The saved id was left in place."
            script_err = remove_watch_script()
            if script_err:
                return 1, script_err + f" Job {job_id} is already gone. The saved id was cleared."
            return 0, f"Job {job_id} is already gone. The watch script is gone. The saved id was cleared."
        return 1, f"The job {job_id} was not removed. The saved file was left in place."
    script_err = remove_watch_script()
    state["job_id"] = None
    save_err = save_state(deps, state)
    if script_err:
        extra = f" The job {job_id} was removed."
        if save_err:
            return 1, script_err + extra + " " + save_err
        return 1, script_err + extra
    if save_err:
        return 1, save_err + f" The job {job_id} was removed. The watch script is gone."
    return 0, (
        f"Removed job {job_id}. The watch script was removed. The saved file remains. "
        "Run unschedule before hermes plugins remove. "
        "If the plugin is already gone and the job is still listed, "
        "remove that leftover job with hermes cron remove and its id."
    )


def status(deps: Deps) -> tuple[int, str]:
    state, err = load_state(deps)
    if err == "corrupt":
        return 1, "The saved file could not be read, so it was not replaced."
    if err == "unreadable":
        return 1, "The data folder could not be read."
    if not isinstance(state, dict) or not state.get("job_id"):
        return 0, "No umami-digest job is stored."
    job_id = str(state.get("job_id") or "")
    words = str(state.get("interval_words") or state.get("schedule") or "")
    deliver = str(state.get("deliver") or "")
    try:
        mod = _cron_module(deps)
        found = _job_by_id(mod, job_id)
    except CronUnread as exc:
        return 1, f"hermes cron list could not be read ({exc.detail}). The saved file was left in place."
    except Exception:
        return 1, "hermes cron could not be run, so status did not check the job list."
    if found is None:
        return 0, (
            f"Saved job {job_id} is gone. {words} deliver {deliver}. "
            "Run hermes umami-digest schedule to create it again."
        )
    return 0, (
        f"Job {job_id} is listed. {words}, deliver {deliver}.\n"
        f"{_cron_help(job_id)}"
    )


def _letter(deps: Deps) -> str:
    return digest.build_letter(_reader(deps), _now(deps), deps.transport)


def umami_digest(deps: Deps, args: dict[str, Any] | None = None, **_kwargs: Any) -> dict[str, Any]:
    if args:
        return _fail(
            "umami_digest takes no arguments, so call it with an empty object.",
            error_type="bad_args",
            next_step="Call umami_digest with an empty argument object.",
        )
    try:
        letter = _letter(deps)
    except ConfigError as exc:
        return _fail(exc.message, error_type=exc.code)
    except DigestError as exc:
        return _fail(exc.message, error_type=exc.code)
    except client.ClientError as exc:
        return _fail(exc.message, error_type=exc.code)
    return {"ok": True, "message": letter}


def digest_stdout(deps: Deps | None = None) -> str:
    if deps is None:
        deps = Deps()
    try:
        letter = _letter(deps)
    except ConfigError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    except DigestError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    except client.ClientError as exc:
        print(exc.message, file=sys.stderr)
        raise SystemExit(1) from exc
    return letter


def tool_json(deps: Deps, args: dict[str, Any] | None = None) -> str:
    return _json(umami_digest(deps, args))
