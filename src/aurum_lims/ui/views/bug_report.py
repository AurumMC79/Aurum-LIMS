"""'Report a bug' tab: write a report (with a screenshot) and follow its status."""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QEvent, QIODevice, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import bug_reports
from aurum_lims.services.bug_reports import BugStatus, Severity
from aurum_lims.ui.common import Card, error_label, format_local, muted_label, primary_button, show_error, show_label_message

AREAS = [
    "General", "Sign-in / accounts", "Dashboard", "Samples", "Testing / result entry", "Review / approval", "OOS",
    "Certificates", "Instruments", "Inventory", "Storage", "Methods", "Specifications", "Sample types",
    "SOP index", "Users", "Audit trail", "Settings", "Other",
]
STATUS_COLORS = {"New": "#b54708", "In progress": "#175cd3", "Fixed": "#067647", "Won't fix": "#8b98a8",
                 "Duplicate": "#8b98a8"}


def image_to_png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data)


class ReportDetailDialog(QDialog):
    def __init__(self, row: bug_reports.BugRow, screenshot: bytes | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{row.number} – {row.title}")
        self.resize(760, 640)
        layout = QVBoxLayout(self)
        info = QLabel(
            f"<b>{row.number}</b> · {row.status} · {row.severity} · {row.area}<br>"
            f"Reported by {row.reported_by} on {format_local(row.reported_at)} (version {row.app_version})"
            + (f"<br>Last updated by {row.updated_by} on {format_local(row.updated_at)}" if row.updated_at else "")
        )
        layout.addWidget(info)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        parts = [f"What happened:\n{row.description}"]
        if row.steps:
            parts.append(f"Steps to reproduce:\n{row.steps}")
        if row.expected:
            parts.append(f"What should have happened:\n{row.expected}")
        if row.resolution:
            parts.append(f"Resolution:\n{row.resolution}")
        text.setPlainText("\n\n".join(parts))
        layout.addWidget(text, 1)
        if screenshot:
            pixmap = QPixmap()
            pixmap.loadFromData(screenshot)
            image = QLabel()
            image.setPixmap(pixmap.scaledToWidth(min(pixmap.width(), 1200), Qt.SmoothTransformation))
            scroll = QScrollArea()
            scroll.setWidget(image)
            layout.addWidget(scroll, 2)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


class StatusDialog(QDialog):
    def __init__(self, row: bug_reports.BugRow, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Update {row.number}")
        self.setMinimumWidth(460)
        form = QFormLayout(self)
        self.status = QComboBox()
        for status in BugStatus:
            self.status.addItem(status.value)
        self.status.setCurrentText(row.status)
        self.resolution = QPlainTextEdit(row.resolution or "")
        self.resolution.setPlaceholderText("What was done (required when Fixed / Won't fix / Duplicate)")
        self.resolution.setFixedHeight(80)
        form.addRow("Status", self.status)
        form.addRow("Resolution", self.resolution)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)


class BugReportTab(QWidget):
    # Area, reporter and full details are in the View window (and tooltips), so the list fits beside the form.
    COLUMNS = ["Number", "Title", "Severity", "Status", "Reported"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows: list[bug_reports.BugRow] = []
        self.screenshot: bytes | None = None
        self.can_manage = user.can(Permission.MANAGE_BUG_REPORTS)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 12, 4, 4)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter)

        # ----- the form -----
        form_card = Card("Report a problem")
        form_card.body.addWidget(muted_label(
            "Describe what went wrong. Reports are stored in Aurum LIMS for IT / the developers to read; "
            "include a screenshot if you can (press Win+Shift+S, then 'Paste screenshot')."
        ))
        form = QFormLayout()
        self.title_edit = QLineEdit()
        self.title_edit.setPlaceholderText("Short summary of the problem")
        self.area = QComboBox()
        self.area.addItems(AREAS)
        self.severity = QComboBox()
        for s in Severity:
            self.severity.addItem(f"{s.value} – {s.help}", s.value)
        self.severity.setCurrentIndex(1)
        for combo in (self.area, self.severity):
            # Don't let long choices force the form wider than its panel.
            combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(16)
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText("What happened? Include any error message exactly as shown.")
        self.steps = QPlainTextEdit()
        self.steps.setPlaceholderText("1. Opened Samples\n2. Clicked Register samples\n3. …")
        self.expected = QPlainTextEdit()
        self.expected.setPlaceholderText("What did you expect to happen instead?")
        for box, height in ((self.description, 90), (self.steps, 80), (self.expected, 50)):
            box.setFixedHeight(height)
        form.addRow("Title *", self.title_edit)
        form.addRow("Where", self.area)
        form.addRow("Severity", self.severity)
        form.addRow("What happened *", self.description)
        form.addRow("Steps to reproduce", self.steps)
        form.addRow("Expected", self.expected)
        shot_row = QHBoxLayout()
        paste = QPushButton("Paste screenshot")
        paste.clicked.connect(lambda *_: self._paste())
        choose = QPushButton("Choose image…")
        choose.clicked.connect(lambda *_: self._choose())
        self.clear_shot = QPushButton("Remove")
        self.clear_shot.clicked.connect(lambda *_: self._set_screenshot(None))
        for b in (paste, choose):
            shot_row.addWidget(b)
        shot_row.addStretch()
        form.addRow("Screenshot", shot_row)
        self.preview = QLabel()
        self.preview.setObjectName("Muted")
        preview_row = QHBoxLayout()
        preview_row.addWidget(self.preview)
        preview_row.addWidget(self.clear_shot)
        preview_row.addStretch()
        form.addRow("", preview_row)
        form_card.body.addLayout(form)
        self.error = error_label()
        form_card.body.addWidget(self.error)
        submit_row = QHBoxLayout()
        submit_row.addStretch()
        submit = primary_button("Submit report")
        submit.setDefault(False)
        submit.clicked.connect(lambda *_: self._submit())
        submit_row.addWidget(submit)
        form_card.body.addLayout(submit_row)
        self.form_card = form_card
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.NoFrame)
        self.form_scroll.setWidget(form_card)
        # BUG-0002: never let the splitter squeeze the form narrower than its fields
        # (it used to be clipped on the right). The list takes the remaining space.
        self.form_scroll.setMinimumWidth(
            form_card.minimumSizeHint().width() + self.form_scroll.verticalScrollBar().sizeHint().width())
        splitter.addWidget(self.form_scroll)

        # ----- the list -----
        list_box = QWidget()
        list_layout = QVBoxLayout(list_box)
        list_layout.setContentsMargins(6, 0, 0, 0)
        heading = QLabel("<b>All bug reports</b>" if self.can_manage else "<b>My bug reports</b>")
        list_layout.addWidget(heading)
        buttons = QHBoxLayout()
        self.view_button = QPushButton("View")
        self.status_button = QPushButton("Update status…")
        self.status_button.setVisible(self.can_manage)
        buttons.addWidget(self.view_button)
        buttons.addWidget(self.status_button)
        buttons.addStretch()
        list_layout.addLayout(buttons)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(self.COLUMNS.index("Title"), QHeaderView.Stretch)
        header.setMinimumSectionSize(32)  # the Title column shrinks first on narrow windows (full title in tooltip)
        self.table.doubleClicked.connect(lambda *_: self._view())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        list_layout.addWidget(self.table, 1)
        splitter.addWidget(list_box)
        splitter.setChildrenCollapsible(False)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # Start with the form at the width it needs and give the list everything else;
        # the divider can still be dragged.
        splitter.setSizes([self.form_scroll.minimumWidth(), 10_000])
        self.splitter = splitter
        list_box.installEventFilter(self)  # show / hide the date column whenever the list changes width

        self.view_button.clicked.connect(lambda *_: self._view())
        self.status_button.clicked.connect(lambda *_: self._update_status())
        self._set_screenshot(None)
        self._update_buttons()

    # ----- layout -----
    TITLE_ROOM = 110  # pixels the Title column should keep before the date column is given up

    def _needed_width(self, with_date: bool = True, title_room: int | None = None) -> int:
        """Width the table needs to show its columns without a sideways scroll bar."""
        header = self.table.horizontalHeader()
        skip = {self.COLUMNS.index("Title")} | (set() if with_date else {self.COLUMNS.index("Reported")})
        needed = 2 * self.table.frameWidth() + (self.TITLE_ROOM if title_room is None else title_room)
        for column in range(len(self.COLUMNS)):
            if column not in skip:
                needed += max(header.sectionSizeHint(column), self.table.sizeHintForColumn(column))
        if self.table.verticalScrollBar().isVisible():
            needed += self.table.verticalScrollBar().width()
        return needed

    def _fit_columns(self) -> None:
        # The list may never be narrower than its essential columns (so nothing is ever cut off);
        # on narrow windows the date column makes way (the date is in the tooltip and the View window).
        list_box = self.splitter.widget(1)
        margins = list_box.layout().contentsMargins()
        minimum = self._needed_width(with_date=False, title_room=self.table.horizontalHeader().minimumSectionSize())
        list_box.setMinimumWidth(max(260, minimum + margins.left() + margins.right()))
        self.table.setColumnHidden(self.COLUMNS.index("Reported"), self.table.width() < self._needed_width())

    def eventFilter(self, watched, event) -> bool:
        if watched is self.splitter.widget(1) and event.type() == QEvent.Resize:
            self._fit_columns()
        return super().eventFilter(watched, event)

    # ----- screenshot -----
    def _set_screenshot(self, data: bytes | None) -> None:
        self.screenshot = data
        self.clear_shot.setEnabled(data is not None)
        if data is None:
            self.preview.clear()  # setPixmap() would also wipe the text, so clear first
            self.preview.setText("No screenshot attached")
            return
        pixmap = QPixmap()
        pixmap.loadFromData(data)
        self.preview.setPixmap(pixmap.scaledToWidth(min(pixmap.width(), 320), Qt.SmoothTransformation))

    def _paste(self) -> None:
        image = QGuiApplication.clipboard().image()
        if image.isNull():
            show_label_message(self.error, "There is no image on the clipboard. Press Win+Shift+S, select an area, "
                                           "then click 'Paste screenshot' again.")
            return
        show_label_message(self.error, "")
        self._set_screenshot(image_to_png(image))

    def _choose(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose screenshot", "", "Images (*.png *.jpg *.jpeg)")
        if path:
            image = QImage(path)
            if image.isNull():
                show_label_message(self.error, "That file could not be read as an image.")
                return
            self._set_screenshot(image_to_png(image))

    # ----- reports -----
    def refresh(self) -> None:
        with self.db.session() as session:
            self.rows = bug_reports.list_reports(session, self.user)
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            values = [row.number, row.title, row.severity, row.status,
                      row.reported_at.astimezone().strftime("%Y-%m-%d")]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                column = self.COLUMNS[c]
                if column == "Title":
                    item.setToolTip(f"{row.title}\nWhere: {row.area}\nReported by: {row.reported_by} on "
                                    f"{format_local(row.reported_at)}")
                if column == "Status":
                    item.setForeground(QColor(STATUS_COLORS.get(row.status, "#1f2933")))
                if column == "Severity" and row.severity in ("High", "Critical"):
                    item.setForeground(QColor("#b42318"))
                if column == "Reported":
                    item.setToolTip(format_local(row.reported_at))
                self.table.setItem(r, c, item)
        self._fit_columns()
        self._update_buttons()

    def _selected(self) -> bug_reports.BugRow | None:
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else None

    def _update_buttons(self) -> None:
        selected = self._selected() is not None
        self.view_button.setEnabled(selected)
        self.status_button.setEnabled(selected)

    def _submit(self) -> None:
        try:
            with self.db.session() as session:
                report = bug_reports.submit(
                    session, self.user, title=self.title_edit.text(), area=self.area.currentText(),
                    severity=self.severity.currentData(), description=self.description.toPlainText(),
                    steps=self.steps.toPlainText(), expected=self.expected.toPlainText(),
                    screenshot=self.screenshot,
                )
                number = report.number
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        show_label_message(self.error, "")
        for widget in (self.title_edit, self.description, self.steps, self.expected):
            widget.clear()
        self._set_screenshot(None)
        self.refresh()
        QMessageBox.information(self, "Thank you", f"Your report was saved as {number}.")

    def _view(self) -> None:
        row = self._selected()
        if row is None:
            return
        try:
            with self.db.session() as session:
                shot = bug_reports.screenshot(session, self.user, row.id)
        except AurumError as exc:
            show_error(self, exc)
            return
        ReportDetailDialog(row, shot, self).exec()

    def _update_status(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = StatusDialog(row, self)
        if dialog.exec() != QDialog.Accepted:
            return
        try:
            with self.db.session() as session:
                bug_reports.update_status(session, self.user, row.id, dialog.status.currentText(),
                                          dialog.resolution.toPlainText())
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()
