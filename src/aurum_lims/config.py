"""Application-wide configuration: where data lives and default security settings.

The data folder and database location can be overridden with environment
variables, which is how a shared network database (or PostgreSQL) will be
configured in a later phase:

* ``AURUM_LIMS_DATA_DIR``      - folder for the database and log files
* ``AURUM_LIMS_DATABASE_URL``  - full SQLAlchemy URL (overrides the data folder DB)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from aurum_lims import __version__

APP_NAME = "Aurum LIMS"
APP_VERSION = __version__
ORGANIZATION = "Aurum LIMS"


def data_dir() -> Path:
    """Return (and create) the folder that holds the database and logs."""
    override = os.environ.get("AURUM_LIMS_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32" and os.environ.get("APPDATA"):
        path = Path(os.environ["APPDATA"]) / "AurumLIMS"
    else:
        path = Path.home() / ".aurum_lims"
    path.mkdir(parents=True, exist_ok=True)
    return path


def database_url() -> str:
    """Return the SQLAlchemy URL of the LIMS database."""
    override = os.environ.get("AURUM_LIMS_DATABASE_URL")
    if override:
        return override
    return f"sqlite:///{(data_dir() / 'aurum.db').as_posix()}"


def log_dir() -> Path:
    path = data_dir() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path
