"""Settings → Backups: where backups go, how often, how many to keep, and the backups themselves."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import backup
from aurum_lims.services.backup import BackupInfo, BackupSettings
from aurum_lims.ui.common import Card, GridTable, ask_reason, format_local, muted_label, primary_button, show_error

RESTORE_STEPS = (
    "To restore a backup: close Aurum LIMS on every computer, rename the current database file "
    "(keep it, do not delete it), copy the backup in its place with the name aurum.db, then start Aurum LIMS "
    "and run Audit Trail → Verify integrity. Restoring loses everything recorded after the backup was taken, "
    "so follow your lab's change-control procedure."
)


def _size(size_bytes: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size_bytes < 1024 or unit == "GB":
            return f"{size_bytes:,.0f} {unit}" if unit == "bytes" else f"{size_bytes:,.1f} {unit}"
        size_bytes /= 1024
    return ""


class BackupsTab(QWidget):
    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.settings = BackupSettings()
        self.rows: list[BackupInfo] = []

        from aurum_lims.ui.views.settings import _scrolling  # shared layout helper

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_scrolling(self._settings_card(), self._list_card()))

    # ----- building -----
    def _settings_card(self) -> Card:
        card = Card("Backup settings")
        form = QFormLayout()
        self.folder = QLineEdit()
        self.folder.setPlaceholderText(str(backup.default_folder()))
        browse = QPushButton("Browse…")
        browse.clicked.connect(lambda *_: self._browse())
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder, 1)
        folder_row.addWidget(browse)
        self.interval = QSpinBox()
        self.interval.setRange(0, 24 * 30)
        self.interval.setSuffix(" hours")
        self.interval.setSpecialValueText("Off")
        self.interval.setFixedWidth(130)
        self.keep = QSpinBox()
        self.keep.setRange(1, 365)
        self.keep.setFixedWidth(130)
        form.addRow("Backup folder", folder_row)
        form.addRow("", muted_label("Leave empty to use the folder shown. A network drive or another disk is safer: "
                                    "a backup on the same disk is lost if that disk fails."))
        form.addRow("Back up every", self.interval)
        form.addRow("", muted_label("Checked each time Aurum LIMS starts. 0 turns automatic backups off."))
        form.addRow("Backups to keep", self.keep)
        form.addRow("", muted_label(f"Older automatic and manual backups are removed. The last "
                                    f"{backup.PRE_UPDATE_KEEP} backups taken before an update are kept separately."))
        card.body.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch()
        save = primary_button("Save backup settings")
        save.setDefault(False)
        save.clicked.connect(lambda *_: self._save())
        buttons.addWidget(save)
        card.body.addLayout(buttons)
        return card

    def _list_card(self) -> Card:
        card = Card("Backups")
        self.last_label = muted_label("")
        card.body.addWidget(self.last_label)
        self.table = GridTable(0, 4)
        self.table.setHorizontalHeaderLabels(["Taken", "Kind", "Size", "File"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setMinimumHeight(260)
        card.body.addWidget(self.table)
        buttons = QHBoxLayout()
        self.backup_button = primary_button("Back up now")
        self.backup_button.setDefault(False)
        self.backup_button.clicked.connect(lambda *_: self._backup_now())
        self.verify_button = QPushButton("Check selected backup")
        self.verify_button.clicked.connect(lambda *_: self._verify())
        open_folder = QPushButton("Open backup folder")
        open_folder.clicked.connect(lambda *_: self._open_folder())
        buttons.addWidget(self.backup_button)
        buttons.addWidget(self.verify_button)
        buttons.addWidget(open_folder)
        buttons.addStretch()
        card.body.addLayout(buttons)
        restore = muted_label(RESTORE_STEPS)
        restore.setWordWrap(True)
        card.body.addWidget(restore)
        return card

    # ----- data -----
    def refresh(self) -> None:
        with self.db.session() as session:
            self.settings = backup.get_settings(session)
            last = backup.last_backup_time(session)
        self.folder.setText(self.settings.folder)
        self.interval.setValue(self.settings.interval_hours)
        self.keep.setValue(self.settings.keep)
        self.last_label.setText(f"Folder: {self.settings.path}   ·   Last backup: {format_local(last)}")
        self.rows = backup.list_backups(self.settings.path)
        self.table.setRowCount(len(self.rows))
        for r, info in enumerate(self.rows):
            values = [info.created.strftime("%Y-%m-%d %H:%M:%S"), info.kind.label, _size(info.size_bytes),
                      info.path.name]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.table.setItem(r, c, item)

    def _browse(self) -> None:
        start = self.folder.text() or str(backup.default_folder())
        chosen = QFileDialog.getExistingDirectory(self, "Backup folder", start)
        if chosen:
            self.folder.setText(str(Path(chosen)))

    def _save(self) -> None:
        new = BackupSettings(self.folder.text().strip(), self.keep.value(), self.interval.value())
        if new == self.settings:
            QMessageBox.information(self, "Backups", "Nothing was changed.")
            return
        reason = ask_reason(self, "Backup settings", "Change where and how often the database is backed up.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                backup.set_settings(session, self.user, new, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.refresh()
        QMessageBox.information(self, "Backups", "Backup settings saved.")

    def _backup_now(self) -> None:
        try:
            path = backup.backup_now(self.db, self.user)
        except AurumError as exc:
            show_error(self, exc, "Backup failed")
            return
        self.refresh()
        QMessageBox.information(self, "Backups", f"Backup made and checked:\n\n{path}")

    def _verify(self) -> None:
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows):
            QMessageBox.information(self, "Backups", "Select a backup first.")
            return
        ok, message = backup.verify(self.rows[row].path)
        (QMessageBox.information if ok else QMessageBox.warning)(self, "Backups", f"{self.rows[row].path.name}\n\n{message}")

    def _open_folder(self) -> None:
        folder = self.settings.path
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
