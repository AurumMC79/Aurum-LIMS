"""Audit trail viewer with filtering, integrity check and CSV export."""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction
from aurum_lims.ui.common import GridTable, format_local, muted_label, primary_button, show_error

QUERY_LIMIT = 5000


def summarize_change(old_json: str | None, new_json: str | None) -> str:
    """Turn stored old/new JSON into a short human-readable description."""
    old = json.loads(old_json) if old_json else {}
    new = json.loads(new_json) if new_json else {}
    parts = []
    for key in sorted(set(old) | set(new)):
        if key in old and key in new:
            parts.append(f"{key}: {old[key]} → {new[key]}")
        elif key in new:
            parts.append(f"{key}: {new[key]}")
        else:
            parts.append(f"{key}: {old[key]} → (removed)")
    return "; ".join(parts)


class AuditView(QWidget):
    title = "Audit Trail"
    COLUMNS = ["#", "Date / time", "User", "Action", "Record", "Details", "Reason"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.entries = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(
            muted_label(
                "Every sign-in, change and signature is recorded here with the date, time and person. "
                "Entries can never be edited or deleted, and each is chained to the one before it so "
                "tampering can be detected with “Verify integrity”."
            )
        )

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search user, record, details or reason…")
        self.search.returnPressed.connect(self.refresh)
        self.action = QComboBox()
        self.action.addItem("All actions", None)
        for action in AuditAction:
            self.action.addItem(action.value.replace("_", " ").title(), action.value)
        self.action.currentIndexChanged.connect(lambda *_: self.refresh())
        self.all_dates = QCheckBox("All dates")
        self.all_dates.setChecked(True)
        self.date_from = QDateEdit(QDate.currentDate().addDays(-30))
        self.date_to = QDateEdit(QDate.currentDate())
        for edit in (self.date_from, self.date_to):
            edit.setCalendarPopup(True)
            edit.setDisplayFormat("yyyy-MM-dd")
            edit.dateChanged.connect(lambda *_: self.refresh())
        self.all_dates.toggled.connect(self._toggle_dates)
        filters.addWidget(self.search, 2)
        filters.addWidget(self.action, 1)
        filters.addWidget(self.all_dates)
        filters.addWidget(QLabel("From"))
        filters.addWidget(self.date_from)
        filters.addWidget(QLabel("To"))
        filters.addWidget(self.date_to)
        layout.addLayout(filters)
        self._toggle_dates(True)

        actions = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(lambda *_: self.refresh())
        verify = primary_button("Verify integrity")
        verify.setDefault(False)
        verify.clicked.connect(self._verify)
        self.export_button = QPushButton("Export CSV…")
        self.export_button.clicked.connect(self._export)
        self.export_button.setVisible(user.can(Permission.EXPORT_AUDIT_TRAIL))
        self.count_label = muted_label("")
        actions.addWidget(refresh)
        actions.addWidget(verify)
        actions.addWidget(self.export_button)
        actions.addStretch()
        actions.addWidget(self.count_label)
        layout.addLayout(actions)

        self.table = GridTable(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.Stretch)
        layout.addWidget(self.table)

    def _toggle_dates(self, all_dates: bool) -> None:
        self.date_from.setEnabled(not all_dates)
        self.date_to.setEnabled(not all_dates)
        self.refresh()

    def _date_range(self) -> tuple[datetime | None, datetime | None]:
        if self.all_dates.isChecked():
            return None, None
        start = datetime.combine(self.date_from.date().toPython(), time.min).astimezone()
        end = datetime.combine(self.date_to.date().toPython() + timedelta(days=1), time.min).astimezone()
        return start, end

    def _query(self, session, limit=QUERY_LIMIT):
        start, end = self._date_range()
        return audit.query_entries(
            session, self.user, search=self.search.text().strip() or None, action=self.action.currentData(),
            start=start, end=end, limit=limit,
        )

    def refresh(self) -> None:
        if not hasattr(self, "table"):
            return
        with self.db.session() as session:
            self.entries = self._query(session)
        self.table.setRowCount(len(self.entries))
        for r, e in enumerate(self.entries):
            record = f"{e.entity_type} {e.entity_id}" if e.entity_type and e.entity_id else (e.entity_type or "")
            values = [
                str(e.id), format_local(e.timestamp_utc, with_seconds=True), e.username,
                e.action.replace("_", " ").title(), record, summarize_change(e.old_values, e.new_values), e.reason or "",
            ]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(value)
                self.table.setItem(r, c, item)
        suffix = f" (newest {QUERY_LIMIT} shown)" if len(self.entries) >= QUERY_LIMIT else ""
        self.count_label.setText(f"{len(self.entries)} entries{suffix}")

    def _verify(self) -> None:
        with self.db.session() as session:
            report = audit.verify_integrity(session, self.user)
        if report.ok:
            QMessageBox.information(
                self, "Audit trail intact",
                f"All {report.entries_checked} audit trail entries were checked.\n\nNo alterations or gaps were found.",
            )
        else:
            details = "\n".join(report.problems[:20])
            more = f"\n…and {len(report.problems) - 20} more." if len(report.problems) > 20 else ""
            QMessageBox.critical(
                self, "Audit trail integrity problem",
                f"{len(report.problems)} problem(s) found in {report.entries_checked} entries:\n\n{details}{more}\n\n"
                "The database may have been modified outside Aurum LIMS. Preserve a copy of the database file "
                "and investigate according to your data-integrity procedures.",
            )
        self.refresh()

    def _export(self) -> None:
        default = f"audit_trail_{datetime.now():%Y%m%d_%H%M}.csv"
        path, _ = QFileDialog.getSaveFileName(self, "Export audit trail", default, "CSV files (*.csv)")
        if not path:
            return
        try:
            with self.db.session() as session:
                entries = self._query(session, limit=None)
                audit.export_csv(session, self.user, path, entries)
        except (AurumError, OSError) as exc:
            show_error(self, exc, "Export failed")
            return
        QMessageBox.information(self, "Export complete", f"Exported {len(entries)} entries to:\n{path}")
        self.refresh()
