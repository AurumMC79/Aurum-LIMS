"""Preventive-maintenance plans: editing a plan, carrying out its checklist, viewing a completed one."""

from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import instruments, maintenance
from aurum_lims.services.maintenance import Outcome, PlanSpec, TaskResult, TaskSpec
from aurum_lims.ui.common import allow, GridTable, ask_reason, error_label, muted_label, primary_button, show_error, \
    show_label_message

RED, AMBER, GREEN, GREY = "#b42318", "#b54708", "#067647", "#8b98a8"
STATE_COLOURS = {"Overdue": RED, "Due soon": AMBER, "Scheduled": GREEN, "Not scheduled": GREY}


def every(days: int) -> str:
    return "1 day" if days == 1 else f"{days} days"


def _date_edit(value: date) -> QDateEdit:
    edit = QDateEdit(QDate(value.year, value.month, value.day))
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd")
    return edit


def _grid(headers: list[str], stretch: int) -> GridTable:
    table = GridTable(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    return table


class PlanDialog(QDialog):
    """Create or edit a plan: name, interval, due date and the checklist."""

    def __init__(self, parent: QWidget, instrument_code: str, spec: PlanSpec | None = None,
                 due_date: date | None = None):
        super().__init__(parent)
        editing = spec is not None
        spec = spec or PlanSpec("", 30, [TaskSpec("")])
        self.setWindowTitle(f"{'Edit' if editing else 'New'} maintenance plan – {instrument_code}")
        self.resize(820, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(spec.name)
        self.name.setPlaceholderText("e.g. Monthly preventive maintenance, Weekly leak check")
        self.interval = QSpinBox()
        self.interval.setRange(1, 3650)
        self.interval.setSuffix(" days")
        self.interval.setValue(spec.interval_days)
        self.interval.setFixedWidth(130)
        self.due = _date_edit(due_date or date.today() + timedelta(days=spec.interval_days))
        if not editing:
            self.interval.valueChanged.connect(
                lambda days: self.due.setDate(QDate.currentDate().addDays(days)))
        form.addRow("Name *", self.name)
        form.addRow("Repeat every", self.interval)
        form.addRow("Next due" if editing else "First due", self.due)
        layout.addLayout(form)
        layout.addWidget(muted_label(
            "Checklist: one line per task. Tick 'Reading' when a value must be recorded (e.g. lamp energy, "
            "leak rate) and give the acceptance criterion so the person doing it can judge OK / Problem found."))
        self.tasks = _grid(["Task *", "Reading", "Acceptance criterion"], 0)
        self.tasks.verticalHeader().setVisible(True)
        self.tasks.setColumnWidth(1, 70)
        self.tasks.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        for task in spec.tasks:
            self._add(task)
        layout.addWidget(self.tasks, 1)
        row = QHBoxLayout()
        for text, handler in (("Add task", lambda: self._add(TaskSpec(""))), ("Remove", self._remove),
                              ("Move up", lambda: self._move(-1)), ("Move down", lambda: self._move(1))):
            button = QPushButton(text)
            button.clicked.connect(lambda *_, h=handler: h())
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setProperty("primary", True)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add(self, task: TaskSpec, at: int | None = None) -> None:
        row = self.tasks.rowCount() if at is None else at
        self.tasks.insertRow(row)
        text = QLineEdit(task.text)
        text.setPlaceholderText("e.g. Replace pump seals")
        reading = QCheckBox()
        reading.setChecked(task.needs_value)
        holder = QWidget()
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.setAlignment(Qt.AlignCenter)
        holder_layout.addWidget(reading)
        acceptance = QLineEdit(task.acceptance)
        acceptance.setPlaceholderText("e.g. ≥ 1500 counts, no leaks")
        self.tasks.setCellWidget(row, 0, text)
        self.tasks.setCellWidget(row, 1, holder)
        self.tasks.setCellWidget(row, 2, acceptance)

    def _task(self, row: int) -> TaskSpec:
        reading = self.tasks.cellWidget(row, 1).findChild(QCheckBox)
        return TaskSpec(self.tasks.cellWidget(row, 0).text(), reading.isChecked(), self.tasks.cellWidget(row, 2).text())

    def _remove(self) -> None:
        if self.tasks.currentRow() >= 0:
            self.tasks.removeRow(self.tasks.currentRow())

    def _move(self, offset: int) -> None:
        row = self.tasks.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.tasks.rowCount():
            return
        task = self._task(row)
        self.tasks.removeRow(row)
        self._add(task, target)
        self.tasks.selectRow(target)

    def spec(self) -> PlanSpec:
        tasks = [self._task(r) for r in range(self.tasks.rowCount())]
        return PlanSpec(self.name.text(), self.interval.value(), tasks, self.due.date().toPython())

    def _check(self) -> None:
        if not self.name.text().strip():
            show_label_message(self.error, "Give the plan a name.")
            return
        if not any(t.text.strip() for t in self.spec().tasks):
            show_label_message(self.error, "Add at least one task.")
            return
        self.accept()


class PerformDialog(QDialog):
    """Go through a plan's checklist."""

    def __init__(self, parent: QWidget, instrument_code: str, plan_name: str, tasks: list[TaskSpec]):
        super().__init__(parent)
        self.task_specs = tasks
        self.setWindowTitle(f"Perform maintenance – {instrument_code} – {plan_name}")
        self.resize(980, 560)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.performed_on = _date_edit(date.today())
        self.performed_on.setMaximumDate(QDate.currentDate())
        self.performed_by = QLineEdit()
        self.performed_by.setPlaceholderText("Leave empty if you did it yourself; e.g. Agilent field engineer")
        form.addRow("Performed on", self.performed_on)
        form.addRow("Performed by", self.performed_by)
        layout.addLayout(form)
        layout.addWidget(muted_label(
            "Mark every task. 'Problem found' needs a description and takes the instrument out of service; "
            "'N/A' needs a reason."))
        self.table = _grid(["Task", "Acceptance", "Outcome", "Reading", "Note"], 4)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setRowCount(len(tasks))
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.setColumnWidth(0, 260)
        for r, task in enumerate(tasks):
            label = QTableWidgetItem(task.text)
            label.setFlags(Qt.ItemIsEnabled)
            acceptance = QTableWidgetItem(task.acceptance)
            acceptance.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, label)
            self.table.setItem(r, 1, acceptance)
            outcome = QComboBox()
            outcome.addItems(["", *[o.value for o in Outcome]])
            outcome.currentIndexChanged.connect(lambda *_: self._update_result())
            reading = QLineEdit()
            reading.setPlaceholderText("required" if task.needs_value else "")
            reading.setEnabled(task.needs_value)
            self.table.setCellWidget(r, 2, outcome)
            self.table.setCellWidget(r, 3, reading)
            self.table.setCellWidget(r, 4, QLineEdit())
        layout.addWidget(self.table, 1)
        layout.addWidget(QLabel("Notes"))
        self.notes = QPlainTextEdit()
        self.notes.setFixedHeight(50)
        layout.addWidget(self.notes)
        self.result_label = QLabel()
        layout.addWidget(self.result_label)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Record maintenance")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._check)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_result()

    def results(self) -> list[TaskResult]:
        return [TaskResult(self.table.cellWidget(r, 2).currentText(), self.table.cellWidget(r, 3).text(),
                           self.table.cellWidget(r, 4).text()) for r in range(self.table.rowCount())]

    def _update_result(self) -> None:
        outcomes = [r.outcome for r in self.results()]
        if Outcome.PROBLEM in outcomes:
            self.result_label.setText(f"<b style='color:{RED}'>Result: Fail</b> – the instrument will be taken out "
                                      "of service.")
        elif all(outcomes):
            self.result_label.setText(f"<b style='color:{GREEN}'>Result: Pass</b> – the next due date moves on.")
        else:
            self.result_label.setText(f"{sum(1 for o in outcomes if o)} of {len(outcomes)} tasks marked.")

    def _check(self) -> None:
        if not all(r.outcome for r in self.results()):
            show_label_message(self.error, "Mark every task OK, Problem found or N/A.")
            return
        self.accept()


class ChecklistDialog(QDialog):
    """A completed checklist from the instrument history (read-only)."""

    def __init__(self, parent: QWidget, title: str, checklist: list[dict]):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 420)
        layout = QVBoxLayout(self)
        table = _grid(["Task", "Acceptance", "Outcome", "Reading", "Note"], 4)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setRowCount(len(checklist))
        for r, line in enumerate(checklist):
            for c, key in enumerate(["task", "acceptance", "outcome", "value", "note"]):
                item = QTableWidgetItem(line.get(key) or "")
                if key == "outcome":
                    item.setForeground(QColor(RED if line[key] == Outcome.PROBLEM else
                                              GREY if line[key] == Outcome.NOT_APPLICABLE else GREEN))
                table.setItem(r, c, item)
        layout.addWidget(table, 1)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)


