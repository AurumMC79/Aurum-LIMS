"""Barcode scanning on Sample Intaking: reading codes, the scan settings and filling the form."""

import json
from dataclasses import replace
from datetime import date

import pytest
from PySide6.QtWidgets import QMessageBox
from sqlalchemy import text

from aurum_lims.errors import ValidationError
from aurum_lims.services import intake, sample_types, storage
from aurum_lims.services.barcodes import GS, ScanSettings, parse_date_text, parse_gs1, read_scan
from aurum_lims.services.intake import FieldSpec, FieldType, builtin_field
from aurum_lims.ui.views.intake import IntakeView

SETTINGS = ScanSettings(True, "auto", {"01": "c_gtin", "10": "lot_number", "17": "c_expiry", "3100": "quantity"},
                        "|", ["sample_type", "material_name", "lot_number"], "lot_number", "^")


# ----- reading codes ---------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "(01)09501101530003(17)261231(10)ABC123",            # as printed under the barcode
    f"010950110153000317261231" f"10ABC123",            # raw, variable field last
    f"0109501101530003" f"10ABC123{GS}17261231",         # raw, GS after the variable field
    "]d20109501101530003" "10ABC123^17261231",           # DataMatrix identifier and a GS substitute
])
def test_gs1_forms(code):
    result = read_scan(SETTINGS, code)
    assert result.kind == "GS1"
    assert result.values == {"c_gtin": "09501101530003", "lot_number": "ABC123", "c_expiry": date(2026, 12, 31)}


def test_gs1_details():
    assert parse_gs1("(01)123") is None  # GTIN must be 14 digits
    assert parse_gs1("hello") is None
    assert read_scan(SETTINGS, "(17)270200").values["c_expiry"] == date(2027, 2, 28)  # day 00 = end of month
    assert read_scan(SETTINGS, "(3100)000025").values["quantity"] == "25"
    assert parse_gs1("(3102)001250") == {"3102": "001250"}
    from aurum_lims.services.barcodes import gs1_value
    assert gs1_value("3102", "001250") == "12.50"
    result = read_scan(SETTINGS, "(01)09501101530003(21)SN-77")
    assert result.unused == ["(21) Serial number"] and ("(21) Serial number", "SN-77") in result.found


def test_separated_and_single_values():
    result = read_scan(SETTINGS, "RM|Lactose monohydrate|LAC-2210|extra")
    assert result.kind == "separated values" and result.unused == ["Position 4"]
    assert result.values == {"sample_type": "RM", "material_name": "Lactose monohydrate", "lot_number": "LAC-2210"}
    assert read_scan(SETTINGS, "LOT-9").values == {"lot_number": "LOT-9"}
    assert read_scan(replace(SETTINGS, mode="gs1"), "LOT-9").kind == "not recognised"
    assert read_scan(replace(SETTINGS, single_field=""), "LOT-9").kind == "not recognised"
    tab = replace(SETTINGS, mode="delimited", separator="\t")
    assert read_scan(tab, "FP\tTablets\tB1").values["material_name"] == "Tablets"


def test_dates_in_text():
    for value in ("2026-12-31", "31.12.2026", "31/12/2026", "20261231", "261231", "31-Dec-2026"):
        assert parse_date_text(value) == date(2026, 12, 31), value
    assert parse_date_text("soon") is None


# ----- settings saved with the form ---------------------------------------------------

def _fields():
    return [builtin_field("sample_type"), builtin_field("material_name"), builtin_field("lot_number"),
            builtin_field("quantity"), builtin_field("quantity_unit"),
            FieldSpec("c_gtin", "GTIN", FieldType.TEXT), FieldSpec("c_expiry", "Supplier expiry", FieldType.DATE),
            builtin_field("location_id")]


def test_scan_settings_are_versioned_with_the_form(db, admin):
    with db.session() as s:
        assert intake.current_scan(s).enabled is False
        intake.save_form(s, admin, _fields(), "Form with barcode scanning", SETTINGS)
    with db.session() as s:
        assert intake.current_scan(s) == SETTINGS
        with pytest.raises(ValidationError, match="Nothing"):
            intake.save_form(s, admin, _fields(), "same", SETTINGS)
        intake.save_form(s, admin, _fields(), "Scanning off", replace(SETTINGS, enabled=False))
        with pytest.raises(ValidationError, match="no longer on the form"):
            intake.save_form(s, admin, [f for f in _fields() if f.key != "c_gtin"], "Removed GTIN")
    with db.session() as s:
        assert intake.current_scan(s).enabled is False and intake.history(s)[0].fields == 8


def test_versions_saved_before_scanning_still_load(db, admin):
    with db.session() as s:
        s.execute(text("INSERT INTO intake_form_versions (version, fields, change_note, created_at, created_by_id) "
                       "VALUES (1, :f, 'old', '2026-10-29 10:00:00', 1)"),
                  {"f": json.dumps([{"key": "sample_type", "label": "Sample type", "type": "dropdown",
                                     "builtin": True, "required": True}])})
    with db.session() as s:
        row, fields = intake.current_form(s)
        assert row.version == 1 and fields[0].key == "sample_type" and intake.current_scan(s) == ScanSettings()


# ----- scanning on the Sample Intaking page -------------------------------------------

def test_scan_box_fills_the_form_and_is_recorded(qtbot, db, admin, monkeypatch):
    with db.session() as s:
        sample_types.seed_defaults(s)
        storage.create_location(s, admin, name="Fridge 1", kind="Refrigerator")
        intake.save_form(s, admin, _fields(), "Barcode scanning", SETTINGS)
    view = IntakeView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    assert view.scan_card.isVisibleTo(view)
    view.scan_box.setText("RM|Lactose monohydrate|LAC-2210")
    view._scanned()
    view.scan_box.setText(f"0109501101530003" f"10LAC-2210{GS}17261231")
    view._scanned()
    form = view.form_widget
    assert form.widgets["sample_type"].currentText() == "RM – Raw Material"
    assert form.widgets["c_gtin"].text() == "09501101530003"
    assert form.widgets["c_expiry"].check.isChecked() and form.widgets["c_expiry"].editor.date().toPython() == \
        date(2026, 12, 31)
    assert "Filled in" in view.scan_result.text() and view.scan_box.text() == ""
    view.scan_box.setText("ZZ|Nothing|X")  # an unknown sample type does not fit the dropdown
    view._scanned()
    assert "Did not fit – Sample type: ZZ" in view.scan_result.text()
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    view.form_widget.set_value("sample_type", "RM")
    view._submit()
    with db.session() as s:
        record = intake.intake_record(s, 1)
        scanned = dict(record.entries)["Barcode scanned"]
        assert "RM|Lactose monohydrate|LAC-2210" in scanned and "<GS>" in scanned
    assert view.scans == []
