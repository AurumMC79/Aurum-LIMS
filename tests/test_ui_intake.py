"""Sample Intaking page and the form designer."""

from PySide6.QtWidgets import QDialog, QMessageBox

from aurum_lims.db.models import Sample
from aurum_lims.security.roles import Role
from aurum_lims.services import intake, sample_types, storage
from aurum_lims.services.intake import FieldSpec, FieldType
from aurum_lims.ui import intake_designer
from aurum_lims.ui.intake_designer import FormDesignerDialog
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.views.intake import IntakeView


def _setup(db, admin):
    with db.session() as s:
        sample_types.seed_defaults(s)
        storage.create_location(s, admin, name="Fridge 1", kind="Refrigerator")


def test_designer_adds_fields_and_saves_a_version(qtbot, db, admin, monkeypatch):
    _setup(db, admin)
    view = IntakeView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    dialog = FormDesignerDialog(db, admin, view.type_choices, view.location_choices)
    qtbot.addWidget(dialog)
    dialog.list.setCurrentRow(1)
    dialog._add(FieldSpec("", "New field", FieldType.TEXT.value))
    dialog.label_edit.setText("PO number")
    dialog.label_edit.textEdited.emit("PO number")
    dialog.required.setChecked(True)
    added = dialog.fields[2]
    assert (added.key, added.label, added.required) == ("c_new_field", "PO number", True)
    dialog.list.setCurrentRow(1)  # sample type cannot be removed or made optional
    dialog._remove()
    assert "must stay" in dialog.error.text() and not dialog.required.isEnabled()
    monkeypatch.setattr(intake_designer, "ask_reason", lambda *a, **k: "Added PO number")
    dialog._save()
    assert dialog.result() == QDialog.Accepted
    with db.session() as s:
        row, fields = intake.current_form(s)
        assert row.version == 1 and fields[2].label == "PO number" and row.change_note == "Added PO number"


def test_intake_page_logs_a_sample_in(qtbot, db, admin, monkeypatch):
    _setup(db, admin)
    view = IntakeView(db, admin)
    qtbot.addWidget(view)
    view.refresh()
    assert "built-in starting form" in view.version_label.text() and not view.design_button.isHidden()
    form = view.form_widget
    form.widgets["sample_type"].setCurrentIndex(form.widgets["sample_type"].findText("RM – Raw Material"))
    form.widgets["material_name"].setText("Microcrystalline cellulose")
    form.widgets["lot_number"].setText("MCC-501")
    form.widgets["location_id"].setCurrentIndex(1)
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: shown.append(a[2]))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    view._submit()
    assert shown and "1 sample(s) created" in shown[-1]
    assert view.recent.rowCount() == 1 and view.recent.item(0, 4).text() == "Received"
    assert form.widgets["material_name"].text() == ""  # cleared for the next sample
    with db.session() as s:
        assert s.query(Sample).one().lot_number == "MCC-501"


def test_page_visibility(qtbot, db, admin, make_user):
    _setup(db, admin)
    for user, visible in ((admin, True), (make_user("ann", Role.ANALYST), True),
                          (make_user("viewer1", Role.VIEWER), False)):
        window = MainWindow(db, user)
        qtbot.addWidget(window)
        window._signing_out = True
        assert ("Sample Intaking" in {p.title for p in window.pages}) == visible
