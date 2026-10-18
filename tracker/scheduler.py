"""
Background polling: the dashboard server's own loop, and the macOS LaunchAgent that keeps
polling when the server is not running. Both call jobs.tick(), which a file lock keeps
single-instance, and each job inside a tick only runs when it is due — so two pollers
never double the requests sent to NSE/BSE.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import threading
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from . import jobs
from .config import LAUNCHD_LABEL, ROOT, load_settings
from .util import IST, now_ist

PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LAUNCHD_LABEL}.plist"
SCRIPT = ROOT / "scripts" / "tracker_schedule.sh"


class ServerPoller:
    """Runs a tick as soon as the server starts, then every `schedule.server_every_min` minutes."""

    def __init__(self) -> None:
        self.thread: threading.Thread | None = None
        self.next_at: str | None = None
        self.running = False
        self._wake = threading.Event()
        self._stop = threading.Event()

    @property
    def enabled(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    def start(self) -> None:
        if self.enabled:
            return
        self.thread = threading.Thread(target=self._loop, name="tracker-poller", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def kick(self) -> None:
        """Run the next tick now instead of waiting for the interval."""
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.next_at = None
            self.running = True
            try:
                jobs.tick(push=True, source="server")
            except Exception:  # never let the loop die
                traceback.print_exc()
            finally:
                self.running = False
            every = max(1, int(load_settings()["schedule"]["server_every_min"]))
            self.next_at = (now_ist() + timedelta(minutes=every)).isoformat(timespec="seconds")
            self._wake.wait(every * 60)
            self._wake.clear()

    def state(self) -> dict:
        return {"enabled": self.enabled, "running": self.running, "next_at": self.next_at,
                "every_min": load_settings()["schedule"]["server_every_min"]}


poller = ServerPoller()


# ── LaunchAgent ─────────────────────────────────────────────────────────────

def launchd_status() -> dict:
    out: dict = {"label": LAUNCHD_LABEL, "installed": PLIST.exists(), "loaded": False,
                 "interval_min": None, "plist": str(PLIST)}
    if PLIST.exists():
        out["installed_at"] = datetime.fromtimestamp(PLIST.stat().st_mtime, IST).isoformat(timespec="seconds")
        try:
            data = plistlib.loads(PLIST.read_bytes())
            out["interval_min"] = round(int(data.get("StartInterval", 0)) / 60, 1) or None
        except Exception:
            pass
    try:
        r = subprocess.run(["launchctl", "print", f"gui/{os.getuid()}/{LAUNCHD_LABEL}"],
                           capture_output=True, text=True, timeout=5)
    except (subprocess.SubprocessError, FileNotFoundError):
        return out
    if r.returncode == 0:
        out["loaded"] = True
        for line in r.stdout.splitlines():
            line = line.strip()
            for key in ("state", "runs", "last exit code"):
                if line.startswith(key + " ="):
                    out[key.replace(" ", "_")] = line.split("=", 1)[1].strip()
    return out


def launchd(action: str) -> dict:
    if action not in ("install", "uninstall"):
        raise ValueError(action)
    args = ["/bin/bash", str(SCRIPT), action]
    if action == "install":
        args.append(str(int(load_settings()["schedule"]["launchd_every_min"]) * 60))
    r = subprocess.run(args, capture_output=True, text=True, timeout=30, cwd=ROOT)
    return {"ok": r.returncode == 0, "output": (r.stdout + r.stderr).strip(), **launchd_status()}
