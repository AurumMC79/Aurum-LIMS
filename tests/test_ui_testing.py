"""Smoke tests for the Phase 3 screens (methods, specs, assigning, result entry, OOS)."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLineEdit

from aurum_lims.services import testing
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.testing_dialogs import AssignTestsDialog, ResultEntryDialog
from aurum_lims.ui.views.methods import MethodEditorDialog, ParameterTable
from aurum_lims.ui.views.oos import OOSView
from aurum_lims.ui.views.specifications import SpecificationsView
from aurum_lims.ui.views.testing import TestingView
from tests.test_testing import ASSAY_PARAMS, ASSAY_VALUES, setup  # noqa: F401 - fixture


def test_parameter_table_round_trip(qtbot):
    table = ParameterTable(ASSAY_PARAMS)
    qtbot.addWidget(table)
    params = table.parameters()
    assert [p.key for p in params] == [p.key for p in ASSAY_PARAMS]
    assert params[-1].formula == ASSAY_PARAMS[-1].formula
    assert params[3].replicates == 2
    table.selectRow(5)
    table.move(-1)
    assert table.parameters()[4].key == "assay"


def test_method_editor_validates(qtbot):
    dialog = MethodEditorDialog()
    qtbot.addWidget(dialog)
    dialog.code.setText("PH")
    dialog.name.setText("pH")
    dialog._check()
    assert dialog.result() != QDialog.Accepted and "key" in dialog.error.text()


def test_qa_sees_master_data_pages(qtbot, db, setup):  # noqa: F811
    window = MainWindow(db, setup["qa"])
    qtbot.addWidget(window)
    titles = [p.title for p in window.pages]
    assert {"Testing", "OOS", "Methods", "Specifications"} <= set(titles)
    for title in titles:
        assert window.show_page(title)
    window.sign_out("test")


def test_assign_and_enter_results_dialogs(qtbot, db, setup, monkeypatch):  # noqa: F811
    dialog = AssignTestsDialog(db, setup["analyst"], setup["sample"])
    qtbot.addWidget(dialog)
    assert dialog.spec.currentData() == setup["spec"]  # suggested because the material matches
    dialog._tick_from_spec()
    ticked = [dialog.method_list.item(i).checkState() == Qt.Checked for i in range(dialog.method_list.count())]
    assert all(ticked) and len(ticked) == 2
    dialog._submit()
    assert dialog.result() == QDialog.Accepted, dialog.error.text()

    view = TestingView(db, setup["analyst"])
    qtbot.addWidget(view)
    view.refresh()
    assert view.table.rowCount() == 2
    assay_row = next(r for r in view.rows if r.method_code == "HPLC-ASSAY")

    entry = ResultEntryDialog(db, setup["analyst"], assay_row.id)
    qtbot.addWidget(entry)
    for key, text in ASSAY_VALUES.items():
        widget = entry.fields[key]
        assert isinstance(widget, QLineEdit)
        widget.setText(text)
    # Live calculation, before anything is saved.
    assert entry.summary.item(0, 4).text() == "100.0"
    assert entry.summary.item(0, 6).text() == "Pass"
    assert entry.complete_button.isEnabled()
    assert entry._save()
    with db.session() as s:
        assert testing.stored_values(s, assay_row.id)[("wt_spl", 2)] == "25.05"


def test_oos_and_spec_views_build(qtbot, db, setup):  # noqa: F811
    for view_class in (OOSView, SpecificationsView):
        view = view_class(db, setup["qa"])
        qtbot.addWidget(view)
        view.refresh()
