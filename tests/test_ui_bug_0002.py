"""BUG-0002: the Report a bug form was cut off on the right and the list needed sideways scrolling."""

import pytest

from aurum_lims.services import bug_reports
from aurum_lims.ui.main_window import MainWindow


def _settle(qtbot, window, width, height):
    window.resize(width, height)
    window.show()
    qtbot.waitExposed(window)
    window.show_page("Settings")
    settings = window.stack.currentWidget()
    settings.tabs.setCurrentWidget(settings.bug_tab)
    qtbot.wait(300)  # let the splitter and column sizes settle
    return settings.bug_tab


@pytest.mark.parametrize("size", [(1440, 900), (1200, 760), (1100, 640), (1000, 640), (960, 600)])
def test_report_form_and_list_fit(qtbot, db, admin, size):
    with db.session() as s:
        for i in range(3):
            bug_reports.submit(s, admin, title=f"Report number {i} with a reasonably long title", area="Settings",
                               severity="Critical", description="x")
    window = MainWindow(db, admin)
    qtbot.addWidget(window)
    tab = _settle(qtbot, window, *size)
    try:
        # The whole form is visible: nothing hangs off the right edge of its panel.
        assert tab.form_scroll.horizontalScrollBar().maximum() == 0
        assert tab.form_card.width() >= tab.form_card.minimumSizeHint().width()
        # Every column of the list fits without a sideways scroll bar; the date shows whenever there is room.
        assert tab.table.horizontalScrollBar().maximum() == 0
        # The date column is shown exactly when it fits (font widths differ between screens).
        date_hidden = tab.table.isColumnHidden(tab.COLUMNS.index("Reported"))
        assert date_hidden == (tab.table.width() < tab._needed_width())
        assert tab.preview.text() == "No screenshot attached"
    finally:
        window.sign_out("test")
