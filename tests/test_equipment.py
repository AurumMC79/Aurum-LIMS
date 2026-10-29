"""Instruments, inventory, method requirements and usage on tests."""

from datetime import date, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import instruments, inventory, methods, testing, usage
from aurum_lims.services.instruments import EventType, InstrumentDetails, InstrumentStatus
from aurum_lims.services.inventory import ItemDetails, ItemKind, LotStatus
from aurum_lims.services.methods import ParameterSpec as P
from aurum_lims.services.methods import RequirementSpec as R
from tests.conftest import USER_PASSWORD
from tests.test_testing import ASSAY_PARAMS, ASSAY_VALUES, setup  # noqa: F401 - fixture

TODAY = date.today()


def _instrument(db, admin, code="HPLC-01", category="HPLC", cal_due=TODAY + timedelta(days=90), interval=180):
    with db.session() as s:
        return instruments.create_instrument(
            s, admin, code, InstrumentDetails(name=f"{category} {code}", category=category,
                                              calibration_interval_days=interval),
            calibration_due=cal_due).id


def _standard_lot(db, admin, purity="99.8", expiry=TODAY + timedelta(days=365), quantity="100"):
    with db.session() as s:
        item = inventory.create_item(s, admin, ItemDetails(name="Aurumex reference standard",
                                                           kind=ItemKind.REFERENCE_STANDARD, unit="mg",
                                                           min_stock="50", days_after_opening=30))
        return inventory.receive_lot(s, admin, item.id, lot_number="RS-2026-07", quantity=quantity,
                                     received_on=TODAY - timedelta(days=60), expiry_date=expiry, purity=purity,
                                     certificate_ref="CoA RS-07").id


# ----- instruments -------------------------------------------------------------

def test_instrument_usability_and_events(db, admin, make_user):
    analyst = make_user("jsmith")
    hplc = _instrument(db, admin)
    with db.session() as s:
        assert instruments.problems(instruments.get(s, hplc)) == []
        with pytest.raises(PermissionDenied):
            instruments.create_instrument(s, analyst, "X-1", InstrumentDetails(name="x", category="Balance"))
    # Analysts can record a calibration; next due is calculated from the interval.
    with db.session() as s:
        instruments.record_event(s, analyst, hplc, event_type=EventType.CALIBRATION, performed_on=TODAY,
                                 result="Pass", performed_by="Vendor Co.", certificate_ref="CAL-123")
    with db.session() as s:
        assert instruments.get(s, hplc).calibration_due == TODAY + timedelta(days=180)
    # A failed check takes it out of service.
    with db.session() as s:
        instruments.record_event(s, analyst, hplc, event_type=EventType.PERFORMANCE_CHECK, performed_on=TODAY,
                                 result="Fail", notes="Pressure ripple")
    with db.session() as s:
        inst = instruments.get(s, hplc)
        assert inst.status == InstrumentStatus.OUT_OF_SERVICE and instruments.problems(inst)
        with pytest.raises(ValidationError, match="note"):
            instruments.record_event(s, analyst, hplc, event_type=EventType.RETURNED, performed_on=TODAY)
        instruments.record_event(s, analyst, hplc, event_type=EventType.RETURNED, performed_on=TODAY,
                                 notes="Pump seals replaced, check passed")
    with db.session() as s:
        assert instruments.get(s, hplc).status == InstrumentStatus.IN_SERVICE
        assert len(instruments.history(s, hplc)) == 3


def test_overdue_and_never_calibrated(db, admin):
    overdue = _instrument(db, admin, "BAL-01", "Balance", cal_due=TODAY - timedelta(days=1))
    never = _instrument(db, admin, "PH-01", "pH meter", cal_due=None)
    with db.session() as s:
        assert "was due" in instruments.problems(instruments.get(s, overdue))[0]
        assert "no calibration" in instruments.problems(instruments.get(s, never))[0]
        late, _ = instruments.due_soon(s)
        assert {i.code for i in late} == {"BAL-01", "PH-01"}
        instruments.record_event(s, admin, overdue, event_type=EventType.RETIRED, performed_on=TODAY,
                                 notes="Replaced")
    with db.session() as s:
        late, _ = instruments.due_soon(s)
        assert {i.code for i in late} == {"PH-01"}  # retired instruments are ignored


