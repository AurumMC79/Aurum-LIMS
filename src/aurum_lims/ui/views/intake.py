"""Sample Intaking page: log new samples in by filling in the laboratory's intake form."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from PySide6.QtCore import Qt
from sqlalchemy import select

from aurum_lims.db import Database
from aurum_lims.db.models import IntakeSubmission, Sample, SampleType, User
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import intake, sample_types, storage
from aurum_lims.services.barcodes import ScanSettings, read_scan
from aurum_lims.ui.common import Card, GridTable, format_local, muted_label, primary_button, show_error
from aurum_lims.ui.intake_designer import FormDesignerDialog
from aurum_lims.ui.intake_form import IntakeFormWidget
from aurum_lims.ui.sample_dialogs import show_created

RECENT = 25


class IntakeView(QWidget):
    title = "Sample Intaking"
    RECENT_COLUMNS = ["Sample ID", "Type", "Material / point", "Lot", "Status", "Logged in", "By"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.can_submit = user.can(Permission.CREATE_RECORDS)
        self.can_design = user.can(Permission.MANAGE_MASTER_DATA)
        self.form_widget: IntakeFormWidget | None = None
        self.form_key = None
        self.scan_settings = ScanSettings()
        self.scans: list[str] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        top = QHBoxLayout()
        self.version_label = muted_label("")
        top.addWidget(self.version_label, 1)
        self.design_button = QPushButton("Design form…")
        self.design_button.setVisible(self.can_design)
        self.design_button.clicked.connect(lambda *_: self._design())
        top.addWidget(self.design_button)
        layout.addLayout(top)

        self.scan_card = Card("Scan")
        scan_row = QHBoxLayout()
        self.scan_box = QLineEdit()
        self.scan_box.setPlaceholderText("Scan a barcode here – or type / paste a code and press Enter")
        self.scan_box.setClearButtonEnabled(True)
        self.scan_box.returnPressed.connect(self._scanned)
        scan_row.addWidget(self.scan_box, 1)
        self.scan_card.body.addLayout(scan_row)
        self.scan_result = QLabel("")
        self.scan_result.setWordWrap(True)
        self.scan_result.setTextFormat(Qt.RichText)
        self.scan_card.body.addWidget(self.scan_result)
        layout.addWidget(self.scan_card)
        self.scan_hint = muted_label("Barcode scanning is off. Set it up with Design form… → Barcode scanning….")
        self.scan_hint.setVisible(False)
        layout.addWidget(self.scan_hint)

        splitter = QSplitter(Qt.Vertical)
        card = Card("New sample")
        self.form_scroll = QScrollArea()
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setFrameShape(QScrollArea.NoFrame)
        card.body.addWidget(self.form_scroll, 1)
        buttons = QHBoxLayout()
        self.submit_button = primary_button("Submit sample")
        self.submit_button.clicked.connect(lambda *_: self._submit())
        self.submit_button.setVisible(self.can_submit)
        clear = QPushButton("Clear form")
        clear.clicked.connect(lambda *_: self._clear())
        buttons.addWidget(self.submit_button)
        buttons.addWidget(clear)
        buttons.addStretch()
        buttons.addWidget(muted_label("* must be filled in"))
        card.body.addLayout(buttons)
        if not self.can_submit:
            card.body.addWidget(muted_label("Your role can design the form but not log samples in."))
        splitter.addWidget(card)

        recent = QWidget()
        recent_layout = QVBoxLayout(recent)
        recent_layout.setContentsMargins(0, 8, 0, 0)
        recent_layout.addWidget(QLabel("<b>Recently logged in</b>"))
        self.recent = GridTable(0, len(self.RECENT_COLUMNS))
        self.recent.setHorizontalHeaderLabels(self.RECENT_COLUMNS)
        self.recent.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recent.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recent.verticalHeader().hide()
        self.recent.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.recent.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        recent_layout.addWidget(self.recent)
        splitter.addWidget(recent)
        splitter.setSizes([560, 200])
        layout.addWidget(splitter, 1)

    # ----- data -----
    def refresh(self) -> None:
        with self.db.session() as session:
            row, fields = intake.current_form(session)
            self.scan_settings = intake.current_scan(session)
            self.type_choices = [(t.id, f"{t.code} – {t.name}") for t in sample_types.list_types(session)
                                 if t.is_active]
            self.location_choices = storage.active_choices(session)
            recent = session.execute(
                select(Sample, SampleType.name, IntakeSubmission.submitted_at, User.full_name)
                .join(IntakeSubmission, IntakeSubmission.sample_id == Sample.id)
                .join(SampleType, SampleType.id == Sample.sample_type_id)
                .join(User, User.id == IntakeSubmission.submitted_by_id)
                .order_by(IntakeSubmission.id.desc()).limit(RECENT)).all()
        self.version_label.setText(
            f"Intake form version {row.version} (saved {format_local(row.created_at)})." if row else
            "Using the built-in starting form." + (" Choose Design form… to make it your own." if self.can_design
                                                   else ""))
        key = (row.version if row else 0, tuple(self.type_choices), tuple(self.location_choices))
        if key != self.form_key:  # rebuild only when the form or its lists changed, so typing is not lost
            self.form_key = key
            self.form_widget = IntakeFormWidget(fields, self.type_choices, self.location_choices)
            holder = QWidget()
            holder.setObjectName("Content")
            inner = QVBoxLayout(holder)
            inner.addWidget(self.form_widget)
            inner.addStretch()
            self.form_scroll.setWidget(holder)
            self.scans = []
            self.scan_result.setText("")
        scanning = self.scan_settings.enabled and self.can_submit
        self.scan_card.setVisible(scanning)
        self.scan_hint.setVisible(not self.scan_settings.enabled and self.can_design)
        if scanning:
            self.scan_box.setFocus()
        self.recent.setRowCount(len(recent))
        for r, (sample, type_name, at, who) in enumerate(recent):
            values = [sample.sample_id, type_name, sample.material_name or sample.sampling_point or "",
                      sample.lot_number or "", sample.status, format_local(at), who]
            for c, value in enumerate(values):
                self.recent.setItem(r, c, QTableWidgetItem(value))

    def _submit(self) -> None:
        if self.form_widget is None:
            return
        values = {**self.form_widget.values(), "_scans": list(self.scans)}
        try:
            with self.db.session() as session:
                created = [s.sample_id for s in intake.submit(session, self.user, values)]
        except AurumError as exc:
            show_error(self, exc, "Sample not logged in")
            return
        self._clear()
        self.refresh()
        show_created(self, "Sample logged in", created)
        first = self.scan_box if self.scan_card.isVisible() else self.form_widget.first_input()
        if first is not None:
            first.setFocus()

    def _clear(self) -> None:
        if self.form_widget is not None:
            self.form_widget.reset()
        self.scans = []
        self.scan_result.setText("")

    def _scanned(self) -> None:
        code = self.scan_box.text()
        self.scan_box.clear()
        if not code.strip() or self.form_widget is None:
            return
        result = read_scan(self.scan_settings, code)
        filled, rejected = [], []
        for key, value in result.values.items():
            label = self.form_widget.label_of(key)
            shown = value.isoformat() if hasattr(value, "isoformat") else str(value)
            (filled if self.form_widget.set_value(key, value) else rejected).append(f"{label}: {shown}")
        if filled:
            self.scans.append(result.code)
        parts = [f"<b>Read as {result.kind}</b>"]
        if filled:
            parts.append("<span style='color:#067647'>Filled in – " + "; ".join(filled) + "</span>")
        if rejected:
            parts.append("<span style='color:#b42318'>Did not fit – " + "; ".join(rejected) + "</span>")
        if result.unused:
            parts.append("<span style='color:#6b7785'>Not used – " + "; ".join(result.unused) + "</span>")
        if not filled and not rejected:
            parts.append("<span style='color:#b54708'>Nothing was filled in. Check the scan settings "
                         "(Design form… → Barcode scanning…).</span>")
        self.scan_result.setText("<br/>".join(parts))
        self.scan_box.setFocus()

    def _design(self) -> None:
        if FormDesignerDialog(self.db, self.user, self.type_choices, self.location_choices,
                              self).exec() == QDialog.Accepted:
            self.form_key = None
            self.refresh()
