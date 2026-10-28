"""Custom sample ID formats.

A format is literal text mixed with tokens, for example ``AU-{YYYY}-{SEQ:5}``
which produces ``AU-2026-00001``. Tokens:

* ``{SEQ}`` / ``{SEQ:n}`` - running number, zero-padded to ``n`` digits (required, once)
* ``{YYYY}`` ``{YY}`` ``{MM}`` ``{DD}`` - date the sample is registered
* ``{TYPE}`` - the sample type's code (e.g. ``RM``)

The running number can restart every year, month or day, or never. When a
format contains ``{TYPE}``, each sample type counts separately
(RM-0001, FP-0001...); otherwise the number is shared.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import AppSetting, IdCounter, Sample, SampleType, utcnow
from aurum_lims.errors import ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

DEFAULT_FORMAT = "AU-{YYYY}-{SEQ:5}"
DEFAULT_SEQ_WIDTH = 4
MAX_ID_LENGTH = 64
FORMAT_KEY = "sample_id_format"
RESET_KEY = "sample_id_reset"

TOKEN_PATTERN = re.compile(r"\{([A-Z]+)(?::(\d+))?\}")
LITERAL_PATTERN = re.compile(r"^[A-Za-z0-9\-_./#]*$")
KNOWN_TOKENS = {"SEQ", "YYYY", "YY", "MM", "DD", "TYPE"}

TOKEN_HELP = [
    ("{SEQ:5}", "Running number, padded to 5 digits (required)"),
    ("{YYYY}", "4-digit year"),
    ("{YY}", "2-digit year"),
    ("{MM}", "Month (01-12)"),
    ("{DD}", "Day (01-31)"),
    ("{TYPE}", "Sample type code, e.g. RM (each type then counts separately)"),
]


class ResetPolicy(StrEnum):
    NEVER = "never"
    YEARLY = "yearly"
    MONTHLY = "monthly"
    DAILY = "daily"

    @property
    def label(self) -> str:
        return {"never": "Never", "yearly": "Every year", "monthly": "Every month", "daily": "Every day"}[self.value]


DEFAULT_RESET = ResetPolicy.YEARLY


@dataclass(frozen=True)
class IdFormat:
    pattern: str
    reset: ResetPolicy


def format_problems(pattern: str, reset: ResetPolicy | str) -> list[str]:
    """Explain what is wrong with a format (empty list = valid)."""
    reset = ResetPolicy(reset)
    problems = []
    if not pattern.strip():
        return ["The format cannot be empty."]
    tokens = TOKEN_PATTERN.findall(pattern)
    names = [name for name, _ in tokens]
    unknown = sorted({name for name in names if name not in KNOWN_TOKENS})
    if unknown:
        problems.append("Unknown token(s): " + ", ".join("{" + u + "}" for u in unknown))
    if names.count("SEQ") != 1:
        problems.append("The format must contain {SEQ} (the running number) exactly once.")
    for name, width in tokens:
        if name == "SEQ" and width and not 1 <= int(width) <= 10:
            problems.append("The {SEQ} width must be between 1 and 10 digits.")
        if name != "SEQ" and width:
            problems.append(f"{{{name}}} does not take a width.")
    literal = TOKEN_PATTERN.sub("", pattern)
    if "{" in literal or "}" in literal:
        problems.append("Check the braces: tokens look like {YYYY} or {SEQ:5}.")
    elif not LITERAL_PATTERN.match(literal):
        problems.append("Fixed text may only use letters, numbers and - _ . / #")
    has_year = "YYYY" in names or "YY" in names
    if reset is ResetPolicy.YEARLY and not has_year:
        problems.append("A yearly restart needs {YYYY} or {YY} in the format, otherwise IDs would repeat.")
    if reset is ResetPolicy.MONTHLY and not (has_year and "MM" in names):
        problems.append("A monthly restart needs the year and {MM} in the format, otherwise IDs would repeat.")
    if reset is ResetPolicy.DAILY and not (has_year and "MM" in names and "DD" in names):
        problems.append("A daily restart needs the year, {MM} and {DD} in the format, otherwise IDs would repeat.")
    if not problems and len(render(pattern, 1, date(2026, 12, 31), "ABCDEFGHIJKLMNOP")) > MAX_ID_LENGTH:
        problems.append(f"Generated IDs would be longer than {MAX_ID_LENGTH} characters.")
    return problems


def validate(pattern: str, reset: ResetPolicy | str) -> None:
    problems = format_problems(pattern, reset)
    if problems:
        raise ValidationError(" ".join(problems))


def render(pattern: str, seq: int, when: date, type_code: str = "") -> str:
    def replace(match: re.Match) -> str:
        name, width = match.group(1), match.group(2)
        if name == "SEQ":
            return str(seq).zfill(int(width) if width else DEFAULT_SEQ_WIDTH)
        return {
            "YYYY": f"{when.year:04d}",
            "YY": f"{when.year % 100:02d}",
            "MM": f"{when.month:02d}",
            "DD": f"{when.day:02d}",
            "TYPE": type_code,
        }.get(name, match.group(0))

    return TOKEN_PATTERN.sub(replace, pattern)


def preview(pattern: str, reset: ResetPolicy | str, type_code: str = "RM", when: date | None = None) -> list[str]:
    """Example IDs for showing next to the format editor."""
    when = when or date.today()
    if format_problems(pattern, reset):
        return []
    return [render(pattern, n, when, type_code) for n in (1, 2, 3)]


def period_key(reset: ResetPolicy, when: date) -> str:
    return {
        ResetPolicy.NEVER: "all",
        ResetPolicy.YEARLY: f"{when.year:04d}",
        ResetPolicy.MONTHLY: f"{when.year:04d}{when.month:02d}",
        ResetPolicy.DAILY: f"{when.year:04d}{when.month:02d}{when.day:02d}",
    }[reset]


def get_lab_format(session: Session) -> IdFormat:
    pattern = session.get(AppSetting, FORMAT_KEY)
    reset = session.get(AppSetting, RESET_KEY)
    return IdFormat(
        pattern.value if pattern else DEFAULT_FORMAT,
        ResetPolicy(reset.value) if reset else DEFAULT_RESET,
    )


def set_lab_format(session: Session, actor: SessionUser, pattern: str, reset: ResetPolicy | str, reason: str) -> IdFormat:
    """Change the lab-wide sample ID format (used by types without their own)."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    pattern = pattern.strip()
    reset = ResetPolicy(reset)
    validate(pattern, reset)
    old = get_lab_format(session)
    if old.pattern == pattern and old.reset == reset:
        raise ValidationError("Nothing was changed.")
    for key, value in ((FORMAT_KEY, pattern), (RESET_KEY, reset.value)):
        row = session.get(AppSetting, key)
        if row is None:
            row = AppSetting(key=key, value=value)
            session.add(row)
        row.value = value
        row.updated_at = utcnow()
        row.updated_by_id = actor.id
    audit.record(
        session, AuditAction.SETTING_CHANGED, actor=actor, entity_type="setting", entity_id="sample_id_format",
        old={"format": old.pattern, "restart": old.reset.value}, new={"format": pattern, "restart": reset.value},
        reason=reason.strip(),
    )
    session.flush()
    return IdFormat(pattern, reset)


