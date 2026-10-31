"""Reloading the running app, and noticing when its code has been updated.

A reload starts a fresh copy of Aurum LIMS and closes this one. The new copy
picks up the latest code (and runs any database upgrade), and continues the
same user's session on the same page using a single-use code from
``auth.create_resume_token`` passed through the environment.

The two copies must never have the database open at the same time: when the
old copy closed its last connection while the new one was starting, SQLite
could remove the write-ahead log the new copy was already writing to, so its
saves stayed invisible on disk until it closed (and would have been lost in a
crash). So the old copy closes its database before starting the new one, and
the new copy waits until the old process has exited before opening it.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

RESUME_ENV = "AURUM_LIMS_RESUME_TOKEN"
PAGE_ENV = "AURUM_LIMS_RESUME_PAGE"
PARENT_ENV = "AURUM_LIMS_RESUME_PARENT"  # process id of the copy being replaced
log = logging.getLogger("aurum_lims")
PACKAGE_DIR = Path(__file__).resolve().parent.parent
WATCHED_SUFFIXES = {".py", ".qss"}


def restart_command() -> list[str]:
    """How to start the app again the same way it was started."""
    if getattr(sys, "frozen", False):  # the stand-alone .exe
        return [sys.executable]
    return [sys.executable, "-m", "aurum_lims"]


def start_new_instance(token: str, page: str | None) -> subprocess.Popen:
    env = os.environ.copy()
    env[RESUME_ENV] = token
    env[PARENT_ENV] = str(os.getpid())
    if page:
        env[PAGE_ENV] = page
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(restart_command(), env=env, cwd=os.getcwd(), close_fds=True, creationflags=flags)


def _process_running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102  # WAIT_TIMEOUT: still running
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def wait_for_previous_instance(timeout: float = 20.0) -> bool:
    """When started by Reload, wait until the copy being replaced has exited (so only one copy ever has
    the database open). Returns False if it was still running after ``timeout`` seconds."""
    value = os.environ.pop(PARENT_ENV, None)
    if not value or not value.isdigit():
        return True
    pid, deadline = int(value), time.monotonic() + timeout
    while _process_running(pid):
        if time.monotonic() > deadline:
            log.warning("The previous copy (process %s) was still running after %.0f s", pid, timeout)
            return False
        time.sleep(0.05)
    return True


def take_resume_request() -> tuple[str | None, str | None]:
    """The reload code and page handed over by the previous copy (removed from the
    environment so they are never passed on again)."""
    return os.environ.pop(RESUME_ENV, None), os.environ.pop(PAGE_ENV, None)


def code_signature(folder: Path = PACKAGE_DIR) -> float:
    """Latest modification time of the app's code; changes when the code is updated."""
    latest = 0.0
    for path in folder.rglob("*"):
        if path.suffix in WATCHED_SUFFIXES and "__pycache__" not in path.parts:
            try:
                latest = max(latest, path.stat().st_mtime)
            except OSError:
                pass
    return latest


class UpdateWatcher(QObject):
    """Emits ``updated`` once when the app's code changes on disk (e.g. after an update
    was pulled into the project folder). Does nothing in the stand-alone .exe."""

    updated = Signal()

    def __init__(self, parent=None, interval_ms: int = 15_000, folder: Path = PACKAGE_DIR):
        super().__init__(parent)
        self.folder = folder
        self.enabled = not getattr(sys, "frozen", False)
        self.baseline = code_signature(folder) if self.enabled else 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(interval_ms)
        self.timer.timeout.connect(self.check)
        if self.enabled:
            self.timer.start()

    def check(self) -> bool:
        if not self.enabled:
            return False
        if code_signature(self.folder) > self.baseline:
            self.timer.stop()
            self.updated.emit()
            return True
        return False
