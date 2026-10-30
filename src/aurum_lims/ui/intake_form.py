"""Draws an intake form from its field list and reads back what was entered.

Used by the Sample Intaking page and by the form designer's live preview, so
the preview always looks exactly like the real form.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from PySide6.QtCore import QDate, QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDateTimeEdit,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QWidget,
)

from aurum_lims.services.barcodes import parse_date_text
from aurum_lims.services.intake import FieldSpec, FieldType

PLACEHOLDER = "— choose —"


class _Optional(QWidget):
    """A date or date-time that can be left empty (tick to fill in)."""

    def __init__(self, editor: QWidget, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.check = QCheckBox()
        self.check.setToolTip("Tick to enter a value")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.check)
        layout.addWidget(editor)
        layout.addStretch()
        editor.setEnabled(False)
        self.check.toggled.connect(editor.setEnabled)


class IntakeFormWidget(QWidget):
    def __init__(self, fields: list[FieldSpec], sample_types: list[tuple[int, str]],
                 locations: list[tuple[int, str]], parent=None):
        super().__init__(parent)
        self.fields = fields
        self.sample_types, self.locations = sample_types, locations
        self.widgets: dict[str, QWidget] = {}
        self.form = QFormLayout(self)
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setVerticalSpacing(8)
        self.form.setHorizontalSpacing(14)
        for f in fields:
            self._add(f)
        self.reset()

    # ----- building -----
    def _add(self, f: FieldSpec) -> None:
        kind = FieldType(f.type)
        if kind is FieldType.HEADING:
            label = QLabel(f.label)
            label.setObjectName("CardTitle")
            label.setStyleSheet("margin-top: 10px; border-bottom: 1px solid #c9a227; padding-bottom: 2px")
            self.form.addRow(label)
            return
        if kind is FieldType.TEXT:
            widget = QLineEdit()
        elif kind is FieldType.MULTILINE:
            widget = QPlainTextEdit()
            widget.setFixedHeight(58)
        elif kind is FieldType.NUMBER:
            widget = QLineEdit()
            widget.setAlignment(Qt.AlignRight)
            widget.setMaximumWidth(180)
            widget.setPlaceholderText("number")
        elif kind is FieldType.DATE:
            editor = QDateEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd")
            widget = editor if f.required else _Optional(editor)
        elif kind is FieldType.DATETIME:
            editor = QDateTimeEdit()
            editor.setCalendarPopup(True)
            editor.setDisplayFormat("yyyy-MM-dd HH:mm")
            widget = editor if f.required else _Optional(editor)
        elif kind is FieldType.DROPDOWN:
            widget = QComboBox()
            widget.setMinimumWidth(240)
            widget.addItem(PLACEHOLDER if f.required else "", None)
            if f.key == "sample_type":
                options = self.sample_types
            elif f.key == "location_id":
                options = self.locations
            else:
                options = [(c, c) for c in f.choices]
            for value, text in options:
                widget.addItem(text, value)
        else:  # checkbox
            widget = QCheckBox()
        if f.help:
            widget.setToolTip(f.help)
            if isinstance(widget, QLineEdit) and not widget.placeholderText():
                widget.setPlaceholderText(f.help[:80])
        label = QLabel(f"{f.label} *" if f.required else f.label)
        if f.help:
            label.setToolTip(f.help)
        self.widgets[f.key] = widget
        self.form.addRow(label, widget)

    # ----- values -----
    def reset(self) -> None:
        """Empty the form (defaults filled in) for the next sample."""
        now = QDateTime.currentDateTime()
        for f in self.fields:
            widget = self.widgets.get(f.key)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                widget.setText(f.default)
            elif isinstance(widget, QPlainTextEdit):
                widget.setPlainText(f.default)
            elif isinstance(widget, QComboBox):
                widget.setCurrentIndex(max(widget.findText(f.default), 0) if f.default else 0)
            elif isinstance(widget, QCheckBox):
                widget.setChecked(f.default.lower() in ("yes", "true", "1"))
            else:
                editor = widget.editor if isinstance(widget, _Optional) else widget
                if isinstance(widget, _Optional):
                    widget.check.setChecked(False)
                if isinstance(editor, QDateTimeEdit) and not isinstance(editor, QDateEdit):
                    editor.setDateTime(now)
                else:
                    editor.setDate(QDate.currentDate())

    def values(self) -> dict:
        out = {}
        for f in self.fields:
            widget = self.widgets.get(f.key)
            if widget is None:
                continue
            if isinstance(widget, QLineEdit):
                out[f.key] = widget.text()
            elif isinstance(widget, QPlainTextEdit):
                out[f.key] = widget.toPlainText()
            elif isinstance(widget, QComboBox):
                out[f.key] = widget.currentData()
            elif isinstance(widget, QCheckBox):
                out[f.key] = widget.isChecked()
            else:
                if isinstance(widget, _Optional) and not widget.check.isChecked():
                    out[f.key] = None
                    continue
                editor = widget.editor if isinstance(widget, _Optional) else widget
                if FieldType(f.type) is FieldType.DATE:
                    out[f.key] = editor.date().toPython()
                else:
                    out[f.key] = editor.dateTime().toPython().astimezone().astimezone(timezone.utc)
        return out

    def set_value(self, key: str, value) -> bool:
        """Fill one field from a scan. Returns False when the value does not fit the field."""
        widget = self.widgets.get(key)
        if widget is None or value is None:
            return False
        text = value.isoformat() if isinstance(value, date) else str(value).strip()
        if isinstance(widget, QLineEdit):
            widget.setText(text)
        elif isinstance(widget, QPlainTextEdit):
            widget.setPlainText(text)
        elif isinstance(widget, QComboBox):
            wanted = text.lower()
            index = next((i for i in range(1, widget.count()) if str(widget.itemData(i)).lower() == wanted
                          or widget.itemText(i).lower() == wanted), -1)
            if index < 0:  # e.g. "RM" picks "RM – Raw Material"
                index = next((i for i in range(1, widget.count())
                              if widget.itemText(i).lower().startswith(f"{wanted} ")), -1)
            if index < 0:
                return False
            widget.setCurrentIndex(index)
        elif isinstance(widget, QCheckBox):
            widget.setChecked(text.lower() in ("1", "y", "yes", "true", "pass", "ok", "x"))
        else:
            editor = widget.editor if isinstance(widget, _Optional) else widget
            moment = value if isinstance(value, (date, datetime)) else parse_date_text(text)
            if moment is None:
                return False
            if isinstance(widget, _Optional):
                widget.check.setChecked(True)
            if isinstance(editor, QDateEdit):
                editor.setDate(QDate(moment.year, moment.month, moment.day))
            else:
                if not isinstance(moment, datetime):
                    moment = datetime(moment.year, moment.month, moment.day)
                editor.setDateTime(QDateTime(QDate(moment.year, moment.month, moment.day),
                                             QDateTime.currentDateTime().time() if moment.hour == moment.minute == 0
                                             else QDateTime(moment).time()))
        return True

    def label_of(self, key: str) -> str:
        return next((f.label for f in self.fields if f.key == key), key)

    def first_input(self) -> QWidget | None:
        return next(iter(self.widgets.values()), None)

