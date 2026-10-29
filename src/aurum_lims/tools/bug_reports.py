"""Read the bug reports users have written in Aurum LIMS (read-only).

Usage (from the project folder)::

    .venv\\Scripts\\python -m aurum_lims.tools.bug_reports            # open reports
    .venv\\Scripts\\python -m aurum_lims.tools.bug_reports --all      # include fixed / closed
    .venv\\Scripts\\python -m aurum_lims.tools.bug_reports BUG-0003   # one report in full
    .venv\\Scripts\\python -m aurum_lims.tools.bug_reports --export DIR   # Markdown + screenshots

It reads the same database the app uses (``AURUM_LIMS_DATA_DIR`` /
``AURUM_LIMS_DATABASE_URL`` are respected) and never writes to it, so it is
safe to run while the app is open. Status changes are made in the app.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from aurum_lims import config
from aurum_lims.services import bug_reports


def _open_session() -> Session:
    url = config.database_url()
    if not url.startswith("sqlite:///"):
        return Session(create_engine(url))
    path = Path(url.removeprefix("sqlite:///"))
    if not path.exists():
        sys.exit(f"No database found at {path}. Has Aurum LIMS been started on this computer?")
    # Open the file read-only so this tool can never change lab data.
    engine = create_engine("sqlite://", creator=lambda: sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True))
    return Session(engine)


def _as_markdown(row: bug_reports.BugRow, screenshot_files: list[str] | None = None) -> str:
    lines = [
        f"# {row.number}: {row.title}",
        "",
        f"- **Status:** {row.status}",
        f"- **Severity:** {row.severity}",
        f"- **Where:** {row.area}",
        f"- **Reported by:** {row.reported_by} on {row.reported_at:%Y-%m-%d %H:%M} UTC (version {row.app_version})",
    ]
    if row.updated_at:
        lines.append(f"- **Last updated by:** {row.updated_by} on {row.updated_at:%Y-%m-%d %H:%M} UTC")
    lines += ["", "## What happened", "", row.description]
    if row.steps:
        lines += ["", "## Steps to reproduce", "", row.steps]
    if row.expected:
        lines += ["", "## Expected", "", row.expected]
    if row.resolution:
        lines += ["", "## Resolution", "", row.resolution]
    if row.screenshot_count:
        lines += ["", "## Screenshots" if row.screenshot_count > 1 else "## Screenshot", ""]
        if screenshot_files:
            lines += [f"![screenshot {n}]({name})" for n, name in enumerate(screenshot_files, start=1)]
        else:
            lines.append(f"({row.screenshot_count} attached – use --export to save them)")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read Aurum LIMS bug reports (read-only).")
    parser.add_argument("number", nargs="?", help="show one report in full, e.g. BUG-0003")
    parser.add_argument("--all", action="store_true", help="include fixed / won't fix / duplicate reports")
    parser.add_argument("--export", metavar="DIR", help="write each report as Markdown (plus screenshots) to DIR")
    args = parser.parse_args(argv)

    with _open_session() as session:
        rows = bug_reports.list_reports(session, None, include_closed=args.all or bool(args.number))
        if args.number:
            rows = [r for r in rows if r.number.upper() == args.number.upper()]
            if not rows:
                print(f"{args.number} not found.")
                return 1
        if args.export:
            out = Path(args.export)
            out.mkdir(parents=True, exist_ok=True)
            for row in rows:
                names = []
                images = bug_reports.screenshots(session, None, row.id)
                for n, image in enumerate(images, start=1):
                    name = f"{row.number}.png" if len(images) == 1 else f"{row.number}-{n}.png"
                    (out / name).write_bytes(image)
                    names.append(name)
                (out / f"{row.number}.md").write_text(_as_markdown(row, names), encoding="utf-8")
            print(f"Exported {len(rows)} report(s) to {out}")
            return 0
        if args.number:
            print(_as_markdown(rows[0]))
            return 0
        if not rows:
            print("No open bug reports." if not args.all else "No bug reports.")
            return 0
        for row in rows:
            print(f"{row.number}  [{row.status} · {row.severity} · {row.area}]  {row.title}  "
                  f"— {row.reported_by}, {row.reported_at:%Y-%m-%d}")
        print(f"\n{len(rows)} report(s). Show one in full with: python -m aurum_lims.tools.bug_reports BUG-0001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
