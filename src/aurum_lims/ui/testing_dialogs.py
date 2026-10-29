"""Assigning tests to a sample, and entering / signing results."""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.db.models import Analysis, Sample, Specification, TestMethod, User
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, has_permission
from aurum_lims.services import formulas, methods, review, specifications, testing, usage
from aurum_lims.services.esign import SignatureMeaning
from aurum_lims.services.formulas import Conformance
from aurum_lims.services.methods import ResultType, to_spec
from aurum_lims.services.testing import OPEN_ANALYSIS, AnalysisStatus, Calculation
from aurum_lims.ui.common import (
    Card,
    GridTable,
    ask_reason,
    error_label,
    format_local,
    muted_label,
    show_error,
    show_label_message,
)
from aurum_lims.ui.sign_dialog import SignDialog
from aurum_lims.ui.usage_widgets import RecordUsageDialog, usage_table

CONFORMANCE_COLORS = {Conformance.PASS: "#067647", Conformance.FAIL: "#b42318", Conformance.REPORT: "#4a5562"}


class AssignTestsDialog(QDialog):
    """Pick the specification and the tests for a sample (by hand)."""

    def __init__(self, db: Database, user: SessionUser, sample_pk: int, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        self.setMinimumSize(620, 600)
        with db.session() as session:
            sample = session.get(Sample, sample_pk)
            self.sample_id = sample.sample_id
            self.current_spec = sample.specification_id
            suggested = specifications.suggestions_for(session, sample.material_name, sample.sample_type_id)
            all_specs = specifications.active_specs(session)
            if sample.specification_id and all(s.id != sample.specification_id for s in all_specs):
                all_specs.append(session.get(Specification, sample.specification_id))
            self.spec_methods = {
                s.id: {limit.method_code for limit in specifications.limits_of(session, s.id)} for s in all_specs
            }
            self.methods = [(m.id, m.code, m.name, m.version) for m in methods.active_methods(session)]
            existing = testing.analyses_for_sample(session, user, sample_pk)
            people = [(u.id, u.full_name) for u in session.query(User).filter(User.is_active.is_(True))
                      .order_by(User.full_name) if has_permission(u.role, Permission.EDIT_RECORDS)]
            material = sample.material_name or sample.sampling_point or ""
        self.setWindowTitle(f"Tests for {self.sample_id}")

        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(f"{self.sample_id} · {material}"))
        form = QFormLayout()
        self.spec = QComboBox()
        self.spec.addItem("(no specification – results are reported without limits)", None)
        suggested_ids = {s.id for s in suggested}
        for s in suggested + [s for s in all_specs if s.id not in suggested_ids]:
            tag = "  ★ matches this sample" if s.id in suggested_ids else ""
            self.spec.addItem(f"{s.code} v{s.version} – {s.name}{tag}", s.id)
        if self.current_spec:
            self.spec.setCurrentIndex(max(self.spec.findData(self.current_spec), 0))
        elif suggested:
            self.spec.setCurrentIndex(1)
        self.assignee = QComboBox()
        self.assignee.addItem("(not assigned to anyone yet)", None)
        for user_id, name in people:
            self.assignee.addItem(name, user_id)
        form.addRow("Specification", self.spec)
        form.addRow("Assign to", self.assignee)
        layout.addLayout(form)

        heading = QHBoxLayout()
        heading.addWidget(QLabel("<b>Tests to add</b>"))
        heading.addStretch()
        tick = QPushButton("Tick the tests in the specification")
        tick.clicked.connect(lambda *_: self._tick_from_spec())
        heading.addWidget(tick)
        layout.addLayout(heading)
        self.method_list = QListWidget()
        for method_id, code, name, version in self.methods:
            item = QListWidgetItem(f"{code} v{version} – {name}")
            item.setData(Qt.UserRole, method_id)
            item.setData(Qt.UserRole + 1, code)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked)
            self.method_list.addItem(item)
        layout.addWidget(self.method_list, 1)
        if not self.methods:
            layout.addWidget(muted_label("No active methods yet. Methods are set up on the Methods page."))
        if existing:
            layout.addWidget(QLabel("<b>Tests already on this sample</b>"))
            layout.addWidget(muted_label("\n".join(
                f"{a.method_code} v{a.method_version} – {a.status}" + (f" ({a.assigned_to})" if a.assigned_to else "")
                for a in existing)))
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Save")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self._submit)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _tick_from_spec(self) -> None:
        codes = self.spec_methods.get(self.spec.currentData(), set())
        for i in range(self.method_list.count()):
            item = self.method_list.item(i)
            item.setCheckState(Qt.Checked if item.data(Qt.UserRole + 1) in codes else Qt.Unchecked)

    def _submit(self) -> None:
        chosen = [self.method_list.item(i).data(Qt.UserRole) for i in range(self.method_list.count())
                  if self.method_list.item(i).checkState() == Qt.Checked]
        spec_id = self.spec.currentData()
        reason = None
        if self.current_spec and spec_id != self.current_spec:
            reason = ask_reason(self, "Change specification", f"Changing the specification of {self.sample_id}.")
            if reason is None:
                return
        if not chosen and spec_id == self.current_spec:
            show_label_message(self.error, "Tick at least one test, or change the specification.")
            return
        try:
            with self.db.session() as session:
                testing.set_specification(session, self.user, self.sample_pk, spec_id, reason)
                if chosen:
                    testing.assign_tests(session, self.user, self.sample_pk, chosen, self.assignee.currentData())
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return
        self.accept()


