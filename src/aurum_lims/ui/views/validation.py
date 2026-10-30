"""Settings → Validation: create the validation package and see whether the program has changed since."""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.ui.common import Card, format_local, muted_label, primary_button, show_error
from aurum_lims.validation import content, package

GREEN, AMBER, RED = "#067647", "#b54708", "#b42318"


class ValidationTab(QWidget):
    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.last_folder: Path | None = None

        from aurum_lims.ui.views.settings import _scrolling  # shared layout helper

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scrolling(self._create_card(), self._status_card()))

    def _create_card(self) -> Card:
        card = Card("Validation package")
        tests = sum(len(a.tests) for a in content.OQ)
        card.body.addWidget(muted_label(
            "Creates the documents for validating Aurum LIMS (computer-system validation, GAMP 5 approach) as "
            "editable Word files: Validation Plan, User Requirements Specification "
            f"({len(content.REQUIREMENTS)} requirements with GxP risk), Installation Qualification filled in with "
            f"this computer's details and automatic checks, Operational Qualification ({tests} test scripts), "
            f"Performance Qualification ({len(content.PQ)} scenarios), Traceability Matrix and a Validation Summary "
            "Report template. Review and adapt them to your procedures, then approve and execute them under your "
            "document control."))
        form = QFormLayout()
        self.prefix = QLineEdit("VAL-AURUM")
        self.prefix.setMaxLength(24)
        self.prefix.setToolTip("Document numbers become e.g. VAL-AURUM-URS-001")
        folder_row = QHBoxLayout()
        self.folder = QLineEdit(str(Path.home() / "Documents"))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda *_: self._browse())
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        form.addRow("Document number prefix", self.prefix)
        form.addRow("Save in", folder_row)
        card.body.addLayout(form)
        buttons = QHBoxLayout()
        self.create_button = primary_button("Create validation package")
        self.create_button.setDefault(False)
        self.create_button.clicked.connect(lambda *_: self._create())
        self.open_button = QPushButton("Open folder")
        self.open_button.setEnabled(False)
        self.open_button.clicked.connect(lambda *_: self._open())
        buttons.addWidget(self.create_button)
        buttons.addWidget(self.open_button)
        buttons.addStretch()
        card.body.addLayout(buttons)
        self.result = QLabel("")
        self.result.setWordWrap(True)
        self.result.setTextFormat(Qt.RichText)
        card.body.addWidget(self.result)
        return card

    def _status_card(self) -> Card:
        card = Card("Change control")
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.RichText)
        card.body.addWidget(self.status)
        card.body.addWidget(muted_label(
            "The program fingerprint is a SHA-256 over every program file. It is recorded in the IQ and in the "
            "audit trail each time a package is created. If it differs from the last package, the software has "
            "changed since then: assess the change, repeat the IQ and re-run the affected OQ tests."))
        return card

    # ----- data -----
    def refresh(self) -> None:
        current = package.manifest_fingerprint(package.program_manifest())
        with self.db.session() as session:
            last = package.last_package(session)
        if last is None:
            self.status.setText(f"No validation package has been created yet.<br/>Current program fingerprint: "
                                f"<code>{current[:16]}…</code>")
            return
        recorded = json.loads(last.new_values or "{}").get("program_fingerprint", "")
        same = recorded == current
        colour, verdict = (GREEN, "unchanged since") if same else (AMBER, "<b>changed</b> since")
        self.status.setText(
            f"Last package: {format_local(last.timestamp_utc)} by {last.username}.<br/>"
            f"<span style='color:{colour}'>The program is {verdict} that package.</span><br/>"
            f"Current fingerprint <code>{current[:16]}…</code> · recorded <code>{recorded[:16]}…</code>")

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Save the validation package in", self.folder.text())
        if chosen:
            self.folder.setText(str(Path(chosen)))

    def _create(self) -> None:
        prefix = "".join(ch for ch in self.prefix.text().strip().upper() if ch.isalnum() or ch in "-_") or "VAL"
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            result = package.generate(self.db, self.user, Path(self.folder.text()), prefix)
        except (AurumError, OSError) as exc:
            QApplication.restoreOverrideCursor()
            show_error(self, exc, "Could not create the package")
            return
        QApplication.restoreOverrideCursor()
        self.last_folder = result.folder
        self.open_button.setEnabled(True)
        failed = [c for c in result.checks if not c.passed]
        checks = (f"<span style='color:{GREEN}'>All {len(result.checks)} automatic installation checks passed.</span>"
                  if not failed else
                  f"<span style='color:{RED}'>{len(failed)} installation check(s) failed: "
                  + "; ".join(f"{c.name} ({c.observed})" for c in failed) + ". See the IQ.</span>")
        self.result.setText(f"Created {len(result.files)} files in:<br/><b>{result.folder}</b><br/>{checks}")
        self.refresh()
        QMessageBox.information(self, "Validation package", f"The validation package was created in:\n\n"
                                                            f"{result.folder}")

    def _open(self) -> None:
        if self.last_folder is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_folder)))
