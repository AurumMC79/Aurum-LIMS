"""Reloading the running app, and noticing when its code has been updated.

A reload starts a fresh copy of Aurum LIMS and closes this one. The new copy
picks up the latest code (and runs any database upgrade), and continues the
same user's session on the same page using a single-use code from
``auth.create_resume_token`` passed through the environment.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

RESUME_ENV = "AURUM_LIMS_RESUME_TOKEN"
PAGE_ENV = "AURUM_LIMS_RESUME_PAGE"
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
    if page:
        env[PAGE_ENV] = page
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(restart_command(), env=env, cwd=os.getcwd(), close_fds=True, creationflags=flags)


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