def test_instrument_events_immutable(db, admin):
    hplc = _instrument(db, admin)
    with db.session() as s:
        instruments.record_event(s, admin, hplc, event_type=EventType.MAINTENANCE, performed_on=TODAY, result="Pass")
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE instrument_events SET result = 'Fail'"))


# ----- inventory ---------------------------------------------------------------

def test_lot_lifecycle_and_stock(db, admin):
    lot_id = _standard_lot(db, admin)
    with db.session() as s:
        lot = inventory.get_lot(s, lot_id)
        assert lot.internal_id.startswith("LOT-") and lot.quantity_on_hand == "100"
        inventory.open_lot(s, admin, lot_id, TODAY)
    with db.session() as s:
        row = inventory.list_lots(s)[0]
        assert row.expiry == TODAY + timedelta(days=30)  # opened: 30 days beats the supplier expiry
        inventory.adjust_stock(s, admin, lot_id, "40", "Stock check")
    with db.session() as s:
        [(item, on_hand)] = inventory.low_stock(s)
        assert on_hand == 40
        assert [t.kind for t in inventory.transactions(s, lot_id)] == ["Received", "Adjusted"]
        inventory.discard_lot(s, admin, lot_id, "Container damaged")
    with db.session() as s:
        lot = inventory.get_lot(s, lot_id)
        assert lot.status == LotStatus.DISCARDED and lot.quantity_on_hand == "0"
        assert inventory.lot_problems(s, lot)


def test_reference_standard_needs_purity(db, admin):
    with db.session() as s:
        item = inventory.create_item(s, admin, ItemDetails(name="RS", kind=ItemKind.REFERENCE_STANDARD, unit="mg"))
        with pytest.raises(ValidationError, match="purity"):
            inventory.receive_lot(s, admin, item.id, lot_number="A", quantity="10")
        with pytest.raises(ValidationError, match="purity"):
            inventory.receive_lot(s, admin, item.id, lot_number="A", quantity="10", purity="120")


# ----- requirements and usage --------------------------------------------------

REQUIREMENTS = [R("instrument", "HPLC", "HPLC system"),
                R("material", "Reference standard", "Aurumex reference standard", fills_parameter="purity_std")]


def test_requirement_validation():
    params = methods.validate_parameters(ASSAY_PARAMS)
    with pytest.raises(ValidationError, match="number input"):
        methods.validate_requirements([R("material", "Reference standard", "RS", fills_parameter="assay")], params)
    with pytest.raises(ValidationError, match="only a reference standard"):
        methods.validate_requirements([R("instrument", "HPLC", "x", fills_parameter="purity_std")], params)
    with pytest.raises(ValidationError, match="instrument category"):
        methods.validate_requirements([R("instrument", "Toaster", "x")], params)


@pytest.fixture
def equipped(db, admin, setup):  # noqa: F811
    """The HPLC assay method re-versioned with requirements, plus an HPLC and a standard lot."""
    with db.session() as s:
        v2 = methods.new_version(s, admin, setup["assay"]).id
    with db.session() as s:
        methods.update_draft(s, admin, v2, name="Assay by HPLC", parameters=ASSAY_PARAMS, requirements=REQUIREMENTS)
    with db.session() as s:
        assert methods.activate(s, setup["qa"], v2, USER_PASSWORD).ok
    with db.session() as s:
        testing.set_specification(s, setup["analyst"], setup["sample"], setup["spec"])
        analysis = testing.assign_tests(s, setup["analyst"], setup["sample"], [v2])[0].id
        reqs = {r.kind: r.id for r in methods.requirements_of(s, v2)}
    return {**setup, "method": v2, "analysis": analysis, "reqs": reqs,
            "hplc": _instrument(db, admin), "lot": _standard_lot(db, admin)}


def test_cannot_sign_without_requirements(db, equipped):
    values = {k: v for k, v in ASSAY_VALUES.items() if k[0] != "purity_std"}
    with db.session() as s:
        testing.save_values(s, equipped["analyst"], equipped["analysis"], values)
        with pytest.raises(ValidationError, match="filled in automatically"):
            testing.save_values(s, equipped["analyst"], equipped["analysis"], {("purity_std", 1): "99.8"})
    with db.session() as s:
        with pytest.raises(ValidationError, match="Record the HPLC system"):
            testing.complete_analysis(s, equipped["analyst"], equipped["analysis"], USER_PASSWORD)