def effective_format(session: Session, sample_type: SampleType) -> tuple[IdFormat, str]:
    """The format a sample type uses, and the counter scope it draws numbers from."""
    if sample_type.id_format:
        fmt = IdFormat(sample_type.id_format, ResetPolicy(sample_type.id_reset or DEFAULT_RESET))
        return fmt, f"type:{sample_type.id}"
    fmt = get_lab_format(session)
    scope = f"lab:type:{sample_type.id}" if "{TYPE}" in fmt.pattern else "lab"
    return fmt, scope


def peek_next_id(session: Session, sample_type: SampleType, when: date | None = None) -> str:
    """The ID the next registration would get, without reserving it."""
    when = when or datetime.now().astimezone().date()
    fmt, scope = effective_format(session, sample_type)
    counter = session.get(IdCounter, (scope, period_key(fmt.reset, when)))
    value = counter.last_value if counter else 0
    while True:
        value += 1
        candidate = render(fmt.pattern, value, when, sample_type.code)
        if session.scalar(select(Sample.id).where(Sample.sample_id == candidate)) is None:
            return candidate


def next_sample_id(session: Session, sample_type: SampleType, when: date | None = None) -> str:
    """Reserve and return the next sample ID for ``sample_type``.

    Numbers already taken (for example after the format was changed) are
    skipped, so a generated ID is always unique."""
    when = when or datetime.now().astimezone().date()
    fmt, scope = effective_format(session, sample_type)
    period = period_key(fmt.reset, when)
    counter = session.get(IdCounter, (scope, period))
    if counter is None:
        counter = IdCounter(scope=scope, period=period, last_value=0)
        session.add(counter)
    while True:
        counter.last_value += 1
        candidate = render(fmt.pattern, counter.last_value, when, sample_type.code)
        if session.scalar(select(Sample.id).where(Sample.sample_id == candidate)) is None:
            break
    session.flush()
    return candidate
