"""Reports and trends: turnaround time, workload, OOS and control charts.

All figures are calculated from the records themselves (sample receipt,
QA decisions, signatures, OOS investigations, frozen reportable results), so
they always agree with what the rest of the system shows.

* **Turnaround** is calendar days from receipt to the sample's first QA
  decision (an amended certificate does not restart the clock).
* **Control charts** are individuals charts in result order: the centre line is
  the mean, sigma is estimated from the average moving range (MR̄ / 1.128) and
  the control limits are mean ± 3 sigma. Points are flagged for the classic
  rules: beyond 3 sigma, 9 in a row on one side of the mean, and 6 in a row
  steadily rising or falling. Results reported as ND or < LOQ are not plotted
  (their value is not quantified) and are counted separately.
"""

from __future__ import annotations

import csv
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import (
    Analysis,
    MethodParameter,
    OOSInvestigation,
    ReportableResult,
    Sample,
    SampleDecision,
    SampleType,
    SpecLimit,
    TestMethod,
    User,
)
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, formulas
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.methods import ParameterKind, ResultType
from aurum_lims.services.samples import CLOSED_STATUSES, DECIDED_STATUSES
from aurum_lims.services.testing import AnalysisStatus

D2 = 1.128  # control-chart constant for moving ranges of two


@dataclass(frozen=True)
class Period:
    start: datetime  # inclusive, UTC
    end: datetime  # exclusive, UTC

    @classmethod
    def last_days(cls, days: int, now: datetime | None = None) -> Period:
        now = now or datetime.now(timezone.utc)
        return cls(now - timedelta(days=days), now)

    @classmethod
    def between(cls, first: date, last: date) -> Period:
        """Whole local days from ``first`` to ``last`` inclusive."""
        start = datetime.combine(first, datetime.min.time()).astimezone()
        end = datetime.combine(last + timedelta(days=1), datetime.min.time()).astimezone()
        return cls(start.astimezone(timezone.utc), end.astimezone(timezone.utc))

    def contains(self, moment: datetime | None) -> bool:
        return moment is not None and self.start <= moment < self.end


def week_start(moment: datetime) -> date:
    local = moment.astimezone().date()
    return local - timedelta(days=local.weekday())


def month_start(moment: datetime) -> date:
    return moment.astimezone().date().replace(day=1)


def _weeks(period: Period) -> list[date]:
    first, last = week_start(period.start), week_start(period.end - timedelta(microseconds=1))
    weeks = []
    while first <= last:
        weeks.append(first)
        first += timedelta(days=7)
    return weeks


def _months(period: Period) -> list[date]:
    first, last = month_start(period.start), month_start(period.end - timedelta(microseconds=1))
    months = []
    while first <= last:
        months.append(first)
        first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return months


def use_months(period: Period) -> bool:
    """Charts over time use weeks, or months for periods longer than about four months."""
    return (period.end - period.start).days > 120


def _bucket(period: Period):
    return month_start if use_months(period) else week_start


def _buckets(period: Period) -> list[date]:
    return _months(period) if use_months(period) else _weeks(period)


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


# ---------------------------------------------------------------------------
# turnaround
# ---------------------------------------------------------------------------


@dataclass
class TatRow:
    sample_id: str
    sample_type: str
    material: str | None
    priority: str
    received_at: datetime
    decided_at: datetime
    decision: str
    days: float
    due_date: date | None
    on_time: bool | None


@dataclass
class Turnaround:
    rows: list[TatRow]
    by_type: list[tuple[str, int, float]] = field(default_factory=list)  # type, samples, median days
    timeline: list[tuple[date, int, float | None]] = field(default_factory=list)  # week/month, samples, median

    @property
    def median_days(self) -> float | None:
        return statistics.median([r.days for r in self.rows]) if self.rows else None

    @property
    def p90_days(self) -> float | None:
        return _percentile([r.days for r in self.rows], 0.9)

    @property
    def on_time_percent(self) -> float | None:
        judged = [r.on_time for r in self.rows if r.on_time is not None]
        return 100 * sum(judged) / len(judged) if judged else None


