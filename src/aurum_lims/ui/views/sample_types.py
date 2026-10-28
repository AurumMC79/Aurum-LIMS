"""Sample Types page: the lab-wide sample ID format and the list of sample types."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.services import sample_ids, sample_types
from aurum_lims.services.sample_ids import TOKEN_HELP, ResetPolicy
from aurum_lims.services.sample_types import Category
from aurum_lims.ui.common import Card, ask_reason, error_label, muted_label, primary_button, show_error, show_label_message


class FormatEditor(QWidget):
    """Format text box + restart policy + live preview and problems."""

    def __init__(self, pattern: str = "", reset: str | None = None, type_code: str = "RM", parent=None):
        super().__init__(parent)
        self.type_code = type_code
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        self.pattern = QLineEdit(pattern)
        self.pattern.setPlaceholderText("e.g. AU-{YYYY}-{SEQ:5}")
        self.reset = QComboBox()
        for policy in ResetPolicy:
            self.reset.addItem(f"Restart numbering: {policy.label}", policy.value)
        self.reset.setCurrentIndex(list(ResetPolicy).index(ResetPolicy(reset or sample_ids.DEFAULT_RESET)))
        row.addWidget(self.pattern, 2)
        row.addWidget(self.reset, 1)
        layout.addLayout(row)
        self.preview = QLabel()
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        tokens = "   ".join(f"<b>{token}</b> {help_text}" for token, help_text in TOKEN_HELP)
        help_label = muted_label("")
        help_label.setText(f"<span style='color:#6b7785'>{tokens}</span>")
        layout.addWidget(help_label)
        self.pattern.textChanged.connect(lambda *_: self._update())
        self.reset.currentIndexChanged.connect(lambda *_: self._update())
        self._update()

    def values(self) -> tuple[str, str]:
        return self.pattern.text().strip(), self.reset.currentData()

    def _update(self) -> None:
        pattern, reset = self.values()
        if not pattern:
            self.preview.setText("")
            return
        problems = sample_ids.format_problems(pattern, reset)
        if problems:
            self.preview.setText("<span style='color:#b42318'>" + "<br>".join(problems) + "</span>")
        else:
            examples = ", ".join(sample_ids.preview(pattern, reset, self.type_code))
            self.preview.setText(f"<span style='color:#067647'>Example IDs: <b>{examples}</b> …</span>")


class SampleTypeDialog(QDialog):
    def __init__(self, parent=None, existing=None):
        super().__init__(parent)
        self.setWindowTitle(f"Edit {existing.name}" if existing else "New sample type")
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(existing.name if existing else "")
        self.code = QLineEdit(existing.code if existing else "")
        self.code.setPlaceholderText("Short code, e.g. RM")
        self.code.setMaxLength(16)
        self.category = QComboBox()
        for category in Category:
            self.category.addItem(category.label, category.value)
        if existing:
            self.category.setCurrentIndex(list(Category).index(Category(existing.category)))
        self.description = QPlainTextEdit(existing.description or "" if existing else "")
        self.description.setFixedHeight(54)
        self.own_format = QCheckBox("Use its own sample ID format (instead of the lab-wide format)")
        self.own_format.setChecked(bool(existing and existing.id_format))
        self.format_editor = FormatEditor(
            existing.id_format or "" if existing else "", existing.id_reset if existing else None,
            existing.code if existing else "RM",
        )
        self.format_editor.setEnabled(self.own_format.isChecked())
        self.own_format.toggled.connect(self.format_editor.setEnabled)
        self.code.textChanged.connect(lambda text: setattr(self.format_editor, "type_code", text.upper() or "RM"))
        self.active = QCheckBox("Active (available when registering samples)")
        self.active.setChecked(existing.is_active if existing else True)
        form.addRow("Name *", self.name)
        form.addRow("Code *", self.code)
        form.addRow("Category", self.category)
        form.addRow("Description", self.description)
        layout.addLayout(form)
        layout.addWidget(muted_label("The category decides which fields are asked for when registering a sample."))
        layout.addWidget(self.own_format)
        layout.addWidget(self.format_editor)
        if existing:
            layout.addWidget(self.active)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _check(self) -> None:
        if self.own_format.isChecked():
            problems = sample_ids.format_problems(*self.format_editor.values())
            if problems:
                show_label_message(self.error, " ".join(problems))
                return
        self.accept()

    def values(self) -> dict:
        pattern, reset = self.format_editor.values()
        own = self.own_format.isChecked()
        return {
            "name": self.name.text(),
            "code": self.code.text(),
            "category": self.category.currentData(),
            "description": self.description.toPlainText(),
            "id_format": pattern if own else None,
            "id_reset": reset if own else None,
        }


class SampleTypesView(QWidget):
    title = "Sample Types"
    COLUMNS = ["Name", "Code", "Category", "Sample ID format", "Status"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.types = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        format_card = Card("Lab-wide sample ID format")
        format_card.body.addWidget(muted_label(
            "Used by every sample type that does not have its own format. Changing it does not change existing IDs."
        ))
        self.lab_format = FormatEditor()
        format_card.body.addWidget(self.lab_format)
        save_row = QHBoxLayout()
        save_row.addStretch()
        save_format = primary_button("Save format")
        save_format.setDefault(False)
        save_format.clicked.connect(lambda *_: self._save_format())
        save_row.addWidget(save_format)
        format_card.body.addLayout(save_row)
        layout.addWidget(format_card)

        toolbar = QHBoxLayout()
        add = primary_button("New sample type")
        add.setDefault(False)
        self.edit_button = QPushButton("Edit")
        add.clicked.connect(lambda *_: self._add())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        toolbar.addWidget(add)
        toolbar.addWidget(self.edit_button)
        toolbar.addStretch()
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
        self.table.itemSelectionChanged.connect(lambda: self.edit_button.setEnabled(self._selected() is not None))
        self.table.doubleClicked.connect(lambda *_: self._edit())
        layout.addWidget(self.table, 1)
        self.edit_button.setEnabled(False)

    def refresh(self) -> None:
        with self.db.session() as session:
            fmt = sample_ids.get_lab_format(session)
            self.types = sample_types.list_types(session, include_inactive=True)
        self.lab_format.pattern.setText(fmt.pattern)
        self.lab_format.reset.setCurrentIndex(list(ResetPolicy).index(fmt.reset))
        self.table.setRowCount(len(self.types))
        for r, t in enumerate(self.types):
            fmt_text = (
                f"{t.id_format}  (restart: {ResetPolicy(t.id_reset or 'yearly').label.lower()})"
                if t.id_format else "Lab-wide format"
            )
            values = [t.name, t.code, Category(t.category).label, fmt_text, "Active" if t.is_active else "Inactive"]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if not t.is_active:
                    item.setForeground(QColor("#8b98a8"))
                self.table.setItem(r, c, item)

    def _selected(self):
        model = self.table.selectionModel()
        indexes = model.selectedRows() if model else []
        return self.types[indexes[0].row()] if indexes and indexes[0].row() < len(self.types) else None

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    def _save_format(self) -> None:
        pattern, reset = self.lab_format.values()
        problems = sample_ids.format_problems(pattern, reset)
        if problems:
            show_error(self, " ".join(problems), "Invalid format")
            return
        reason = ask_reason(self, "Change sample ID format", f"New lab-wide format: {pattern}")
        if reason is not None:
            self._run(lambda s: sample_ids.set_lab_format(s, self.user, pattern, reset, reason))

    def _add(self) -> None:
        dialog = SampleTypeDialog(self)
        if dialog.exec() == QDialog.Accepted:
            self._run(lambda s: sample_types.create_type(s, self.user, **dialog.values()))

    def _edit(self) -> None:
        existing = self._selected()
        if existing is None:
            return
        dialog = SampleTypeDialog(self, existing)
        if dialog.exec() != QDialog.Accepted:
            return
        reason = ask_reason(self, "Edit sample type", f"Changing {existing.name}.")
        if reason is not None:
            self._run(lambda s: sample_types.update_type(
                s, self.user, existing.id, reason=reason, is_active=dialog.active.isChecked(), **dialog.values()
            ))
