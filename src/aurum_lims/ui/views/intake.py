"""Sample Intaking page: log new samples in by filling in the laboratory's intake form."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
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
        clear.clicked.connect(lambda *_: self.form_widget and self.form_widget.reset())
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
        self.recent.setRowCount(len(recent))
        for r, (sample, type_name, at, who) in enumerate(recent):
            values = [sample.sample_id, type_name, sample.material_name or sample.sampling_point or "",
                      sample.lot_number or "", sample.status, format_local(at), who]
            for c, value in enumerate(values):
                self.recent.setItem(r, c, QTableWidgetItem(value))

    def _submit(self) -> None:
        if self.form_widget is None:
            return
        try:
            with self.db.session() as session:
                created = [s.sample_id for s in intake.submit(session, self.user, self.form_widget.values())]
        except AurumError as exc:
            show_error(self, exc, "Sample not logged in")
            return
        self.form_widget.reset()
        self.refresh()
        show_created(self, "Sample logged in", created)
        first = self.form_widget.first_input()
        if first is not None:
            first.setFocus()

    def _design(self) -> None:
        if FormDesignerDialog(self.db, self.user, self.type_choices, self.location_choices,
                              self).exec() == QDialog.Accepted:
            self.form_key = None
            self.refresh()
