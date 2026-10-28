# Aurum LIMS

A Laboratory Information Management System for QC and regulated laboratories
(GMP, ISO/IEC 17025), built as a Windows desktop application.

**Status:** Phase 1 (foundation) is complete. This covers secure sign-in, user
roles, the audit trail and electronic signatures. Samples, tests and results
come next. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Features (Phase 1)

| Area | What it does |
|---|---|
| Accounts | One account per person. First launch creates the Administrator. Accounts are deactivated, never deleted, so every record stays attributable. |
| Roles | **Administrator** (users and settings), **QA Reviewer** (review/approve), **Analyst** (create/edit records), **Viewer** (read-only). Administrators do not approve data (segregation of duties). |
| Passwords | Argon2id hashing. Minimum length with a letter and a number. Expiry (default 90 days). Recent passwords cannot be re-used. Temporary passwords must be changed at first sign-in. |
| Lockout | 5 wrong passwords lock the account until an Administrator unlocks it. |
| Inactivity | The screen locks after 15 minutes without input and needs the same user's password to continue. A **Lock** button locks it immediately. |
| Audit trail | Every sign-in, failed attempt, lock, user change, setting change and signature is recorded with UTC time, user, old → new values and reason. Rows cannot be updated or deleted (database triggers), and each row is SHA-256 hash-chained to the previous one. **Verify integrity** detects altered, inserted or removed entries. Exports to CSV. |
| E-signatures | Signing requires re-entering the password and records the signer's printed name, date/time and meaning (Performed / Reviewed / Approved / Rejected by). Signatures cannot be changed. |
| Reason for change | All administrative changes require a written reason, which is stored in the audit trail. |

All settings above can be changed by an Administrator on the **Settings** page.

## Running the app

Requires Windows and Python 3.11 or newer ([python.org](https://www.python.org/downloads/)).

Double-click **`scripts\run.bat`**. The first time, it creates a private Python
environment in `.venv` and installs the dependencies, which needs internet. After
that it starts the app directly.

Or, from a terminal in the project folder:

```bash
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m aurum_lims
```

On first launch you'll be asked to create the Administrator account. Sign in with
it, then add the rest of the lab on the **Users** page.

## Building a stand-alone .exe

```bash
scripts\build_exe.bat
```

This produces `dist\AurumLIMS\AurumLIMS.exe`. Copy the whole `dist\AurumLIMS`
folder to any Windows PC; Python does not need to be installed there.

## Where data is stored

| What | Location |
|---|---|
| Database | `%APPDATA%\AurumLIMS\aurum.db` |
| Logs | `%APPDATA%\AurumLIMS\logs\` |

To put the data somewhere else, set the environment variable
`AURUM_LIMS_DATA_DIR` (folder) or `AURUM_LIMS_DATABASE_URL` (full database URL).

**Back up `aurum.db` regularly** with the app closed. Lab data is never stored in
the git repository.

## Development

```bash
.venv\Scripts\python -m pytest
```

The tests cover the security rules (lockout, permissions, password policy,
audit-trail tamper detection, e-signatures) and build the main screens offscreen.
GitHub Actions runs them on every push.

Project layout:

```
src/aurum_lims/
  app.py               entry point, sign-in / sign-out flow
  config.py            data folder and database location
  db/                  SQLAlchemy models and database session
  migrations/          Alembic schema migrations (run automatically at start-up)
  security/            password hashing/policy, roles and permissions
  services/            business rules: auth, users, audit, e-signatures, settings
  ui/                  PySide6 screens (dialogs, main window, pages in ui/views)
tests/                 pytest suite
scripts/               run.bat, build_exe.bat
docs/ROADMAP.md        planned phases
```

Changing the database schema: edit `db/models.py`, then add a migration in
`src/aurum_lims/migrations/versions/` (for example `alembic revision -m "add samples"`
from the project root). The app applies pending migrations at start-up.

## Compliance note

Aurum LIMS is designed to support 21 CFR Part 11, EU GMP Annex 11 and ISO/IEC
17025 data-integrity expectations (ALCOA+). Software alone does not make a lab
compliant. A regulated lab still needs to **validate** the system for its intended
use (URS, risk assessment, IQ/OQ/PQ), and to put procedures in place for account
management, backup and restore, periodic audit-trail review, and change control.
