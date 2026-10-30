"""Reports page."""

from PySide6.QtWidgets import QFileDialog, QMessageBox

from aurum_lims.services import review
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.views.reports import ReportsView
from tests.conftest import USER_PASSWORD
from tests.test_review import _review_all, signed  # noqa: F401 - fixture
from tests.test_testing import setup  # noqa: F401 - fixture


def test_reports_page(qtbot, db, signed, tmp_path, monkeypatch):  # noqa: F811
    _review_all(db, signed)
    with db.session() as s:
        assert review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD).ok
    view = ReportsView(db, signed["analyst"])
    qtbot.addWidget(view)
    view.refresh()
    assert view.tat_tab.figures["count"].text() == "1"
    assert view.work_tab.figures["signed"].text() == "2"
    assert view.oos_tab.figures["count"].text() == "0"
    assert view.tat_table.rowCount() == 1 and view.work_table.rowCount() == 2
    assert view.option.currentData().parameter_key == "assay"
    assert view.ctl_tab.figures["n"].text() == "1" and view.ctl_table.rowCount() == 1
    assert not view.start_date.isVisibleTo(view)
    view.period.setCurrentIndex(view.period.findData("custom"))  # custom range shows the dates and refreshes
    assert view.start_date.isVisibleTo(view)
    view.period.setCurrentIndex(view.period.findData(365))
    assert view.tat_tab.figures["count"].text() == "1"

    target = tmp_path / "tat.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: QMessageBox.Ok)
    view._export("Turnaround")
    lines = target.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].startswith("Sample ID,Type") and len(lines) == 2


def test_reports_in_navigation_for_everyone(qtbot, db, setup):  # noqa: F811
    window = MainWindow(db, setup["analyst"])
    qtbot.addWidget(window)
    window._signing_out = True
    assert window.show_page("Reports")
