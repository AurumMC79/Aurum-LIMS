"""Import template editor, import dialog and filling the result form."""

from PySide6.QtWidgets import QDialog, QMessageBox

from aurum_lims.db.models import Sample
from aurum_lims.services import instrument_import as ii
from aurum_lims.services import testing
from aurum_lims.ui import testing_dialogs
from aurum_lims.ui.import_dialogs import ImportResultsDialog, TemplateEditorDialog
from aurum_lims.ui.testing_dialogs import ResultEntryDialog
from aurum_lims.ui.views.methods import MethodsView
from tests.test_instrument_import import CDS_EXPORT, CDS_TEMPLATE
from tests.test_testing import setup  # noqa: F401 - fixture


def _open_test(db, setup):  # noqa: F811
    with db.session() as s:
        [test] = [a.id for a in testing.assign_tests(s, setup["analyst"], setup["sample"], [setup["assay"]])]
        return test, s.get(Sample, setup["sample"]).sample_id


def test_template_editor_preview_and_save(qtbot, db, setup, admin):  # noqa: F811
    _, sample_id = _open_test(db, setup)
    editor = TemplateEditorDialog(db, admin, "HPLC-ASSAY", spec=ii.TemplateSpec(
        "Empower assay", "HPLC-ASSAY", header_row=4, rules=CDS_TEMPLATE.rules))
    qtbot.addWidget(editor)
    editor.example = CDS_EXPORT.format(sid=sample_id).encode()
    editor.preview_sample.setText(sample_id)
    editor._reparse()
    value_column = editor.rules.cellWidget(0, 1)
    assert [value_column.itemText(i) for i in range(value_column.count())] == \
        ["Sample Name", "Injection", "Area", "Retention Time"]
    assert "<b>10234</b>" in editor.found.text() and "<b>10120</b>" in editor.found.text()
    editor._save()
    assert editor.result() == QDialog.Accepted
    with db.session() as s:
        [row] = ii.templates_for(s, "HPLC-ASSAY")
        assert ii.TemplateSpec.from_row(row).rules == CDS_TEMPLATE.rules


def test_import_fills_the_result_form(qtbot, db, setup, admin, monkeypatch):  # noqa: F811
    test, sample_id = _open_test(db, setup)
    with db.session() as s:
        template_id = ii.create_template(s, admin, CDS_TEMPLATE).id
    data = CDS_EXPORT.format(sid=sample_id).encode()

    entry = ResultEntryDialog(db, setup["analyst"], test)
    qtbot.addWidget(entry)
    dialog = ImportResultsDialog(db, setup["analyst"], test, "HPLC-ASSAY", entry.params, sample_id)
    qtbot.addWidget(dialog)
    assert dialog.template.currentData() == template_id
    dialog.file = ("seq.csv", data)
    dialog._preview()
    assert dialog.ok.isEnabled()
    dialog._import()
    assert dialog.result() == QDialog.Accepted and dialog.values[("area_spl", 1)] == "10234"

    class Done:  # stands in for the dialog the analyst just completed
        def __init__(self, *args):
            self.values = dialog.values

        def exec(self):
            return QDialog.Accepted

    monkeypatch.setattr(testing_dialogs, "ImportResultsDialog", Done)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    entry.fields[("wt_std", 1)].setText("25.12")  # typed before importing: kept
    entry._import()
    assert entry.fields[("area_spl", 2)].text() == "10101"
    assert entry.fields[("area_std", 1)].text() == "10120"
    assert entry.fields[("wt_std", 1)].text() == "25.12"
    assert [f.file_name for f in entry.imported] == ["seq.csv"]
    with db.session() as s:
        assert testing.stored_values(s, test) == {}  # nothing saved until the analyst saves


def test_templates_button_only_for_master_data_managers(qtbot, db, setup, admin):  # noqa: F811
    for user, visible in ((admin, True), (setup["analyst"], False)):
        view = MethodsView(db, user)
        qtbot.addWidget(view)
        assert view.templates_button.isVisibleTo(view) == visible
