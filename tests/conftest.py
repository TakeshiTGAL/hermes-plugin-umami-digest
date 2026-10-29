from __future__ import annotations

import os
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import hermes_constants  # noqa: F401
except ImportError:
    stub = types.ModuleType("hermes_constants")

    def get_hermes_home():
        env = os.environ.get("HERMES_HOME")
        if env:
            return Path(env)
        home = os.environ.get("HOME") or "/tmp"
        return Path(home) / ".hermes"

    stub.get_hermes_home = get_hermes_home
    sys.modules["hermes_constants"] = stub

try:
    import agent.file_safety  # noqa: F401
except ImportError:
    pkg = sys.modules.get("agent") or types.ModuleType("agent")
    sys.modules["agent"] = pkg
    safety = types.ModuleType("agent.file_safety")

    def get_write_denied_error(path, *, verb="Write", entry=False):  # noqa: ARG001
        return None

    safety.get_write_denied_error = get_write_denied_error
    sys.modules["agent.file_safety"] = safety
    pkg.file_safety = safety


def network_blocked() -> bool:
    import socket

    try:
        sock = socket.socket()
        try:
            sock.connect(("127.0.0.1", 1))
        finally:
            sock.close()
    except RuntimeError as exc:
        return "network blocked" in str(exc)
    except OSError:
        return False
    return False
