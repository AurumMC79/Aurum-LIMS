"""Smoke tests for the Phase 5 screens."""

from PySide6.QtWidgets import QDialog

from aurum_lims.services import testing, usage
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.testing_dialogs import ResultEntryDialog
from aurum_lims.ui.usage_widgets import RecordUsageDialog
from aurum_lims.ui.views.instruments import EventDialog, InstrumentsView
from aurum_lims.ui.views.inventory import InventoryView
from aurum_lims.ui.views.methods import MethodEditorDialog
from tests.test_equipment import ASSAY_VALUES, REQUIREMENTS, equipped  # noqa: F401 - fixtures
from tests.test_testing import ASSAY_PARAMS, setup  # noqa: F401 - fixture


def test_method_editor_requirements_round_trip(qtbot):
    dialog = MethodEditorDialog(None, None, ASSAY_PARAMS, requirements=REQUIREMENTS)
    qtbot.addWidget(dialog)
    reqs = dialog.requirements.requirements()
    assert [(r.kind, r.category, r.fills_parameter) for r in reqs] == [
        ("instrument", "HPLC", None), ("material", "Reference standard", "purity_std")]
    dialog.code.setText("HPLC-ASSAY-02")
    dialog.name.setText("Assay")
    dialog._check()
    assert dialog.result() == QDialog.Accepted, dialog.error.text()


def test_result_entry_with_equipment(qtbot, db, equipped):  # noqa: F811
    a = equipped["analysis"]
    entry = ResultEntryDialog(db, equipped["analyst"], a)
    qtbot.addWidget(entry)
    assert ("purity_std", 1) not in entry.fields  # filled from the lot, not typed
    assert entry.usage_table.rowCount() == 2 and entry.usage_table.item(0, 3).text() == "Missing"
    for key, value in ASSAY_VALUES.items():
        if key in entry.fields:
            entry.fields[key].setText(value)
    assert not entry.complete_button.isEnabled()
    assert "record the HPLC system used" in entry.problems.text()

    dialog = RecordUsageDialog(db, equipped["analyst"], a, equipped["method"], "instrument")
    qtbot.addWidget(dialog)
    dialog._submit()
    assert dialog.result() == QDialog.Accepted, dialog.error.text()
    lot_dialog = RecordUsageDialog(db, equipped["analyst"], a, equipped["method"], "material")
    qtbot.addWidget(lot_dialog)
    lot_dialog.quantity.setText("25")
    lot_dialog._submit()
    assert lot_dialog.result() == QDialog.Accepted, lot_dialog.error.text()

    assert entry._save()
    entry._load()
    assert entry.usage_table.item(0, 3).text() == "OK" and entry.usage_table.item(1, 3).text() == "OK"
    assert entry.summary.item(0, 4).text() == "100.0"
    assert entry.complete_button.isEnabled()
    with db.session() as s:
        assert usage.readiness_problems(s, s.get(testing.Analysis, a)) == []


def test_instrument_and_inventory_pages(qtbot, db, equipped):  # noqa: F811
    view = InstrumentsView(db, equipped["qa"])
    qtbot.addWidget(view)
    view.refresh()
    assert view.table.rowCount() == 1 and view.table.item(0, 7).text() == "Yes"
    view.table.selectRow(0)
    event = EventDialog(view._selected())
    qtbot.addWidget(event)
    assert event.values()["event_type"].value == "Calibration"

    inv = InventoryView(db, equipped["analyst"])
    qtbot.addWidget(inv)
    inv.refresh()
    assert inv.lot_table.rowCount() == 1 and inv.item_table.rowCount() == 1
    inv.lot_table.selectRow(0)
    assert inv.tx_table.rowCount() == 1  # the 'Received' movement


def test_navigation_includes_equipment(qtbot, db, equipped):  # noqa: F811
    window = MainWindow(db, equipped["analyst"])
    qtbot.addWidget(window)
    titles = [p.title for p in window.pages]
    assert {"Instruments", "Inventory"} <= set(titles)
    for title in titles:
        assert window.show_page(title)
    window.sign_out("test")
