#!/usr/bin/env python3
"""Drive the Counter fixture app for verification."""

import json
import os
import shutil
import signal
import socket
import subprocess  # nosec B404 -- the CLI starts the app it launched
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("VERIFY_COUNTER_ROOT") or Path(__file__).resolve().parents[4])
STATE = Path(os.environ.get("VERIFY_COUNTER_STATE") or Path(tempfile.gettempdir()) / f"verify-counter-{os.getuid()}")
PID_FILE = STATE / "pid"
PORT_FILE = STATE / "port"
LOG_FILE = STATE / "server.log"
TASK = "mise run verify:counter"
INVENTORY = [
    {"name": "commands", "destructive": False, "description": "list the subcommands"},
    {"name": "doctor", "destructive": False, "description": "check whether the app is running and healthy"},
    {"name": "count", "destructive": False, "description": "read the current count"},
    {"name": "launch", "destructive": True, "description": "start the app on a free loopback port"},
    {"name": "increment", "destructive": True, "description": "add one to the count"},
    {"name": "reset", "destructive": True, "description": "set the count back to zero"},
    {"name": "stop", "destructive": True, "description": "stop the app this CLI started"},
]


def emit(payload, code=0):
    print(json.dumps(payload, sort_keys=True))
    return code


def fail(message, alternative, code=1):
    return emit({"ok": False, "error": message, "alternative": alternative}, code)


def dry_run(action):
    return emit({"ok": True, "dry_run": True, "action": action})


def port():
    if not PORT_FILE.is_file():
        return None
    value = PORT_FILE.read_text().strip()
    return int(value) if value.isdigit() else None


def pid():
    if not PID_FILE.is_file():
        return None
    value = PID_FILE.read_text().strip()
    return int(value) if value.isdigit() else None


def request(method, path, timeout=5):
    current = port()
    if current is None:
        raise ConnectionError("no port recorded")
    data = b"{}" if method == "POST" else None
    url = f"http://127.0.0.1:{current}{path}"
    with urllib.request.urlopen(  # nosec B310 -- loopback URL recorded by this run
        urllib.request.Request(url, data=data, method=method), timeout=timeout
    ) as response:
        return json.loads(response.read())


def healthy():
    try:
        return request("GET", "/health", timeout=1).get("ok") is True
    except (OSError, ValueError):
        return False


def alive():
    current = pid()
    if current is None:
        return False
    try:
        os.kill(current, 0)
    except OSError:
        return False
    return healthy()


def launch(dry):
    if dry:
        return dry_run("launch")
    if alive():
        return emit({"ok": True, "already_running": True, "url": f"http://127.0.0.1:{port()}"})
    STATE.mkdir(parents=True, exist_ok=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        chosen = probe.getsockname()[1]
    log = LOG_FILE.open("ab")
    # nosemgrep
    process = subprocess.Popen(  # nosec B603 -- this checkout's app.py and interpreter
        [sys.executable, str(ROOT / "app.py"), "--port", str(chosen)],  # nosec B603; nosemgrep
        stdout=log,
        stderr=log,
    )
    log.close()
    PORT_FILE.write_text(str(chosen))
    PID_FILE.write_text(str(process.pid))
    for _ in range(50):
        if healthy():
            return emit({"ok": True, "pid": process.pid, "url": f"http://127.0.0.1:{chosen}"})
        time.sleep(0.1)
    stop(False)
    return fail("the app did not become healthy", f"read {LOG_FILE}, then run `{TASK} launch` again")


def stop(dry):
    if dry:
        return dry_run("stop")
    current = pid()
    if current is None:
        return emit({"ok": True, "stopped": False})
    try:
        os.kill(current, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    for _ in range(30):
        try:
            os.kill(current, 0)
        except OSError:
            break
        time.sleep(0.1)
    shutil.rmtree(STATE, ignore_errors=True)
    return emit({"ok": True, "stopped": True})


def count(dry):
    if dry:
        return dry_run("count")
    if not healthy():
        return fail("the app is not running", f"{TASK} launch")
    return emit({"ok": True, **request("GET", "/count")})


def mutate(action, path, dry):
    if dry:
        return dry_run(action)
    if not healthy():
        return fail("the app is not running", f"{TASK} launch")
    return emit({"ok": True, **request("POST", path)})


def main(argv):
    if not argv:
        return fail("no subcommand given", f"{TASK} commands")
    command, *args = argv
    dry = "--dry-run" in args
    extra = [argument for argument in args if argument != "--dry-run"]
    if extra:
        return fail(f"unexpected argument {extra[0]!r}", f"{TASK} {command} --dry-run")
    handlers = {
        "commands": lambda: emit({"ok": True, "commands": INVENTORY}),
        "doctor": lambda: emit({"ok": True, "running": True, "url": f"http://127.0.0.1:{port()}"})
        if healthy()
        else fail("the app is not running", f"{TASK} launch"),
        "launch": lambda: launch(dry),
        "stop": lambda: stop(dry),
        "count": lambda: count(dry),
        "increment": lambda: mutate("increment", "/increment", dry),
        "reset": lambda: mutate("reset", "/reset", dry),
    }
    handler = handlers.get(command)
    if handler is None:
        return fail(f"unknown subcommand {command!r}", f"{TASK} commands")
    return handler()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