def turnaround(session: Session, actor: SessionUser, period: Period) -> Turnaround:
    require(actor, Permission.VIEW_RECORDS)
    first_decision = (select(SampleDecision.sample_id, func.min(SampleDecision.id).label("decision_id"))
                      .group_by(SampleDecision.sample_id).subquery())
    stmt = (select(Sample, SampleDecision, SampleType.name)
            .join(first_decision, first_decision.c.sample_id == Sample.id)
            .join(SampleDecision, SampleDecision.id == first_decision.c.decision_id)
            .join(SampleType, SampleType.id == Sample.sample_type_id)
            .where(Sample.received_at.is_not(None)))
    rows = []
    for sample, decision, type_name in session.execute(stmt):
        if not period.contains(decision.decided_at):
            continue
        days = (decision.decided_at - sample.received_at).total_seconds() / 86400
        on_time = None if sample.due_date is None else decision.decided_at.astimezone().date() <= sample.due_date
        rows.append(TatRow(sample.sample_id, type_name, sample.material_name or sample.sampling_point,
                           sample.priority, sample.received_at, decision.decided_at, decision.decision,
                           round(days, 2), sample.due_date, on_time))
    rows.sort(key=lambda r: r.decided_at)
    result = Turnaround(rows)
    grouped = defaultdict(list)
    for r in rows:
        grouped[r.sample_type].append(r.days)
    result.by_type = sorted(((t, len(v), statistics.median(v)) for t, v in grouped.items()), key=lambda x: x[0])
    bucket, timeline = _bucket(period), defaultdict(list)
    for r in rows:
        timeline[bucket(r.decided_at)].append(r.days)
    result.timeline = [(b, len(timeline[b]), statistics.median(timeline[b]) if timeline[b] else None)
                       for b in _buckets(period)]
    return result


# ---------------------------------------------------------------------------
# workload
# ---------------------------------------------------------------------------


@dataclass
class Workload:
    timeline: list[tuple[date, int, int]]  # week/month, samples received, samples decided
    people: list[tuple[str, int, int]]  # name, tests signed, tests reviewed
    backlog: list[tuple[str, int]]  # sample status, open samples
    tests_signed: int = 0
    received: int = 0
    decided: int = 0


def workload(session: Session, actor: SessionUser, period: Period) -> Workload:
    require(actor, Permission.VIEW_RECORDS)
    bucket = _bucket(period)
    received = Counter(bucket(t) for (t,) in session.execute(select(Sample.received_at).where(
        Sample.received_at >= period.start, Sample.received_at < period.end)))
    decided_times = [t for (t,) in session.execute(
        select(func.min(SampleDecision.decided_at)).group_by(SampleDecision.sample_id)) if period.contains(t)]
    decided = Counter(bucket(t) for t in decided_times)
    names = {u.id: u.full_name for u in session.scalars(select(User))}
    signed, reviewed = Counter(), Counter()
    for who, when in session.execute(select(Analysis.completed_by_id, Analysis.completed_at).where(
            Analysis.completed_by_id.is_not(None), Analysis.status != AnalysisStatus.CANCELLED)):
        if period.contains(when):
            signed[who] += 1
    for who, when in session.execute(select(Analysis.reviewed_by_id, Analysis.reviewed_at).where(
            Analysis.status == AnalysisStatus.REVIEWED)):
        if period.contains(when):
            reviewed[who] += 1
    people = sorted(((names.get(p, "?"), signed[p], reviewed[p]) for p in set(signed) | set(reviewed)),
                    key=lambda x: (-(x[1] + x[2]), x[0]))
    open_statuses = session.execute(select(Sample.status, func.count()).where(
        Sample.status.not_in([*CLOSED_STATUSES, *DECIDED_STATUSES])).group_by(Sample.status))
    order = ["Registered", "Received", "In Testing", "Testing Complete", "Reviewed"]
    backlog = sorted(((s, n) for s, n in open_statuses), key=lambda x: order.index(x[0]) if x[0] in order else 99)
    return Workload([(b, received[b], decided[b]) for b in _buckets(period)], people, backlog,
                    sum(signed.values()), sum(received.values()), len(decided_times))


# ---------------------------------------------------------------------------
# OOS
# ---------------------------------------------------------------------------


@dataclass
class OosRow:
    number: str
    opened_at: datetime
    sample_id: str
    test: str
    parameter: str
    result: str
    limit: str | None
    status: str
    conclusion: str | None


