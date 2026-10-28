from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.db.models import utcnow
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import audit, samples, sample_types, storage
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.samples import CustodyEventType, Receipt, SampleDetails, SampleStatus
from aurum_lims.services.storage import LocationKind
from tests.conftest import USER_PASSWORD


@pytest.fixture
def lab(db, admin, make_user):
    """Seeded sample types, two storage locations and an analyst."""
    with db.session() as s:
        sample_types.seed_defaults(s)
    with db.session() as s:
        room = storage.create_location(s, admin, name="QC Lab", kind=LocationKind.ROOM)
        fridge = storage.create_location(s, admin, name="Fridge 1", kind=LocationKind.REFRIGERATOR,
                                         parent_id=room.id, condition="2-8 °C")
        shelf = storage.create_location(s, admin, name="Retain shelf", kind=LocationKind.SHELF, parent_id=room.id)
        types = {t.code: t.id for t in sample_types.list_types(s)}
    analyst = make_user("jsmith", Role.ANALYST)
    return {"types": types, "fridge": fridge.id, "shelf": shelf.id, "room": room.id, "analyst": analyst}


RM = SampleDetails(material_name="Sodium chloride", lot_number="L2026-114", supplier="ChemCo",
                   quantity="500", quantity_unit="g")


def register(db, lab, details=RM, count=1, receipt=None, code="RM"):
    with db.session() as s:
        created = samples.register_samples(s, lab["analyst"], lab["types"][code], details, count=count,
                                           receipt=receipt)
        return [(c.id, c.sample_id) for c in created]


def test_register_assigns_ids_and_custody(db, lab):
    created = register(db, lab, count=3)
    year = utcnow().astimezone().year
    assert [sid for _, sid in created] == [f"AU-{year}-0000{n}" for n in (1, 2, 3)]
    with db.session() as s:
        history = samples.custody_history(s, lab["analyst"], created[0][0])
        assert [e.event for e in history] == [CustodyEventType.REGISTERED]
        assert s.get(samples.Sample, created[0][0]).status == SampleStatus.REGISTERED


def test_required_fields_depend_on_category(db, lab):
    with pytest.raises(ValidationError, match="Lot / batch number"):
        register(db, lab, SampleDetails(material_name="NaCl"))
    with pytest.raises(ValidationError, match="Stability study"):
        register(db, lab, SampleDetails(material_name="Tablet X", lot_number="B1"), code="STB")
    with pytest.raises(ValidationError, match="Sampling point"):
        register(db, lab, SampleDetails(), code="EM")
    register(db, lab, SampleDetails(sampling_point="Cleanroom 2, grade B"), code="EM")


def test_quantity_validation(db, lab):
    with pytest.raises(ValidationError, match="number"):
        register(db, lab, SampleDetails(material_name="A", lot_number="1", quantity="lots", quantity_unit="g"))
    with pytest.raises(ValidationError, match="unit"):
        register(db, lab, SampleDetails(material_name="A", lot_number="1", quantity="5"))