def test_full_usage_flow_fills_purity_and_consumes_stock(db, equipped):
    a, analyst = equipped["analysis"], equipped["analyst"]
    values = {k: v for k, v in ASSAY_VALUES.items() if k[0] != "purity_std"}
    with db.session() as s:
        testing.save_values(s, analyst, a, values)
        usage.record_instrument(s, analyst, a, equipped["hplc"], equipped["reqs"]["instrument"])
        usage.record_lot(s, analyst, a, equipped["lot"], quantity_used="25.12",
                         requirement_id=equipped["reqs"]["material"])
    with db.session() as s:
        assert testing.stored_values(s, a)[("purity_std", 1)] == "99.8"  # from the lot's certificate
        assert inventory.get_lot(s, equipped["lot"]).quantity_on_hand == "74.88"
        assert usage.readiness_problems(s, s.get(testing.Analysis, a)) == []
        assert testing.complete_analysis(s, analyst, a, USER_PASSWORD).ok
    with db.session() as s:
        assert testing.reportable_results(s, a)[0].reported_value == "100.0"
        with pytest.raises(ValidationError, match="no longer be changed"):
            usage.remove_usage(s, analyst, usage.usages_for(s, a)[0].id, "x")


def test_bad_instrument_or_lot_refused(db, admin, equipped):
    balance = _instrument(db, admin, "BAL-01", "Balance")
    late = _instrument(db, admin, "HPLC-02", "HPLC", cal_due=TODAY - timedelta(days=3))
    expired = _standard_lot(db, admin, expiry=TODAY - timedelta(days=1))
    a, analyst = equipped["analysis"], equipped["analyst"]
    with db.session() as s:
        with pytest.raises(ValidationError, match="must be a HPLC"):
            usage.record_instrument(s, analyst, a, balance, equipped["reqs"]["instrument"])
        with pytest.raises(ValidationError, match="calibration was due"):
            usage.record_instrument(s, analyst, a, late, equipped["reqs"]["instrument"])
        with pytest.raises(ValidationError, match="expired"):
            usage.record_lot(s, analyst, a, expired, requirement_id=equipped["reqs"]["material"])
        with pytest.raises(ValidationError, match="Not enough stock"):
            usage.record_lot(s, analyst, a, equipped["lot"], quantity_used="1000",
                             requirement_id=equipped["reqs"]["material"])


def test_remove_usage_returns_stock_and_clears_purity(db, equipped):
    a, analyst = equipped["analysis"], equipped["analyst"]
    with db.session() as s:
        usage.record_lot(s, analyst, a, equipped["lot"], quantity_used="10",
                         requirement_id=equipped["reqs"]["material"])
    with db.session() as s:
        [row] = usage.usages_for(s, a)
        with pytest.raises(ValidationError, match="reason"):
            usage.remove_usage(s, analyst, row.id, "")
        usage.remove_usage(s, analyst, row.id, "Wrong lot scanned")
    with db.session() as s:
        assert inventory.get_lot(s, equipped["lot"]).quantity_on_hand == "100"
        assert ("purity_std", 1) not in testing.stored_values(s, a)
        assert [t.kind for t in inventory.transactions(s, equipped["lot"])] == ["Received", "Used", "Returned"]


def test_instrument_going_overdue_blocks_signing(db, admin, equipped):
    a, analyst = equipped["analysis"], equipped["analyst"]
    values = {k: v for k, v in ASSAY_VALUES.items() if k[0] != "purity_std"}
    with db.session() as s:
        testing.save_values(s, analyst, a, values)
        usage.record_instrument(s, analyst, a, equipped["hplc"], equipped["reqs"]["instrument"])
        usage.record_lot(s, analyst, a, equipped["lot"], requirement_id=equipped["reqs"]["material"])
    with db.session() as s:  # the instrument fails a check before the analyst signs
        instruments.record_event(s, admin, equipped["hplc"], event_type=EventType.PERFORMANCE_CHECK,
                                 performed_on=TODAY, result="Fail", notes="Leak")
    with db.session() as s:
        with pytest.raises(ValidationError, match="out of service"):
            testing.complete_analysis(s, analyst, a, USER_PASSWORD)


def test_viewer_cannot_record_usage(db, equipped, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            usage.record_instrument(s, viewer, equipped["analysis"], equipped["hplc"])