@dataclass
class OosSummary:
    rows: list[OosRow]
    monthly: list[tuple[date, int]]
    by_test: list[tuple[str, int]]
    by_conclusion: list[tuple[str, int]]
    tests_signed: int
    open_now: int

    @property
    def rate_per_100(self) -> float | None:
        return 100 * len(self.rows) / self.tests_signed if self.tests_signed else None


def oos_summary(session: Session, actor: SessionUser, period: Period) -> OosSummary:
    require(actor, Permission.VIEW_RECORDS)
    stmt = (select(OOSInvestigation, Sample.sample_id, TestMethod.code)
            .join(Sample, Sample.id == OOSInvestigation.sample_id)
            .join(Analysis, Analysis.id == OOSInvestigation.analysis_id)
            .join(TestMethod, TestMethod.id == Analysis.method_id)
            .where(OOSInvestigation.opened_at >= period.start, OOSInvestigation.opened_at < period.end)
            .order_by(OOSInvestigation.opened_at))
    rows = [OosRow(o.number, o.opened_at, sid, code, o.parameter_name, o.result_value, o.spec_text, o.status,
                   o.conclusion) for o, sid, code in session.execute(stmt)]
    monthly = Counter(month_start(r.opened_at) for r in rows)
    by_test = Counter(f"{r.test} – {r.parameter}" for r in rows)
    by_conclusion = Counter(r.conclusion or "Still open" for r in rows)
    signed = sum(1 for (when,) in session.execute(select(Analysis.completed_at).where(
        Analysis.completed_at.is_not(None), Analysis.status != AnalysisStatus.CANCELLED)) if period.contains(when))
    open_now = session.scalar(select(func.count()).select_from(OOSInvestigation)
                              .where(OOSInvestigation.status == "Open")) or 0
    return OosSummary(rows, [(m, monthly[m]) for m in _months(period)], by_test.most_common(),
                      by_conclusion.most_common(), signed, open_now)


# ---------------------------------------------------------------------------
# control charts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrendOption:
    method_code: str
    parameter_key: str
    parameter_name: str
    unit: str | None

    @property
    def label(self) -> str:
        return f"{self.method_code} – {self.parameter_name}" + (f" ({self.unit})" if self.unit else "")


def trend_options(session: Session) -> list[TrendOption]:
    """Every number result of every method (all versions, by method code)."""
    seen: dict[tuple[str, str], TrendOption] = {}
    stmt = (select(TestMethod.code, MethodParameter).join(MethodParameter, MethodParameter.method_id == TestMethod.id)
            .where(MethodParameter.kind == ParameterKind.RESULT, MethodParameter.result_type == ResultType.NUMERIC)
            .order_by(TestMethod.code, TestMethod.version, MethodParameter.position))
    for code, p in session.execute(stmt):
        seen[(code, p.key)] = TrendOption(code, p.key, p.name, p.unit)  # newest version's name wins
    return sorted(seen.values(), key=lambda o: (o.method_code, o.parameter_name))


def trend_materials(session: Session, method_code: str) -> list[str]:
    stmt = (select(Sample.material_name).join(Analysis, Analysis.sample_id == Sample.id)
            .join(TestMethod, TestMethod.id == Analysis.method_id)
            .where(TestMethod.code == method_code, Sample.material_name.is_not(None)).distinct())
    return sorted(m for (m,) in session.execute(stmt) if m)


@dataclass
class TrendPoint:
    when: datetime
    value: float
    reported: str
    sample_id: str
    lot_number: str | None
    conformance: str
    signals: list[str] = field(default_factory=list)


@dataclass
class Trend:
    option: TrendOption
    points: list[TrendPoint]
    not_quantified: int = 0
    mean: float | None = None
    sigma: float | None = None
    ucl: float | None = None
    lcl: float | None = None
    spec_low: float | None = None
    spec_high: float | None = None
    spec_text: str | None = None

    @property
    def cpk(self) -> float | None:
        if self.mean is None or not self.sigma or (self.spec_low is None and self.spec_high is None):
            return None
        sides = []
        if self.spec_high is not None:
            sides.append((self.spec_high - self.mean) / (3 * self.sigma))
        if self.spec_low is not None:
            sides.append((self.mean - self.spec_low) / (3 * self.sigma))
        return min(sides)

    @property
    def signal_count(self) -> int:
        return sum(1 for p in self.points if p.signals)