class ResultEntryDialog(QDialog):
    """Enter replicate values; calculations and pass/fail update as you type."""

    SUMMARY_COLUMNS = ["Result", "Replicates", "Mean", "%RSD", "Reported", "Specification", "Conformance"]

    def __init__(self, db: Database, user: SessionUser, analysis_id: int, parent=None, review_mode: bool = False):
        super().__init__(parent)
        self.db, self.user, self.analysis_id = db, user, analysis_id
        self.review_mode = review_mode
        self.resize(980, 760)
        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(0, 0, 0, 0)
        self._container: QWidget | None = None
        self._load()

    # ----- data -----
    def _load(self) -> None:
        with self.db.session() as session:
            analysis = session.get(Analysis, self.analysis_id)
            method = session.get(TestMethod, analysis.method_id)
            sample = session.get(Sample, analysis.sample_id)
            spec = session.get(Specification, sample.specification_id) if sample.specification_id else None
            self.params = [to_spec(p) for p in methods.parameters_of(session, method.id)]
            self.limits = testing.limits_for(session, sample, method.code)
            self.stored = testing.stored_values(session, analysis.id)
            self.status = analysis.status
            self.frozen = testing.reportable_results(session, analysis.id)
            completed_by = session.get(User, analysis.completed_by_id).full_name if analysis.completed_by_id else None
            reviewer = session.get(User, analysis.reviewed_by_id).full_name if analysis.reviewed_by_id else None
            self.header = (f"{sample.sample_id} · {sample.material_name or sample.sampling_point or ''} · "
                           f"{method.code} v{method.version} – {method.name}")
            self.subheader = (f"Specification: {spec.code} v{spec.version}" if spec else "No specification") + \
                f" · Status: {analysis.status}" + \
                (f" (revision {analysis.revision})" if analysis.revision > 1 else "") + \
                (f" · Signed by {completed_by} on {format_local(analysis.completed_at)}" if completed_by else "") + \
                (f" · Reviewed by {reviewer} on {format_local(analysis.reviewed_at)}"
                 if reviewer and analysis.status == AnalysisStatus.REVIEWED else "") + \
                (f" · SOP {method.sop_reference}" if method.sop_reference else "")
            self.return_note = (f"Returned for correction by {reviewer}: {analysis.review_comment}"
                                if analysis.status == AnalysisStatus.RETURNED else "")
            self.sample_id = sample.sample_id
            self.method_label = f"{method.code} v{method.version}"
            self.method_id = method.id
            self.requirements = methods.requirements_of(session, method.id)
            self.usages = usage.usages_for(session, analysis.id)
            self.lot_filled = {key: r.label for key, r in usage.lot_filled_keys(session, method.id).items()}
        self.editable = self.status in OPEN_ANALYSIS and self.user.can(Permission.EDIT_RECORDS)
        self.setWindowTitle(f"Results – {self.sample_id} – {self.method_label}")
        self._build()

    def _build(self) -> None:
        # Everything lives in one container so the window can be rebuilt after recording equipment.
        if self._container is not None:
            self._outer.removeWidget(self._container)
            self._container.deleteLater()
        self._container = QWidget(self)
        self._outer.addWidget(self._container)
        layout = QVBoxLayout(self._container)
        title = QLabel(self.header)
        title.setObjectName("CardTitle")
        layout.addWidget(title)
        layout.addWidget(muted_label(self.subheader))
        if self.return_note:
            banner = QLabel(self.return_note)
            banner.setObjectName("Banner")
            banner.setWordWrap(True)
            layout.addWidget(banner)

        if self.requirements or self.usages or self.editable:
            equipment = Card("Equipment and materials used")
            self.usage_table = usage_table(self.requirements, self.usages)
            equipment.body.addWidget(self.usage_table)
            if self.editable:
                row = QHBoxLayout()
                for text, kind in (("Record instrument…", "instrument"), ("Record lot…", "material")):
                    button = QPushButton(text)
                    button.clicked.connect(lambda *_, k=kind: self._record_usage(k))
                    row.addWidget(button)
                remove = QPushButton("Remove selected…")
                remove.clicked.connect(lambda *_: self._remove_usage())
                row.addWidget(remove)
                row.addStretch()
                equipment.body.addLayout(row)
            layout.addWidget(equipment)

        self.fields: dict[tuple[str, int], QWidget] = {}
        self.calc_labels: dict[tuple[str, int], QLabel] = {}
        entry = Card("Entered and calculated values")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        max_reps = max((p.replicates for p in self.params), default=1)
        grid.addWidget(QLabel("<b>Parameter</b>"), 0, 0)
        for i in range(max_reps):
            grid.addWidget(QLabel(f"<b>Replicate {i + 1}</b>" if max_reps > 1 else "<b>Value</b>"), 0, i + 1)
        for row, p in enumerate(self.params, start=1):
            label = QLabel(f"{p.name}" + (f" ({p.unit})" if p.unit else "") +
                           ("" if p.kind == "result" else "  <span style='color:#8b98a8'>input</span>"))
            label.setToolTip(f"Key: {p.key}" + (f"\nFormula: {p.formula}" if p.formula else ""))
            grid.addWidget(label, row, 0)
            for i in range(1, p.replicates + 1):
                if p.formula:
                    widget = QLabel("")
                    widget.setStyleSheet("color:#4a5562; font-style: italic")
                    widget.setToolTip(p.formula)
                    self.calc_labels[(p.key, i)] = widget
                elif p.result_type == ResultType.PASS_FAIL:
                    widget = QComboBox()
                    widget.addItems(["", "Pass", "Fail"])
                    widget.setCurrentText(self.stored.get((p.key, i), ""))
                    widget.currentIndexChanged.connect(lambda *_: self._recalculate())
                    widget.setEnabled(self.editable)
                    self.fields[(p.key, i)] = widget
                elif p.key in self.lot_filled:
                    # Filled from the reference standard lot; never typed by hand.
                    widget = QLineEdit(self.stored.get((p.key, i), ""))
                    widget.setAlignment(Qt.AlignRight)
                    widget.setReadOnly(True)
                    widget.setPlaceholderText("record the lot")
                    widget.setStyleSheet("background:#f3ecd6")
                    widget.setToolTip(f"Filled automatically from the {self.lot_filled[p.key]} lot's certificate")
                else:
                    widget = QLineEdit(self.stored.get((p.key, i), ""))
                    if p.result_type == ResultType.NUMERIC:
                        widget.setAlignment(Qt.AlignRight)
                    widget.textChanged.connect(lambda *_: self._recalculate())
                    widget.setReadOnly(not self.editable)
                    self.fields[(p.key, i)] = widget
                grid.addWidget(widget, row, i)
        grid.setColumnStretch(0, 2)
        for i in range(max_reps):
            grid.setColumnStretch(i + 1, 1)
        entry.body.addLayout(grid)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setWidget(entry)
        layout.addWidget(scroll, 3)

        layout.addWidget(QLabel("<b>Reportable results</b>  <span style='color:#6b7785'>(mean of replicates, "
                                "rounded half-up to the method's decimals, then compared with the limit)</span>"))
        self.summary = GridTable(0, len(self.SUMMARY_COLUMNS))
        self.summary.setHorizontalHeaderLabels(self.SUMMARY_COLUMNS)
        self.summary.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.summary.verticalHeader().hide()
        header = self.summary.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.summary, 2)
        self.problems = muted_label("")
        layout.addWidget(self.problems)
        self.error = error_label()
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(lambda *_: self._close())
        if self.editable:
            save = QPushButton("Save")
            save.clicked.connect(lambda *_: self._save())
            self.complete_button = QPushButton("Save and sign as complete…")
            self.complete_button.setProperty("primary", True)
            self.complete_button.clicked.connect(lambda *_: self._complete())
            buttons.addWidget(save)
            buttons.addWidget(self.complete_button)
        if (self.review_mode and self.status == AnalysisStatus.COMPLETE
                and self.user.can(Permission.REVIEW_RECORDS)):
            send_back = QPushButton("Return for correction…")
            send_back.setProperty("danger", True)
            send_back.clicked.connect(lambda *_: self._return())
            accept = QPushButton("Accept and sign…")
            accept.setProperty("primary", True)
            accept.clicked.connect(lambda *_: self._accept_review())
            buttons.addWidget(send_back)
            buttons.addWidget(accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self._recalculate()

    def _current_values(self) -> dict[tuple[str, int], str]:
        out = {(key, 1): self.stored[(key, 1)] for key in self.lot_filled if (key, 1) in self.stored}
        for key, widget in self.fields.items():
            out[key] = widget.currentText() if isinstance(widget, QComboBox) else widget.text()
        return out

    def _dirty(self) -> dict[tuple[str, int], str]:
        current = self._current_values()
        return {k: v for k, v in current.items() if k[0] not in self.lot_filled and v.strip() != self.stored.get(k, "")}

    def _record_usage(self, kind: str) -> None:
        if self._dirty() and not self._save():
            return
        if RecordUsageDialog(self.db, self.user, self.analysis_id, self.method_id, kind, self).exec() == QDialog.Accepted:
            self._load()

    def _remove_usage(self) -> None:
        rows = self.usage_table.selectionModel().selectedRows()
        usage_id = self.usage_table.usage_ids[rows[0].row()] if rows else None
        if usage_id is None:
            show_label_message(self.error, "Select a recorded instrument or lot to remove.")
            return
        if self._dirty() and not self._save():
            return
        reason = ask_reason(self, "Remove", "Remove this instrument / lot from the test? Any stock taken is put back.")
        if reason is None:
            return
        try:
            with self.db.session() as session:
                usage.remove_usage(session, self.user, usage_id, reason)
        except AurumError as exc:
            show_error(self, exc)
            return
        self._load()

    def _recalculate(self) -> None:
        if not self.editable and self.frozen:
            self._show_frozen()
            return
        calc = testing.calculate(self.params, self._current_values(), self.limits)
        for p in calc.parameters:
            if p.calculated:
                for i, text in enumerate(p.replicate_values, start=1):
                    if (p.key, i) in self.calc_labels:
                        self.calc_labels[(p.key, i)].setText(text or "—")
        self._show_calculation(calc)

    def _show_calculation(self, calc: Calculation) -> None:
        results = calc.results
        self.summary.setRowCount(len(results))
        for r, p in enumerate(results):
            reps = ", ".join(v for v in p.replicate_values if v) if len(p.replicate_values) > 1 else ""
            values = [
                p.name + (f" ({p.unit})" if p.unit else ""), reps,
                formulas.format_decimal(p.mean, None if p.decimals is None else p.decimals + 2)
                if p.mean is not None and len(p.replicate_values) > 1 else "",
                formulas.format_decimal(p.rsd, 2) if p.rsd is not None else "",
                p.reported or "", p.spec_text or "", p.conformance.value if p.conformance else "",
            ]
            self._fill_row(r, values, p.conformance)
        problems = calc.problems()
        covered = {u.requirement_id for u in self.usages if u.requirement_id}
        problems += [f"record the {r.label} used" for r in self.requirements if r.id not in covered]
        problems += [p for u in self.usages for p in u.problems]
        self.problems.setText("Still needed: " + "; ".join(problems) if problems else
                              ("All results calculated." + (f"  {len(calc.failures)} result(s) OUT OF SPECIFICATION."
                                                            if calc.failures else "")))
        self.problems.setStyleSheet("color:#b42318" if calc.failures else "")
        if self.editable:
            self.complete_button.setEnabled(calc.complete and not problems)

    def _show_frozen(self) -> None:
        self.summary.setRowCount(len(self.frozen))
        for r, res in enumerate(self.frozen):
            reps = json.loads(res.replicate_values or "[]")
            values = [res.parameter_name + (f" ({res.unit})" if res.unit else ""),
                      ", ".join(reps) if len(reps) > 1 else "", res.mean if len(reps) > 1 else "", res.rsd or "",
                      res.reported_value, res.spec_text or "", res.conformance]
            self._fill_row(r, values, Conformance(res.conformance))
        comments = [f"{res.parameter_name}: {res.comment}" for res in self.frozen if res.comment]
        self.problems.setText("Final results (signed). " + ("Comments: " + "; ".join(comments) if comments else ""))
        stored_calc = testing.calculate(self.params, self.stored, {})
        for p in stored_calc.parameters:
            if p.calculated:
                for i, text in enumerate(p.replicate_values, start=1):
                    if (p.key, i) in self.calc_labels:
                        self.calc_labels[(p.key, i)].setText(text or "—")

    def _fill_row(self, row: int, values: list[str], conformance) -> None:
        for c, value in enumerate(values):
            item = QTableWidgetItem(value)
            if c == 6 and conformance:
                item.setForeground(QColor(CONFORMANCE_COLORS[conformance]))
                font = item.font()
                font.setBold(True)
                item.setFont(font)
            if c == 4 and conformance is Conformance.FAIL:
                item.setForeground(QColor("#b42318"))
            self.summary.setItem(row, c, item)

    # ----- actions -----
    def _save(self) -> bool:
        changes = self._dirty()
        if not changes:
            return True
        changed_existing = [k for k in changes if k in self.stored]
        reason = None
        if changed_existing:
            names = ", ".join(f"{k[0]} #{k[1]}" for k in changed_existing)
            reason = ask_reason(self, "Change saved values", f"You are changing values that were already saved ({names}).")
            if reason is None:
                return False
        try:
            with self.db.session() as session:
                testing.save_values(session, self.user, self.analysis_id, changes, reason)
                self.stored = testing.stored_values(session, self.analysis_id)
        except AurumError as exc:
            show_label_message(self.error, str(exc))
            return False
        show_label_message(self.error, "")
        return True

    def _complete(self) -> None:
        if not self._save():
            return
        calc = testing.calculate(self.params, self.stored, self.limits)
        if not calc.complete:
            show_label_message(self.error, "The results are not complete.")
            return
        extra = QWidget()
        extra_layout = QFormLayout(extra)
        extra_layout.setContentsMargins(0, 0, 0, 0)
        summary = "\n".join(f"{p.name}: {p.reported} {p.unit or ''}  {p.spec_text or ''}  "
                            f"{p.conformance.value if p.conformance else ''}" for p in calc.results)
        extra_layout.addRow(muted_label(summary))
        comment_fields = {}
        for p in calc.failures:
            field = QLineEdit()
            field.setPlaceholderText("Required: what happened / first observations")
            comment_fields[p.key] = field
            label = QLabel(f"<span style='color:#b42318'><b>OOS</b></span> {p.name} comment")
            extra_layout.addRow(label, field)
        if calc.failures:
            extra_layout.addRow(muted_label("Signing will open an OOS investigation for each result outside its limit."))

        def action(password):
            with self.db.session() as session:
                return testing.complete_analysis(session, self.user, self.analysis_id, password,
                                                 {k: f.text() for k, f in comment_fields.items()})

        statement = (f"By entering your password you electronically sign:\n“{SignatureMeaning.PERFORMED}” "
                     f"{self.user.full_name} — {self.method_label} on {self.sample_id}. Results will be locked.")
        if SignDialog(self, "Sign test as complete", statement, action, "Sign", extra).exec() == QDialog.Accepted:
            if calc.failures:
                QMessageBox.warning(self, "OOS opened",
                                    f"{len(calc.failures)} OOS investigation(s) opened. See the OOS page.")
            self.accept()

    def _return(self) -> None:
        comment, ok = QInputDialog.getMultiLineText(
            self, "Return for correction", f"What must the analyst correct on {self.method_label} ({self.sample_id})?")
        if not ok:
            return
        try:
            with self.db.session() as session:
                review.return_test(session, self.user, self.analysis_id, comment)
        except AurumError as exc:
            show_error(self, exc)
            return
        self.accept()

    def _accept_review(self) -> None:
        extra = QWidget()
        form = QFormLayout(extra)
        form.setContentsMargins(0, 0, 0, 0)
        comment = QLineEdit()
        comment.setPlaceholderText("Optional review comment")
        form.addRow("Comment", comment)

        def action(password):
            with self.db.session() as session:
                return review.accept_test(session, self.user, self.analysis_id, password, comment.text())

        statement = (f"By entering your password you electronically sign:\n“{SignatureMeaning.REVIEWED}” "
                     f"{self.user.full_name} — I have checked the raw values, calculations and results of "
                     f"{self.method_label} on {self.sample_id}.")
        if SignDialog(self, "Accept test review", statement, action, "Sign", extra).exec() == QDialog.Accepted:
            self.accept()

    def _close(self) -> None:
        if self.editable and self._dirty():
            answer = QMessageBox.question(self, "Unsaved values", "Save the values you entered before closing?",
                                          QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if answer == QMessageBox.Cancel or (answer == QMessageBox.Save and not self._save()):
                return
        self.accept()

    def reject(self) -> None:
        self._close()
