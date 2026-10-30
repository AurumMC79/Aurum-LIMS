"""Barcode scanning settings for the intake form (opened from the form designer)."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.services.barcodes import COMMON_AIS, ScanMode, ScanSettings, ai_title, read_scan
from aurum_lims.services.intake import FieldSpec, FieldType
from aurum_lims.ui.common import GridTable, muted_label

SEPARATORS = [("|", "Bar  |"), (",", "Comma  ,"), (";", "Semicolon  ;"), ("\t", "Tab"), ("/", "Slash  /"),
              ("*", "Star  *")]
START_POSITIONS = 6


class ScanSettingsDialog(QDialog):
    def __init__(self, parent: QWidget, fields: list[FieldSpec], settings: ScanSettings):
        super().__init__(parent)
        self.targets = [(f.key, f.label) for f in fields if f.type != FieldType.HEADING]
        self.setWindowTitle("Barcode scanning")
        self.resize(900, 820)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(
            "Most USB / Bluetooth scanners type the code and press Enter, so no set-up is needed on the computer. "
            "Choose below which form field each part of a barcode fills, then try a code in the test box."))
        top = QFormLayout()
        self.enabled = QCheckBox("Show the Scan box on the Sample Intaking page")
        self.enabled.setChecked(settings.enabled)
        self.mode = QComboBox()
        for mode in ScanMode:
            self.mode.addItem(mode.label, mode.value)
        self.mode.setCurrentIndex(max(self.mode.findData(settings.mode), 0))
        top.addRow("", self.enabled)
        top.addRow("Barcodes contain", self.mode)
        layout.addLayout(top)

        # ----- GS1 -----
        self.gs1_box = QGroupBox("GS1 supplier barcodes – what each value fills")
        gs1_layout = QVBoxLayout(self.gs1_box)
        self.gs1_table = GridTable(len(COMMON_AIS), 3)
        self.gs1_table.setHorizontalHeaderLabels(["AI", "Value", "Fills form field"])
        self.gs1_table.verticalHeader().hide()
        self.gs1_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.gs1_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.gs1_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.gs1_combos: dict[str, QComboBox] = {}
        for row, ai in enumerate(COMMON_AIS):
            for column, text in enumerate((f"({ai})", ai_title(ai))):
                item = QTableWidgetItem(text)
                item.setFlags(Qt.ItemIsEnabled)
                self.gs1_table.setItem(row, column, item)
            combo = self._target_combo(settings.gs1_fields.get(ai, ""))
            self.gs1_combos[ai] = combo
            self.gs1_table.setCellWidget(row, 2, combo)
        self.gs1_table.setMinimumHeight(220)
        gs1_layout.addWidget(self.gs1_table)
        substitute = QHBoxLayout()
        substitute.addWidget(QLabel("If the scanner cannot send the GS separator, the character it sends instead:"))
        self.gs_substitute = QLineEdit(settings.gs_substitute)
        self.gs_substitute.setMaxLength(1)
        self.gs_substitute.setFixedWidth(40)
        self.gs_substitute.setPlaceholderText("–")
        substitute.addWidget(self.gs_substitute)
        substitute.addStretch()
        gs1_layout.addLayout(substitute)
        layout.addWidget(self.gs1_box)

        # ----- separated values and single value -----
        lower = QHBoxLayout()
        self.split_box = QGroupBox("Values split by a separator")
        split_layout = QVBoxLayout(self.split_box)
        sep_row = QHBoxLayout()
        sep_row.addWidget(QLabel("Separator"))
        self.separator = QComboBox()
        for value, text in SEPARATORS:
            self.separator.addItem(text, value)
        index = self.separator.findData(settings.separator)
        if index < 0:
            self.separator.addItem(settings.separator, settings.separator)
            index = self.separator.count() - 1
        self.separator.setCurrentIndex(index)
        sep_row.addWidget(self.separator)
        sep_row.addStretch()
        split_layout.addLayout(sep_row)
        self.positions = GridTable(0, 2)
        self.positions.setHorizontalHeaderLabels(["Position", "Fills form field"])
        self.positions.verticalHeader().hide()
        self.positions.setSelectionMode(QAbstractItemView.NoSelection)
        self.positions.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.positions.setMinimumHeight(190)
        for key in settings.positions or [""] * START_POSITIONS:
            self._add_position(key)
        while self.positions.rowCount() < START_POSITIONS:
            self._add_position("")
        split_layout.addWidget(self.positions)
        add = QPushButton("Add position")
        add.clicked.connect(lambda *_: self._add_position(""))
        split_layout.addWidget(add, 0, Qt.AlignLeft)
        lower.addWidget(self.split_box, 3)
        self.single_box = QGroupBox("Single value")
        single_layout = QVBoxLayout(self.single_box)
        single_layout.addWidget(QLabel("The whole code fills:"))
        self.single = self._target_combo(settings.single_field)
        single_layout.addWidget(self.single)
        single_layout.addStretch()
        lower.addWidget(self.single_box, 2)
        layout.addLayout(lower)

        # ----- test -----
        test_box = QGroupBox("Try a code")
        test_layout = QVBoxLayout(test_box)
        self.test = QLineEdit()
        self.test.setPlaceholderText("Scan or paste a barcode here, e.g. (01)09501101530003(17)261231(10)ABC123")
        self.test.textChanged.connect(lambda *_: self._try())
        test_layout.addWidget(self.test)
        self.test_result = QLabel("")
        self.test_result.setWordWrap(True)
        self.test_result.setTextFormat(Qt.RichText)
        test_layout.addWidget(self.test_result)
        layout.addWidget(test_box)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use these settings")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        layout.addWidget(muted_label("The settings are saved with the form when you choose Save as new version."))
        self.mode.currentIndexChanged.connect(lambda *_: self._sync())
        self.separator.currentIndexChanged.connect(lambda *_: self._try())
        self.gs_substitute.textChanged.connect(lambda *_: self._try())
        self._sync()

    def _target_combo(self, key: str) -> QComboBox:
        combo = QComboBox()
        combo.addItem("(not used)", "")
        for target, label in self.targets:
            combo.addItem(label, target)
        combo.setCurrentIndex(max(combo.findData(key), 0))
        combo.currentIndexChanged.connect(lambda *_: self._try())
        return combo

    def _add_position(self, key: str) -> None:
        row = self.positions.rowCount()
        self.positions.insertRow(row)
        item = QTableWidgetItem(str(row + 1))
        item.setFlags(Qt.ItemIsEnabled)
        item.setTextAlignment(Qt.AlignCenter)
        self.positions.setItem(row, 0, item)
        self.positions.setCellWidget(row, 1, self._target_combo(key))

    def _sync(self) -> None:
        mode = ScanMode(self.mode.currentData())
        self.gs1_box.setEnabled(mode in (ScanMode.AUTO, ScanMode.GS1))
        self.split_box.setEnabled(mode in (ScanMode.AUTO, ScanMode.DELIMITED))
        self.single_box.setEnabled(mode in (ScanMode.AUTO, ScanMode.SINGLE))
        self._try()

    def settings(self) -> ScanSettings:
        positions = [self.positions.cellWidget(r, 1).currentData() for r in range(self.positions.rowCount())]
        while positions and not positions[-1]:
            positions.pop()
        return ScanSettings(self.enabled.isChecked(), self.mode.currentData(),
                            {ai: combo.currentData() for ai, combo in self.gs1_combos.items() if combo.currentData()},
                            self.separator.currentData(), positions, self.single.currentData(),
                            self.gs_substitute.text())

    def _try(self) -> None:
        code = self.test.text()
        if not code.strip():
            self.test_result.setText("")
            return
        result = read_scan(replace(self.settings(), enabled=True), code)
        labels = dict(self.targets)
        lines = [f"<b>Read as: {result.kind}</b>"]
        lines += [f"{what}: <b>{value}</b>" for what, value in result.found]
        if result.values:
            lines.append("<span style='color:#067647'>Fills – " + "; ".join(
                f"{labels.get(k, k)} = {v.isoformat() if hasattr(v, 'isoformat') else v}"
                for k, v in result.values.items()) + "</span>")
        if result.unused:
            lines.append("<span style='color:#6b7785'>Not used – " + "; ".join(result.unused) + "</span>")
        self.test_result.setText("<br/>".join(lines))