def flag_signals(points: list[TrendPoint], mean: float, sigma: float) -> None:
    """Mark points that break the control-chart rules."""
    values = [p.value for p in points]
    for i, v in enumerate(values):
        if sigma and abs(v - mean) > 3 * sigma:
            points[i].signals.append("beyond 3σ")
    for i in range(8, len(values)):
        window = values[i - 8:i + 1]
        if all(v > mean for v in window) or all(v < mean for v in window):
            for j in range(i - 8, i + 1):
                if "9 in a row on one side" not in points[j].signals:
                    points[j].signals.append("9 in a row on one side")
    for i in range(5, len(values)):
        window = values[i - 5:i + 1]
        pairs = list(zip(window, window[1:]))
        if all(b > a for a, b in pairs) or all(b < a for a, b in pairs):
            for j in range(i - 5, i + 1):
                if "6 in a row rising / falling" not in points[j].signals:
                    points[j].signals.append("6 in a row rising / falling")


def trend(session: Session, actor: SessionUser, option: TrendOption, period: Period,
          material: str | None = None) -> Trend:
    require(actor, Permission.VIEW_RECORDS)
    stmt = (select(ReportableResult, Analysis, Sample)
            .join(Analysis, Analysis.id == ReportableResult.analysis_id)
            .join(TestMethod, TestMethod.id == Analysis.method_id)
            .join(Sample, Sample.id == Analysis.sample_id)
            .where(TestMethod.code == option.method_code, ReportableResult.parameter_key == option.parameter_key,
                   ReportableResult.revision == Analysis.revision,
                   Analysis.status.in_([AnalysisStatus.COMPLETE, AnalysisStatus.REVIEWED]),
                   Analysis.completed_at >= period.start, Analysis.completed_at < period.end)
            .order_by(Analysis.completed_at, Analysis.id))
    if material:
        stmt = stmt.where(Sample.material_name == material)
    result = Trend(option, [])
    latest_sample = None
    for res, analysis, sample in session.execute(stmt):
        latest_sample = sample
        if res.reported_value == formulas.NOT_DETECTED or res.reported_value.startswith("<") or res.mean is None:
            result.not_quantified += 1
            continue
        result.points.append(TrendPoint(analysis.completed_at, float(Decimal(res.mean)), res.reported_value,
                                        sample.sample_id, sample.lot_number, res.conformance))
    values = [p.value for p in result.points]
    if values:
        result.mean = statistics.fmean(values)
        ranges = [abs(b - a) for a, b in zip(values, values[1:])]
        result.sigma = statistics.fmean(ranges) / D2 if ranges else None
        if result.sigma is not None:
            result.ucl, result.lcl = result.mean + 3 * result.sigma, result.mean - 3 * result.sigma
            flag_signals(result.points, result.mean, result.sigma)
    if latest_sample is not None and latest_sample.specification_id:
        limit = session.scalar(select(SpecLimit).where(SpecLimit.spec_id == latest_sample.specification_id,
                                                       SpecLimit.method_code == option.method_code,
                                                       SpecLimit.parameter_key == option.parameter_key))
        if limit is not None:
            result.spec_text = formulas.limit_text(limit.limit_type, limit.low, limit.high, limit.expected_text,
                                                   option.unit)
            if limit.limit_type in ("range", "nlt", "gt") and limit.low:
                result.spec_low = float(Decimal(limit.low))
            if limit.limit_type in ("range", "nmt", "lt") and limit.high:
                result.spec_high = float(Decimal(limit.high))
    return result


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def export_csv(session: Session, actor: SessionUser, path: Path | str, report: str, headers: list[str],
               rows: list[list]) -> Path:
    """Write a report table to CSV (opens in Excel) and record the export."""
    require(actor, Permission.VIEW_RECORDS)
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)
    audit.record(session, AuditAction.REPORT_EXPORTED, actor=actor, entity_type="report", entity_id=report,
                 new={"file": str(path), "rows": len(rows)})
    return path
