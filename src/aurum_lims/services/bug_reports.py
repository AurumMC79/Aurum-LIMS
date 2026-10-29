"""Bug reports written by users inside the app.

Anyone can report a problem (with an optional screenshot). Reporters see
their own reports; administrators see all of them and update the status.
Developers read them with ``python -m aurum_lims.tools.bug_reports``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from aurum_lims import config
from aurum_lims.db.models import BugReport, BugReportScreenshot, IdCounter, User, utcnow
from aurum_lims.errors import NotFound, PermissionDenied, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

MAX_SCREENSHOT_BYTES = 10 * 1024 * 1024
MAX_SCREENSHOTS = 10


class Severity(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"

    @property
    def help(self) -> str:
        return {
            "Low": "Cosmetic or minor inconvenience",
            "Medium": "Something works wrongly but there is a workaround",
            "High": "Blocks my work; no workaround",
            "Critical": "Wrong results, data loss or a compliance risk",
        }[self.value]


class BugStatus(StrEnum):
    NEW = "New"
    IN_PROGRESS = "In progress"
    FIXED = "Fixed"
    WONT_FIX = "Won't fix"
    DUPLICATE = "Duplicate"


OPEN_STATUSES = (BugStatus.NEW, BugStatus.IN_PROGRESS)


@dataclass
class BugRow:
    id: int
    number: str
    title: str
    area: str
    severity: str
    status: str
    description: str
    steps: str | None
    expected: str | None
    resolution: str | None
    screenshot_count: int
    app_version: str
    reported_at: datetime
    reported_by: str
    updated_at: datetime | None
    updated_by: str | None


def _next_number(session: Session) -> str:
    counter = session.get(IdCounter, ("bug", "all"))
    if counter is None:
        counter = IdCounter(scope="bug", period="all", last_value=0)
        session.add(counter)
    counter.last_value += 1
    session.flush()
    return f"BUG-{counter.last_value:04d}"


def submit(session: Session, actor: SessionUser, *, title: str, area: str, severity: Severity | str,
           description: str, steps: str | None = None, expected: str | None = None,
           screenshots: list[bytes] | None = None) -> BugReport:
    """Any signed-in user can report a bug, with up to ``MAX_SCREENSHOTS`` screenshots."""
    title = " ".join((title or "").split())
    if not title:
        raise ValidationError("Give the problem a short title.")
    if len(title) > 200:
        raise ValidationError("The title must be 200 characters or fewer.")
    if not (description or "").strip():
        raise ValidationError("Describe what happened.")
    screenshots = [s for s in (screenshots or []) if s]
    if len(screenshots) > MAX_SCREENSHOTS:
        raise ValidationError(f"A report can have at most {MAX_SCREENSHOTS} screenshots.")
    for number, image in enumerate(screenshots, start=1):
        if len(image) > MAX_SCREENSHOT_BYTES:
            raise ValidationError(f"Screenshot {number} is larger than 10 MB.")
        if not (image.startswith(b"\x89PNG") or image.startswith(b"\xff\xd8")):
            raise ValidationError(f"Screenshot {number} must be a PNG or JPEG image.")
    report = BugReport(
        number=_next_number(session), title=title, area=(area or "General").strip()[:60],
        severity=Severity(severity).value, description=description.strip(), steps=(steps or "").strip() or None,
        expected=(expected or "").strip() or None, app_version=config.APP_VERSION,
        status=BugStatus.NEW, reported_by_id=actor.id,
    )
    session.add(report)
    session.flush()
    for position, image in enumerate(screenshots, start=1):
        session.add(BugReportScreenshot(report_id=report.id, position=position, image=image))
    session.flush()
    audit.record(session, AuditAction.BUG_REPORTED, actor=actor, entity_type="bug_report", entity_id=report.number,
                 new={"title": title, "area": report.area, "severity": report.severity,
                      "screenshots": len(screenshots)})
    return report


def update_status(session: Session, actor: SessionUser, report_id: int, status: BugStatus | str,
                  resolution: str | None = None) -> BugReport:
    require(actor, Permission.MANAGE_BUG_REPORTS)
    report = session.get(BugReport, report_id)
    if report is None:
        raise NotFound("Bug report not found.")
    status = BugStatus(status)
    resolution = (resolution or "").strip() or None
    if status in (BugStatus.FIXED, BugStatus.WONT_FIX, BugStatus.DUPLICATE) and not resolution:
        raise ValidationError("Add a note explaining the resolution.")
    old = {"status": report.status, "resolution": report.resolution}
    if old == {"status": status.value, "resolution": resolution}:
        raise ValidationError("Nothing was changed.")
    report.status, report.resolution = status.value, resolution
    report.updated_at, report.updated_by_id = utcnow(), actor.id
    audit.record(session, AuditAction.BUG_UPDATED, actor=actor, entity_type="bug_report", entity_id=report.number,
                 old=old, new={"status": status.value, "resolution": resolution})
    return report


def list_reports(session: Session, actor: SessionUser | None, *, include_closed: bool = True) -> list[BugRow]:
    """All reports for administrators (or developer tools, ``actor=None``); own reports for others."""
    reporter, updater = aliased(User), aliased(User)
    stmt = (select(BugReport, reporter.full_name, updater.full_name)
            .join(reporter, BugReport.reported_by_id == reporter.id)
            .outerjoin(updater, BugReport.updated_by_id == updater.id)
            .order_by(BugReport.id.desc()))
    if actor is not None and not actor.can(Permission.MANAGE_BUG_REPORTS):
        stmt = stmt.where(BugReport.reported_by_id == actor.id)
    if not include_closed:
        stmt = stmt.where(BugReport.status.in_([s.value for s in OPEN_STATUSES]))
    counts = dict(session.execute(select(BugReportScreenshot.report_id, func.count())
                                  .group_by(BugReportScreenshot.report_id)).all())
    return [BugRow(b.id, b.number, b.title, b.area, b.severity, b.status, b.description, b.steps, b.expected,
                   b.resolution, counts.get(b.id, 0), b.app_version, b.reported_at, who, b.updated_at, upd)
            for b, who, upd in session.execute(stmt)]


def screenshots(session: Session, actor: SessionUser | None, report_id: int) -> list[bytes]:
    """The report's screenshots in the order they were added."""
    report = session.get(BugReport, report_id)
    if report is None:
        raise NotFound("Bug report not found.")
    if actor is not None and not actor.can(Permission.MANAGE_BUG_REPORTS) and report.reported_by_id != actor.id:
        raise PermissionDenied("You can only see your own reports.")
    return list(session.scalars(select(BugReportScreenshot.image).where(BugReportScreenshot.report_id == report_id)
                                .order_by(BugReportScreenshot.position)))