class CopyPlanDialog(QDialog):
    def __init__(self, parent: QWidget, plan_name: str, choices: list[tuple[int, str, bool]]):
        """``choices``: (instrument id, label, same category)."""
        super().__init__(parent)
        self.setWindowTitle(f"Copy '{plan_name}' to other instruments")
        self.setMinimumSize(460, 420)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label("Tick the instruments that should get the same plan. Instruments of the same "
                                     "category are listed first; each copy is first due one interval from today."))
        self.list = QListWidget()
        for instrument_id, label, _ in sorted(choices, key=lambda c: (not c[2], c[1])):
            item = QListWidgetItem(label)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            item.setData(Qt.UserRole, instrument_id)
            self.list.addItem(item)
        layout.addWidget(self.list, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Copy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def chosen(self) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole) for i in range(self.list.count())
                if self.list.item(i).checkState() == Qt.Checked]


class PlansPanel(QWidget):
    """The maintenance plans of the selected instrument (shown under the instrument list)."""

    COLUMNS = ["Plan", "Every", "Tasks", "Last done", "Next due", "Status"]

    def __init__(self, db: Database, user: SessionUser, on_change, parent=None):
        super().__init__(parent)
        self.db, self.user, self.on_change = db, user, on_change
        self.instrument = None
        self.plans = []
        can_manage = user.can(Permission.MANAGE_MASTER_DATA)
        can_record = can_manage or user.can(Permission.EDIT_RECORDS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 6, 0, 0)
        bar = QHBoxLayout()
        self.perform_button = primary_button("Perform…")
        self.perform_button.setDefault(False)
        self.new_button = QPushButton("New plan…")
        self.edit_button = QPushButton("Edit…")
        self.copy_button = QPushButton("Copy to…")
        self.active_button = QPushButton("Deactivate")
        self.show_inactive = QCheckBox("Show inactive")
        allow(self.perform_button, can_record)
        for b in (self.new_button, self.edit_button, self.copy_button, self.active_button):
            allow(b, can_manage)
        for b in (self.perform_button, self.new_button, self.edit_button, self.copy_button, self.active_button):
            bar.addWidget(b)
        bar.addStretch()
        bar.addWidget(self.show_inactive)
        layout.addLayout(bar)
        self.table = _grid(self.COLUMNS, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        self.perform_button.clicked.connect(lambda *_: self._perform())
        self.new_button.clicked.connect(lambda *_: self._new())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.copy_button.clicked.connect(lambda *_: self._copy())
        self.active_button.clicked.connect(lambda *_: self._toggle())
        self.show_inactive.toggled.connect(lambda *_: self.show_instrument(self.instrument))
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.doubleClicked.connect(lambda *_: self._perform() if can_record else None)
        self._update_buttons()

    def show_instrument(self, instrument) -> None:
        self.instrument = instrument
        self.plans = []
        if instrument is not None:
            with self.db.session() as session:
                self.plans = maintenance.plans_for(session, instrument.id, self.show_inactive.isChecked())
        today = date.today()
        self.table.setRowCount(len(self.plans))
        for r, plan in enumerate(self.plans):
            row = maintenance.ScheduleRow(plan.id, plan.instrument_id, "", "", plan.name, plan.interval_days,
                                          plan.due_date, plan.last_done, len(maintenance.tasks_of(plan)))
            state = row.state(today) if plan.is_active else "Inactive"
            values = [plan.name, every(plan.interval_days), str(row.task_count),
                      plan.last_done.isoformat() if plan.last_done else "—",
                      plan.due_date.isoformat() if plan.due_date else "—", state]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c in (4, 5):
                    item.setForeground(QColor(STATE_COLOURS.get(state, GREY)))
                self.table.setItem(r, c, item)
        self.new_button.setEnabled(instrument is not None and instrument.status != "Retired")
        self._update_buttons()

    def _selected(self):
        row = self.table.currentRow()
        return self.plans[row] if 0 <= row < len(self.plans) and self.table.selectedItems() else None

    def _update_buttons(self) -> None:
        plan = self._selected()
        usable = plan is not None and self.instrument is not None and self.instrument.status != "Retired"
        self.perform_button.setEnabled(usable and plan.is_active)
        self.edit_button.setEnabled(usable)
        self.copy_button.setEnabled(usable)
        self.active_button.setEnabled(usable)
        self.active_button.setText("Activate" if plan is not None and not plan.is_active else "Deactivate")

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                result = action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.on_change()
        return result if result is not None else True

    def _new(self) -> None:
        dialog = PlanDialog(self, self.instrument.code)
        while dialog.exec() == QDialog.Accepted:
            if self._run(lambda s: maintenance.create_plan(s, self.user, self.instrument.id, dialog.spec())):
                return

    def _edit(self) -> None:
        plan = self._selected()
        if plan is None:
            return
        dialog = PlanDialog(self, self.instrument.code,
                            PlanSpec(plan.name, plan.interval_days, maintenance.tasks_of(plan)), plan.due_date)
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit maintenance plan", f"Changing '{plan.name}' on {self.instrument.code}.")
            spec = dialog.spec()
            if reason is not None and self._run(lambda s: maintenance.update_plan(
                    s, self.user, plan.id, spec, reason, due_date=spec.first_due)):
                return

    def _perform(self) -> None:
        plan = self._selected()
        if plan is None or not plan.is_active:
            return
        perform_plan(self, self.db, self.user, plan.id, self.instrument.code, plan.name, maintenance.tasks_of(plan),
                     self.on_change)

    def _copy(self) -> None:
        plan = self._selected()
        if plan is None:
            return
        with self.db.session() as session:
            choices = [(i.id, f"{i.code} – {i.name} ({i.category})", i.category == self.instrument.category)
                       for i in instruments.list_instruments(session) if i.id != self.instrument.id]
        dialog = CopyPlanDialog(self, plan.name, choices)
        if dialog.exec() != QDialog.Accepted or not dialog.chosen():
            return
        copied = []
        if self._run(lambda s: copied.extend(maintenance.copy_plan(s, self.user, plan.id, dialog.chosen()))):
            skipped = len(dialog.chosen()) - len(copied)
            QMessageBox.information(self, "Plan copied", f"Copied to {', '.join(copied) or 'no instruments'}."
                                    + (f" {skipped} already had a plan with this name." if skipped else ""))

    def _toggle(self) -> None:
        plan = self._selected()
        if plan is None:
            return
        activate = not plan.is_active
        reason = ask_reason(self, "Maintenance plan", f"{'Activate' if activate else 'Deactivate'} '{plan.name}'.")
        if reason is not None:
            self._run(lambda s: maintenance.set_active(s, self.user, plan.id, activate, reason))


def perform_plan(parent: QWidget, db: Database, user: SessionUser, plan_id: int, code: str, name: str,
                 tasks: list[TaskSpec], on_done) -> bool:
    dialog = PerformDialog(parent, code, name, tasks)
    while dialog.exec() == QDialog.Accepted:
        try:
            with db.session() as session:
                maintenance.perform(session, user, plan_id, dialog.results(),
                                    performed_on=dialog.performed_on.date().toPython(),
                                    performed_by=dialog.performed_by.text(), notes=dialog.notes.toPlainText())
        except AurumError as exc:
            show_label_message(dialog.error, str(exc))
            continue
        on_done()
        return True
    return False


class ScheduleDialog(QDialog):
    """Every active plan across all instruments, soonest due first."""

    COLUMNS = ["Instrument", "Name", "Plan", "Every", "Last done", "Next due", "Status"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.rows = []
        self.setWindowTitle("Maintenance schedule")
        self.resize(900, 520)
        layout = QVBoxLayout(self)
        self.table = _grid(self.COLUMNS, 1)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)
        bar = QHBoxLayout()
        self.perform_button = primary_button("Perform…")
        self.perform_button.setDefault(False)
        allow(self.perform_button, user.can(Permission.MANAGE_MASTER_DATA) or user.can(Permission.EDIT_RECORDS))
        self.perform_button.clicked.connect(lambda *_: self._perform())
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        bar.addWidget(self.perform_button)
        bar.addStretch()
        bar.addWidget(close)
        layout.addLayout(bar)
        self.refresh()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.rows = maintenance.schedule(session)
        today = date.today()
        self.table.setRowCount(len(self.rows))
        for r, row in enumerate(self.rows):
            state = row.state(today)
            values = [row.instrument_code, row.instrument_name, row.plan_name, every(row.interval_days),
                      row.last_done.isoformat() if row.last_done else "—",
                      row.due_date.isoformat() if row.due_date else "—", state]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c in (5, 6):
                    item.setForeground(QColor(STATE_COLOURS.get(state, GREY)))
                self.table.setItem(r, c, item)

    def _perform(self) -> None:
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            return
        row = self.rows[index]
        with self.db.session() as session:
            tasks = maintenance.tasks_of(session.get(maintenance.MaintenancePlan, row.plan_id))
        perform_plan(self, self.db, self.user, row.plan_id, row.instrument_code, row.plan_name, tasks, self.refresh)
