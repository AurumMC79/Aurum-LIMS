"""Reports: turnaround, workload, OOS and control charts."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from aurum_lims.db.models import AuditEntry, Sample
from aurum_lims.services import reports, review, samples, storage, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.reports import Period, TrendOption, TrendPoint
from aurum_lims.services.samples import Receipt, SampleDetails
from tests.conftest import USER_PASSWORD
from tests.test_review import LOW_ASSAY, _review_all, signed  # noqa: F401 - fixture
from tests.test_testing import ASSAY_VALUES, setup  # noqa: F401 - fixture

def last_30() -> Period:
    return Period.last_days(30)


ASSAY = TrendOption("HPLC-ASSAY", "assay", "Assay", "%")


def _points(values):
    now = datetime.now(timezone.utc)
    return [TrendPoint(now + timedelta(hours=i), v, str(v), f"S{i}", None, "Pass") for i, v in enumerate(values)]


# ----- pure helpers --------------------------------------------------------------

def test_control_chart_rules():
    points = _points([100, 100.4, 99.6, 100.2, 99.8, 104])  # last point far out
    reports.flag_signals(points, mean=100, sigma=0.5)
    assert points[-1].signals == ["beyond 3σ"] and not any(p.signals for p in points[:-1])
    points = _points([101, 101.2, 100.9, 101.1, 100.8, 101.3, 101, 100.7, 101.4])  # 9 above the mean
    reports.flag_signals(points, mean=100, sigma=1)
    assert all("9 in a row on one side" in p.signals for p in points)
    points = _points([99, 99.2, 99.4, 99.6, 99.8, 100.0, 99.0])  # 6 rising
    reports.flag_signals(points, mean=99.5, sigma=1)
    assert all("6 in a row rising / falling" in p.signals for p in points[:6]) and not points[6].signals


def test_percentile_period_and_cpk():
    assert reports._percentile([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 0.9) == pytest.approx(9.1)
    assert reports._percentile([], 0.5) is None
    period = Period.between(date(2026, 10, 1), date(2026, 10, 31))
    assert period.contains(datetime(2026, 10, 31, 12, tzinfo=timezone.utc).astimezone())
    assert (period.end - period.start).days in (30, 31)
    t = reports.Trend(ASSAY, [], mean=100.0, sigma=1.0, spec_low=95.0, spec_high=105.0)
    assert t.cpk == pytest.approx(5 / 3)
    t.spec_low = None
    t.mean = 104.0
    assert t.cpk == pytest.approx(1 / 3)


# ----- from real records -----------------------------------------------------------

def test_turnaround_and_workload(db, signed, make_user):  # noqa: F811
    _review_all(db, signed)
    with db.session() as s:
        assert review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD).ok
    with db.session() as s:
        sample = s.get(Sample, signed["sample"])
        sample.received_at = sample.received_at - timedelta(days=2, hours=12)
        sample.due_date = date.today() + timedelta(days=1)
    viewer = make_user("viewer1", "viewer")
    with db.session() as s:
        tat = reports.turnaround(s, viewer, last_30())
        [row] = tat.rows
        assert row.days == pytest.approx(2.5, abs=0.01) and row.on_time and row.decision == "Approved"
        assert tat.median_days == row.days and tat.on_time_percent == 100
        assert tat.by_type == [("Finished Product", 1, row.days)]
        assert sum(n for _, n, _ in tat.timeline) == 1
        year = reports.turnaround(s, viewer, Period.last_days(365))
        assert reports.use_months(Period.last_days(365)) and 12 <= len(year.timeline) <= 13
        assert all(b.day == 1 for b, _, _ in year.timeline)
        assert reports.turnaround(s, viewer, Period.last_days(1, datetime.now(timezone.utc) - timedelta(days=5))).rows == []

        work = reports.workload(s, viewer, last_30())
        assert (work.tests_signed, work.received, work.decided) == (2, 1, 1)
        assert dict((name, (signed_, reviewed)) for name, signed_, reviewed in work.people) == \
            {"Jsmith": (2, 0), "Qa.Lee": (0, 2)}
        assert work.backlog == []


def test_oos_summary(db, setup):  # noqa: F811
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        [assay] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        testing.save_values(s, setup["analyst"], assay, LOW_ASSAY)
    with db.session() as s:
        assert testing.complete_analysis(s, setup["analyst"], assay, USER_PASSWORD, {"assay": "Low"}).ok
    with db.session() as s:
        summary = reports.oos_summary(s, setup["analyst"], last_30())
        [row] = summary.rows
        assert row.test == "HPLC-ASSAY" and row.parameter == "Assay" and row.status == "Open"
        assert summary.by_test == [("HPLC-ASSAY – Assay", 1)] and summary.by_conclusion == [("Still open", 1)]
        assert summary.rate_per_100 == 100 and summary.open_now == 1
        assert sum(n for _, n in summary.monthly) == 1


def test_control_chart_from_results(db, setup, admin):  # noqa: F811
    with db.session() as s:
        shelf = storage.create_location(s, admin, name="Shelf 9", kind="Shelf").id
        fp = s.get(Sample, setup["sample"]).sample_type_id
        pks = [x.id for x in samples.register_samples(
            s, setup["analyst"], fp, SampleDetails(material_name="Aurumex 10 mg tablets", lot_number="B"),
            count=5, receipt=Receipt(location_id=shelf))]
    for i, pk in enumerate(pks):
        with db.session() as s:
            testing.set_specification(s, setup["analyst"], pk, setup["spec"])
            [test] = [a.id for a in testing.assign_tests(s, setup["analyst"], pk, [setup["assay"]])]
            area = str(10234 + 40 * i)
            testing.save_values(s, setup["analyst"], test, {**ASSAY_VALUES, ("area_spl", 1): area})
        with db.session() as s:
            assert testing.complete_analysis(s, setup["analyst"], test, USER_PASSWORD).ok
    with db.session() as s:
        options = reports.trend_options(s)
        assert ASSAY in options and all(o.method_code != "APP" for o in options)
        assert reports.trend_materials(s, "HPLC-ASSAY") == ["Aurumex 10 mg tablets"]
        chart = reports.trend(s, setup["analyst"], ASSAY, last_30())
        assert len(chart.points) == 5 and chart.not_quantified == 0
        values = [p.value for p in chart.points]
        assert values == sorted(values)  # rising areas -> rising assay, in result order
        assert chart.mean == pytest.approx(sum(values) / 5)
        assert chart.ucl > chart.mean > chart.lcl
        assert (chart.spec_low, chart.spec_high, chart.spec_text) == (95.0, 105.0, "95.0 – 105.0 %")
        assert chart.cpk is not None
        assert reports.trend(s, setup["analyst"], ASSAY, last_30(), material="Something else").points == []


def test_export_is_audited(db, setup, tmp_path):  # noqa: F811
    with db.session() as s:
        path = reports.export_csv(s, setup["analyst"], tmp_path / "tat.csv", "Turnaround",
                                  ["Sample", "Days"], [["AU-1", 2.5]])
        assert path.read_text(encoding="utf-8-sig").splitlines() == ["Sample,Days", "AU-1,2.5"]
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.REPORT_EXPORTED)).one()
        assert entry.entity_id == "Turnaround"
