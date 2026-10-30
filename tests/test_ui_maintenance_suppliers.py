"""Maintenance plans on the Instruments page and the Suppliers tab on the Inventory page."""

from datetime import date, timedelta

from PySide6.QtWidgets import QDialog, QMessageBox

from aurum_lims.db.models import InventoryLot
from aurum_lims.security.roles import Role
from aurum_lims.services import inventory, maintenance, suppliers
from aurum_lims.services.inventory import ItemDetails, ItemKind
from aurum_lims.services.maintenance import PlanSpec, TaskSpec
from aurum_lims.services.suppliers import SupplierDetails
from aurum_lims.ui import maintenance_dialogs
from aurum_lims.ui.maintenance_dialogs import PerformDialog, PlanDialog
from aurum_lims.ui.views.instruments import InstrumentsView
from aurum_lims.ui.views.inventory import InventoryView, ItemDialog, ReceiveLotDialog
from tests.test_equipment import _instrument

TODAY = date.today()


def test_plan_dialog_and_perform(qtbot, db, admin, monkeypatch):
    hplc = _instrument(db, admin)
    dialog = PlanDialog(None, "HPLC-01")
    qtbot.addWidget(dialog)
    dialog.name.setText("Monthly PM")
    dialog.tasks.cellWidget(0, 0).setText("Replace seals")
    dialog._add(TaskSpec("Lamp energy", True, "≥ 1500"))
    dialog.tasks.selectRow(1)
    dialog._move(-1)
    spec = dialog.spec()
    assert [t.text for t in spec.tasks] == ["Lamp energy", "Replace seals"] and spec.tasks[0].needs_value
    assert spec.first_due == TODAY + timedelta(days=30)
    with db.session() as s:
        plan_id = maintenance.create_plan(s, admin, hplc, spec).id

    view = InstrumentsView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    view.table.selectRow(0)
    panel = view.plans_panel
    assert panel.table.rowCount() == 1 and panel.table.item(0, 1).text() == "30 days"
    panel.table.selectRow(0)

    perform = PerformDialog(None, "HPLC-01", "Monthly PM", spec.tasks)
    qtbot.addWidget(perform)
    perform.table.cellWidget(0, 2).setCurrentText("OK")
    perform.table.cellWidget(0, 3).setText("1688")
    assert "tasks marked" in perform.result_label.text()
    perform.table.cellWidget(1, 2).setCurrentText("OK")
    assert "Pass" in perform.result_label.text()

    class Filled(PerformDialog):  # the dialog as the user completed it
        def exec(self):
            for r in range(self.table.rowCount()):
                self.table.cellWidget(r, 2).setCurrentText(perform.table.cellWidget(r, 2).currentText())
                self.table.cellWidget(r, 3).setText(perform.table.cellWidget(r, 3).text())
            return QDialog.Accepted

    monkeypatch.setattr(maintenance_dialogs, "PerformDialog", Filled)
    panel._perform()
    with db.session() as s:
        assert s.get(maintenance.MaintenancePlan, plan_id).last_done == TODAY
    view.lower_tabs.setCurrentIndex(0)
    assert view.history.item(0, 1).text() == "Maintenance – Monthly PM"
    assert view.events[0].checklist[0]["value"] == "1688"


def test_suppliers_tab_and_receiving(qtbot, db, admin, make_user, monkeypatch):
    qa = make_user("qa1", Role.QA_REVIEWER)
    with db.session() as s:
        supplier_id = suppliers.create_supplier(s, admin, SupplierDetails("New Standards Co")).id
        item_id = inventory.create_item(s, admin, ItemDetails(name="Caffeine CRS", kind=ItemKind.SOLVENT, unit="mg",
                                                              supplier_id=supplier_id)).id
    view = InventoryView(db, qa)
    qtbot.addWidget(view)
    view.refresh()
    assert view.suppliers_tab.table.rowCount() == 1
    assert view.suppliers_tab.table.item(0, 1).text() == "Under evaluation"
    assert not view.suppliers_tab.new_button.isHidden()

    # The item editor offers the supplier list; receiving defaults to the item's supplier and warns.
    with db.session() as s:
        item = inventory.get_item(s, item_id)
    editor = ItemDialog(None, item, view._supplier_choices())
    qtbot.addWidget(editor)
    assert editor.details().supplier_id == supplier_id and editor.details().supplier == "New Standards Co"
    receive = ReceiveLotDialog(view.active_items, view.locations, None, item_id, view.supplier_rows)
    qtbot.addWidget(receive)
    assert receive.supplier.currentData() == supplier_id
    assert receive.concerns() == ["New Standards Co is not approved yet (under evaluation)"]

    answers = iter([QMessageBox.No, QMessageBox.Yes])
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: next(answers))

    class Filled(ReceiveLotDialog):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.lot_number.setText("CRS-7")
            self.quantity.setText("200")

    import aurum_lims.ui.views.inventory as inventory_view
    monkeypatch.setattr(inventory_view, "ReceiveLotDialog", Filled)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.Accepted)
    view._receive()  # first answer No -> asked again, second Yes -> received
    with db.session() as s:
        [lot] = s.query(InventoryLot).all()
        assert lot.supplier_id == supplier_id and lot.lot_number == "CRS-7"