def test_viewer_cannot_register(db, lab, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            samples.register_samples(s, viewer, lab["types"]["RM"], RM)


def test_receive_and_transfer(db, lab, admin):
    [(pk, sid)] = register(db, lab)
    with db.session() as s:
        samples.receive_sample(s, lab["analyst"], pk, Receipt(location_id=lab["fridge"], temperature="4 °C"))
    with db.session() as s:
        with pytest.raises(ValidationError, match="already been received"):
            samples.receive_sample(s, lab["analyst"], pk, Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        samples.transfer_sample(s, lab["analyst"], pk, to_holder_id=lab["analyst"].id, notes="For assay")
    with db.session() as s:
        samples.transfer_sample(s, lab["analyst"], pk, to_location_id=lab["shelf"])
    with db.session() as s:
        sample = s.get(samples.Sample, pk)
        assert sample.status == SampleStatus.RECEIVED
        assert sample.location_id == lab["shelf"] and sample.holder_id is None
        events = [e.event for e in samples.custody_history(s, lab["analyst"], pk)]
        assert events == ["Registered", "Received", "Transferred", "Transferred"]
        actions = [e.action for e in audit.query_entries(s, admin, search=sid)]
        assert AuditAction.SAMPLE_TRANSFERRED in actions and AuditAction.SAMPLE_RECEIVED in actions


def test_register_with_immediate_receipt(db, lab):
    [(pk, _)] = register(db, lab, receipt=Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        assert s.get(samples.Sample, pk).status == SampleStatus.RECEIVED


def test_receipt_rules(db, lab):
    [(pk, _)] = register(db, lab)
    with db.session() as s:
        with pytest.raises(ValidationError, match="notes"):
            samples.receive_sample(s, lab["analyst"], pk, Receipt(location_id=lab["fridge"], condition="Damaged container"))
        with pytest.raises(ValidationError, match="future"):
            samples.receive_sample(s, lab["analyst"], pk, Receipt(location_id=lab["fridge"],
                                                                  received_at=utcnow() + timedelta(hours=2)))


def test_transfer_rules(db, lab):
    [(pk, _)] = register(db, lab)
    with db.session() as s:
        with pytest.raises(ValidationError, match="cannot be moved"):
            samples.transfer_sample(s, lab["analyst"], pk, to_location_id=lab["shelf"])
    with db.session() as s:
        samples.receive_sample(s, lab["analyst"], pk, Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        with pytest.raises(ValidationError, match="either"):
            samples.transfer_sample(s, lab["analyst"], pk)
        with pytest.raises(ValidationError, match="already in that location"):
            samples.transfer_sample(s, lab["analyst"], pk, to_location_id=lab["fridge"])


def test_aliquots(db, lab):
    [(pk, sid)] = register(db, lab, receipt=Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        kids = samples.create_aliquots(s, lab["analyst"], pk, count=2, location_id=lab["shelf"],
                                       quantity="10", quantity_unit="g")
        assert [k.sample_id for k in kids] == [f"{sid}-A1", f"{sid}-A2"]
    with db.session() as s:
        kids = samples.create_aliquots(s, lab["analyst"], pk, count=1, location_id=lab["shelf"])
        assert kids[0].sample_id == f"{sid}-A3"
        assert kids[0].material_name == "Sodium chloride"
        found = samples.search(s, lab["analyst"], text=sid)
        assert {r.sample_id for r in found} == {sid, f"{sid}-A1", f"{sid}-A2", f"{sid}-A3"}


def test_dispose_requires_signature(db, lab):
    [(pk, sid)] = register(db, lab, receipt=Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        result = samples.dispose_sample(s, lab["analyst"], pk, method="Consumed in testing", password="wrong-pass-1")
    assert not result.ok
    with db.session() as s:
        assert s.get(samples.Sample, pk).status == SampleStatus.RECEIVED
    with db.session() as s:
        result = samples.dispose_sample(s, lab["analyst"], pk, method="Consumed in testing", password=USER_PASSWORD)
    assert result.ok
    with db.session() as s:
        sample = s.get(samples.Sample, pk)
        assert sample.status == SampleStatus.DISPOSED and sample.location_id is None
        with pytest.raises(ValidationError, match="already Disposed"):
            samples.dispose_sample(s, lab["analyst"], pk, method="Other", password=USER_PASSWORD, notes="x")


def test_cancel_only_before_receipt(db, lab):
    [(pk, _)] = register(db, lab)
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            samples.cancel_sample(s, lab["analyst"], pk, "")
        samples.cancel_sample(s, lab["analyst"], pk, "Shipment never arrived")
    [(pk2, _)] = register(db, lab, receipt=Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        with pytest.raises(ValidationError, match="Only samples"):
            samples.cancel_sample(s, lab["analyst"], pk2, "test")
        open_ids = {r.id for r in samples.search(s, lab["analyst"])}
        assert pk not in open_ids and pk2 in open_ids
        assert pk in {r.id for r in samples.search(s, lab["analyst"], include_closed=True)}


def test_correct_details_audited(db, lab, admin):
    [(pk, sid)] = register(db, lab)
    with db.session() as s:
        details = samples.details_of(s.get(samples.Sample, pk))
        details.lot_number = "L2026-115"
        with pytest.raises(ValidationError, match="reason"):
            samples.update_details(s, lab["analyst"], pk, details, "")
        samples.update_details(s, lab["analyst"], pk, details, "Typo in lot number")
    with db.session() as s:
        entry = audit.query_entries(s, admin, action=AuditAction.SAMPLE_UPDATED)[0]
        assert "L2026-114" in entry.old_values and "L2026-115" in entry.new_values
        assert entry.reason == "Typo in lot number"


def test_custody_and_samples_are_protected(db, lab):
    [(pk, _)] = register(db, lab)
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE custody_events SET notes = 'edited'"))
    with pytest.raises(DBAPIError, match="cannot be deleted"):
        with db.engine.begin() as conn:
            conn.execute(text("DELETE FROM samples"))


def test_storage_rules(db, lab, admin):
    [(pk, _)] = register(db, lab, receipt=Receipt(location_id=lab["fridge"]))
    with db.session() as s:
        with pytest.raises(ValidationError, match="Move the samples"):
            storage.set_active(s, admin, lab["fridge"], False, "Decommissioned")
        with pytest.raises(ValidationError, match="locations inside"):
            storage.set_active(s, admin, lab["room"], False, "Closed")
        with pytest.raises(ValidationError, match="inside itself"):
            storage.update_location(s, admin, lab["room"], reason="x", name="QC Lab", kind="Room",
                                    parent_id=lab["fridge"], condition=None)
        with pytest.raises(ValidationError, match="already a location"):
            storage.create_location(s, admin, name="fridge 1", kind="Refrigerator", parent_id=lab["room"])
        assert storage.paths(s)[lab["fridge"]] == "QC Lab › Fridge 1"
        assert storage.sample_counts(s)[lab["fridge"]] == 1
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            storage.create_location(s, lab["analyst"], name="Bench", kind="Other")
