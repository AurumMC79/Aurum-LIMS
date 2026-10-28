"""Certificates page: every issued Certificate of Analysis and Test Report."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import certificates
from aurum_lims.ui.common import format_local, muted_label, primary_button, show_error
from aurum_lims.ui.views.review import open_certificate


class CertificatesView(QWidget):
    title = "Certificates"
    COLUMNS = ["Number", "Type", "Sample ID", "Material / point", "Decision", "Issued", "Issued by"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "Each certificate is stored exactly as issued. Opening or saving a copy always gives the original "
            "document, and its digital fingerprint is checked every time."
        ))
        toolbar = QHBoxLayout()
        self.open_button = primary_button("Open PDF")
        self.open_button.setDefault(False)
        self.save_button = QPushButton("Save a copy…")
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by number, sample ID or material…")
        self.search.textChanged.connect(lambda *_: self._fill())
        self.count = muted_label("")
        toolbar.addWidget(self.open_button)
        toolbar.addWidget(self.save_button)
        toolbar.addStretch()
        toolbar.addWidget(self.search, 1)
        toolbar.addWidget(self.count)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.doubleClicked.connect(lambda *_: self._open())
        self.table.itemSelectionChanged.connect(self._update_buttons)
        layout.addWidget(self.table, 1)
        self.open_button.clicked.connect(lambda *_: self._open())
        self.save_button.clicked.connect(lambda *_: self._save())
        self._update_buttons()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.all_rows = certificates.list_certificates(session, self.user)
        self._fill()

    def _fill(self) -> None:
        needle = self.search.text().strip().lower()
        self.rows = [r for r in getattr(self, "all_rows", []) if not needle or needle in
                     f"{r.number} {r.sample_id} {r.material or ''}".lower()]
        self.table.setRowCount(len(self.rows))
        for i, r in enumerate(self.rows):
            for c, value in enumerate([r.number, r.kind, r.sample_id, r.material or "", r.decision,
                                       format_local(r.issued_at), r.issued_by]):
                item = QTableWidgetItem(value)
                if c == 4:
                    item.setForeground(QColor("#067647" if r.decision == "Approved" else "#b42318"))
                self.table.setItem(i, c, item)
        self.count.setText(f"{len(self.rows)} certificate(s)")
        self._update_buttons()

    def _selected(self):
        model = self.table.selectionModel()
        idx = model.selectedRows() if model else []
        return self.rows[idx[0].row()] if idx and idx[0].row() < len(self.rows) else None

    def _update_buttons(self) -> None:
        selected = self._selected() is not None
        self.open_button.setEnabled(selected)
        self.save_button.setEnabled(selected)

    def _open(self) -> None:
        row = self._selected()
        if row is not None:
            open_certificate(self, self.db, self.user, row.id)

    def _save(self) -> None:
        row = self._selected()
        if row is None:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save certificate", f"{row.number}.pdf", "PDF files (*.pdf)")
        if not path:
            return
        try:
            with self.db.session() as session:
                certificates.export_pdf(session, self.user, row.id, path)
        except (AurumError, OSError) as exc:
            show_error(self, exc, "Could not save")
            return
        QMessageBox.information(self, "Saved", f"{row.number} saved to:\n{path}")
