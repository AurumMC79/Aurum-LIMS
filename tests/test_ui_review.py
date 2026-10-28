"""Smoke tests for the Phase 4 screens (review, decision, certificates, lab details)."""

from PySide6.QtWidgets import QPushButton

from aurum_lims.services import review
from aurum_lims.ui.main_window import MainWindow
from aurum_lims.ui.testing_dialogs import ResultEntryDialog
from aurum_lims.ui.views.certificates import CertificatesView
from aurum_lims.ui.views.review import DecisionDialog, ReviewView
from tests.conftest import USER_PASSWORD
from tests.test_review import signed  # noqa: F401 - fixture
from tests.test_testing import setup  # noqa: F401 - fixture


def _buttons(widget) -> dict[str, QPushButton]:
    return {b.text(): b for b in widget.findChildren(QPushButton)}


def test_review_page_and_review_mode(qtbot, db, signed):  # noqa: F811
    view = ReviewView(db, signed["qa"])
    qtbot.addWidget(view)
    view.refresh()
    assert view.test_table.rowCount() == 2 and view.sample_table.rowCount() == 0
    assert view.test_table.item(0, 3).text() == "Jsmith"  # who signed the test, not who it was assigned to

    dialog = ResultEntryDialog(db, signed["qa"], signed["assay_test"], review_mode=True)
    qtbot.addWidget(dialog)
    buttons = _buttons(dialog)
    assert "Accept and sign…" in buttons and "Return for correction…" in buttons
    assert dialog.summary.item(0, 4).text() == "100.0"  # frozen, signed result

    analyst_view = ResultEntryDialog(db, signed["analyst"], signed["assay_test"], review_mode=True)
    qtbot.addWidget(analyst_view)
    assert "Accept and sign…" not in _buttons(analyst_view)  # analysts cannot review


def test_decision_dialog_and_certificates(qtbot, db, signed):  # noqa: F811
    for analysis_id in (signed["assay_test"], signed["app_test"]):
        with db.session() as s:
            assert review.accept_test(s, signed["qa"], analysis_id, USER_PASSWORD).ok
    dialog = DecisionDialog(db, signed["qa"], signed["sample"])
    qtbot.addWidget(dialog)
    buttons = _buttons(dialog)
    assert buttons["Approve and sign…"].isEnabled() and buttons["Reject and sign…"].isEnabled()

    with db.session() as s:
        review.decide_sample(s, signed["qa"], signed["sample"], approve=True, password=USER_PASSWORD)
    view = CertificatesView(db, signed["analyst"])
    qtbot.addWidget(view)
    view.refresh()
    assert view.table.rowCount() == 1 and view.table.item(0, 0).text().startswith("CoA-")
    view.search.setText("no-such-thing")
    assert view.table.rowCount() == 0


def test_navigation_by_role(qtbot, db, signed):  # noqa: F811
    qa_window = MainWindow(db, signed["qa"])
    qtbot.addWidget(qa_window)
    titles = [p.title for p in qa_window.pages]
    assert {"Review", "Certificates"} <= set(titles)
    for title in titles:
        assert qa_window.show_page(title)
    qa_window.sign_out("test")
    analyst_window = MainWindow(db, signed["analyst"])
    qtbot.addWidget(analyst_window)
    assert "Review" not in {p.title for p in analyst_window.pages}
    analyst_window.sign_out("test")
