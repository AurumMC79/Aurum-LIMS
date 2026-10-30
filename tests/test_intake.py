"""Sample intaking: the form design, its versions, and submitting samples through it."""

from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import AuditEntry, Sample
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import intake, sample_types, storage
from aurum_lims.services.intake import FieldSpec, FieldType, builtin_field
from aurum_lims.services.samples import SampleStatus


def _type_id(db, code="RM"):
    with db.session() as s:
        sample_types.seed_defaults(s)
        return next(t.id for t in sample_types.list_types(s) if t.code == code)


def _form():
    return [builtin_field("sample_type"), builtin_field("material_name"), builtin_field("lot_number"),
            FieldSpec("c_po_number", "PO number", FieldType.TEXT, required=True),
            FieldSpec("c_cold_chain", "Cold chain intact", FieldType.CHECKBOX),
            FieldSpec("c_grade", "Grade", FieldType.DROPDOWN, choices=["USP", "EP", "ACS"], default="USP"),
            FieldSpec("h_receipt", "Receipt", FieldType.HEADING), builtin_field("location_id"),
            builtin_field("receipt_condition"), builtin_field("receipt_notes"), builtin_field("count")]


def test_starting_form_and_validation(db, admin):
    with db.session() as s:
        row, fields = intake.current_form(s)
        assert row is None and fields[1].key == "sample_type" and fields[1].required
        for form, message in [
            ([builtin_field("material_name")], "sample type"),
            ([builtin_field("sample_type"), builtin_field("sample_type")], "twice"),
            ([builtin_field("sample_type"), FieldSpec("c_x", "X", FieldType.DROPDOWN)], "at least one choice"),
            ([builtin_field("sample_type"), FieldSpec("c_x", " ", FieldType.TEXT)], "label"),
            ([builtin_field("sample_type"), FieldSpec("c_x", "X", FieldType.NUMBER, default="abc")], "number"),
            ([builtin_field("sample_type"), builtin_field("receipt_condition")], "Storage location"),
        ]:
            with pytest.raises(ValidationError, match=message):
                intake.save_form(s, admin, form, "x")
    assert intake.make_key("PO number", set()) == "c_po_number"
    assert intake.make_key("PO number", {"c_po_number"}) == "c_po_number_2"


def test_form_versions_are_kept(db, admin, make_user):
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            intake.save_form(s, make_user("ann", Role.ANALYST), _form(), "x")
        with pytest.raises(ValidationError, match="what changed"):
            intake.save_form(s, admin, _form(), " ")
        assert intake.save_form(s, admin, _form(), "First lab form").version == 1
    with db.session() as s:
        with pytest.raises(ValidationError, match="Nothing"):
            intake.save_form(s, admin, _form(), "same")
        changed = _form()
        changed[3].label = "Purchase order"
        intake.save_form(s, admin, changed, "Renamed PO")
    with db.session() as s:
        row, fields = intake.current_form(s)
        assert row.version == 2 and fields[3].label == "Purchase order"
        assert [h.version for h in intake.history(s)] == [2, 1]
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == "INTAKE_FORM_SAVED")).all()[0]
        assert '"added"' in entry.new_values and entry.reason == "First lab form"
    with db.session() as s, pytest.raises(DBAPIError, match="immutable"):
        s.execute(text("UPDATE intake_form_versions SET change_note = 'x'"))


def test_submit_registers_and_receives(db, admin, make_user):
    analyst = make_user("ann", Role.ANALYST)
    rm = _type_id(db)
    with db.session() as s:
        fridge = storage.create_location(s, admin, name="Fridge 1", kind="Refrigerator").id
        intake.save_form(s, admin, _form(), "Lab form")
    values = {"sample_type": rm, "material_name": "Lactose monohydrate", "lot_number": "L-2210",
              "c_po_number": "PO-7781", "c_cold_chain": True, "c_grade": "EP", "location_id": fridge,
              "receipt_condition": "Acceptable", "count": "2"}
    with db.session() as s:
        with pytest.raises(ValidationError, match="Please fill in: PO number"):
            intake.submit(s, analyst, {**values, "c_po_number": " "})
        with pytest.raises(ValidationError, match="Number of samples"):
            intake.submit(s, analyst, {**values, "count": "1.5"})
        with pytest.raises(ValidationError, match="choose one of the listed"):
            intake.submit(s, analyst, {**values, "c_grade": "Food"})
        with pytest.raises(ValidationError, match="Describe the problem"):  # the normal sample rules still apply
            intake.submit(s, analyst, {**values, "receipt_condition": "Damaged container"})
        created = intake.submit(s, analyst, values)
        ids = [x.id for x in created]
    with db.session() as s:
        first = s.get(Sample, ids[0])
        assert len(ids) == 2 and first.status == SampleStatus.RECEIVED and first.location_id == fridge
        assert first.material_name == "Lactose monohydrate" and first.lot_number == "L-2210"
        record = intake.intake_record(s, ids[1])
        assert record.form_version == 1 and record.submitted_by == "Ann"
        entries = dict(record.entries)
        assert entries["PO number"] == "PO-7781" and entries["Cold chain intact"] == "Yes"
        assert entries["Sample type"] == "RM – Raw Material" and entries["Storage location"] == "Fridge 1"
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == "SAMPLE_INTAKE")).one()
        assert '"PO number": "PO-7781"' in entry.new_values


def test_sample_type_rules_and_no_receipt(db, admin, make_user):
    analyst = make_user("ann", Role.ANALYST)
    viewer = make_user("viewer1", Role.VIEWER)
    rm = _type_id(db)
    with db.session() as s:
        with pytest.raises(ValidationError, match="Material / product"):  # raw materials need a material
            intake.submit(s, analyst, {"sample_type": rm, "lot_number": "L1"})
        [sample] = intake.submit(s, analyst, {
            "sample_type": rm, "material_name": "Talc", "lot_number": "T9", "quantity": "250", "quantity_unit": "g",
            "due_date": date.today() + timedelta(days=5), "priority": "Urgent",
            "sampled_at": datetime.now(timezone.utc) - timedelta(hours=2)})
        assert sample.status == SampleStatus.REGISTERED and sample.priority == "Urgent" and sample.quantity == "250"
    with db.session() as s:
        assert intake.intake_record(s, sample.id).form_version == 0  # the starting form
        with pytest.raises(PermissionDenied):
            intake.submit(s, viewer, {"sample_type": rm})
