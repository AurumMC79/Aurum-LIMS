"""Review page: tests awaiting second-person review and samples awaiting QA's decision."""

from __future__ import annotations

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
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
from aurum_lims.security.roles import Permission
from aurum_lims.services import certificates, lab_info, review
from aurum_lims.services.esign import SignatureMeaning
from aurum_lims.services.formulas import Conformance
from aurum_lims.ui.common import GridTable, error_label, format_local, muted_label, open_pdf, primary_button, show_error
from aurum_lims.ui.sign_dialog import SignDialog
from aurum_lims.ui.testing_dialogs import CONFORMANCE_COLORS, ResultEntryDialog


def _table(columns: list[str], stretch: int) -> QTableWidget:
    table = GridTable(0, len(columns))
    table.setHorizontalHeaderLabels(columns)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.SingleSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setAlternatingRowColors(True)
    table.verticalHeader().hide()
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
    table.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
    return table


class DecisionDialog(QDialog):
    """QA's final decision on a reviewed sample, with its certificate."""

    COLUMNS = ["Test", "Parameter", "Result", "Specification", "Conformance", "Performed by", "Reviewed by"]

    def __init__(self, db: Database, user: SessionUser, sample_pk: int, parent=None):
        super().__init__(parent)
        self.db, self.user, self.sample_pk = db, user, sample_pk
        self.certificate_id = None
        self.certificate_number = None
        with db.session() as session:
            self.summary = review.decision_summary(session, user, sample_pk)
            lab_name = lab_info.get_lab_info(session).lab_name
        s = self.summary
        self.setWindowTitle(f"Decision – {s.sample_id}")
        self.resize(960, 600)
        layout = QVBoxLayout(self)
        title = QLabel(f"{s.sample_id} · {s.status}")
        title.setObjectName("PageTitle")
        layout.addWidget(title)

        table = _table(self.COLUMNS, 1)
        table.setRowCount(len(s.results))
        for r, line in enumerate(s.results):
            values = [line.test, line.parameter, f"{line.reported} {line.unit or ''}".strip(), line.spec_text or "",
                      line.conformance, line.performed_by or "", line.reviewed_by or ""]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 4:
                    item.setForeground(QColor(CONFORMANCE_COLORS.get(Conformance(line.conformance), "#1f2933")))
                table.setItem(r, c, item)
        layout.addWidget(table, 1)

        notes = []
        if s.open_oos:
            notes.append(f"<span style='color:#b42318'>Open OOS investigation(s): {', '.join(s.open_oos)}. "
                         "Close them on the OOS page before deciding.</span>")
        if s.unreviewed:
            notes.append(f"<span style='color:#b42318'>Not yet reviewed: {', '.join(s.unreviewed)}.</span>")
        if s.results:
            notes.append("<span style='color:#067647'><b>All results conform.</b></span>" if s.conforms else
                         "<span style='color:#b42318'><b>Some results do not conform</b> – the sample can only be "
                         "rejected.</span>")
        if not lab_name:
            notes.append("<span style='color:#8a6d15'>The laboratory name is not set (Settings → Laboratory "
                         "details); certificates will show 'Laboratory name not set'.</span>")
        for note in notes:
            label = QLabel(note)
            label.setWordWrap(True)
            layout.addWidget(label)

        layout.addWidget(QLabel("Comment (required to reject; printed on the certificate)"))
        self.comment = QPlainTextEdit()
        self.comment.setFixedHeight(60)
        layout.addWidget(self.comment)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QHBoxLayout()
        buttons.addStretch()
        reject = QPushButton("Reject and sign…")
        reject.setProperty("danger", True)
        approve = primary_button("Approve and sign…")
        approve.setDefault(False)
        cancel = QPushButton("Cancel")
        reject.clicked.connect(lambda *_: self._decide(False))
        approve.clicked.connect(lambda *_: self._decide(True))
        cancel.clicked.connect(self.reject)
        reject.setEnabled(s.can_decide)
        approve.setEnabled(s.can_decide and s.conforms)
        for b in (reject, approve, cancel):
            buttons.addWidget(b)
        layout.addLayout(buttons)

    def _decide(self, approve: bool) -> None:
        comment = self.comment.toPlainText()
        kind = certificates.COA if approve else certificates.TEST_REPORT

        def action(password):
            with self.db.session() as session:
                result = review.decide_sample(session, self.user, self.sample_pk, approve=approve,
                                              password=password, comment=comment)
            if result.ok:
                self.certificate_id, self.certificate_number = result.certificate_id, result.certificate_number
            return result.signature

        meaning = SignatureMeaning.APPROVED if approve else SignatureMeaning.REJECTED
        statement = (f"By entering your password you electronically sign:\n“{meaning}” {self.user.full_name} — "
                     f"sample {self.summary.sample_id} is {'approved' if approve else 'rejected'}. "
                     f"A {kind} will be issued and the sample locked.")
        title = "Approve sample" if approve else "Reject sample"
        if SignDialog(self, title, statement, action, "Sign").exec() == QDialog.Accepted:
            answer = QMessageBox.question(self, f"{kind} issued",
                                          f"{self.certificate_number} has been issued and stored.\n\nOpen it now?")
            if answer == QMessageBox.Yes:
                open_certificate(self, self.db, self.user, self.certificate_id)
            self.accept()


def open_certificate(parent: QWidget, db: Database, user: SessionUser, certificate_id: int) -> None:
    try:
        with db.session() as session:
            number, pdf = certificates.get_pdf(session, user, certificate_id)
    except AurumError as exc:
        show_error(parent, exc, "Certificate problem")
        return
    open_pdf(number, pdf)


class ReviewView(QWidget):
    title = "Review"
    TEST_COLUMNS = ["Sample ID", "Material / point", "Test", "Performed by", "Signed", "Priority"]
    SAMPLE_COLUMNS = ["Sample ID", "Material / point", "Lot / batch", "Priority", "Due", "Ready since"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.tests = []
        self.samples = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(QLabel("<b>1. Tests awaiting second-person review</b>"))
        layout.addWidget(muted_label("Check the raw values and calculations, then accept (signed) or return the "
                                     "test to the analyst. You cannot review a test you worked on."))
        row = QHBoxLayout()
        # Managers see the queues but cannot act on them.
        self.review_button = primary_button("Review test…" if user.can(Permission.REVIEW_RECORDS) else "View test…")
        self.review_button.setDefault(False)
        self.test_count = muted_label("")
        row.addWidget(self.review_button)
        row.addStretch()
        row.addWidget(self.test_count)
        layout.addLayout(row)
        self.test_table = _table(self.TEST_COLUMNS, 2)
        layout.addWidget(self.test_table, 1)

        layout.addSpacing(8)
        layout.addWidget(QLabel("<b>2. Samples awaiting decision</b>"))
        layout.addWidget(muted_label("All tests are reviewed. Approving issues a Certificate of Analysis; "
                                     "rejecting issues a Test Report."))
        row = QHBoxLayout()
        self.decide_button = primary_button("Decide…")
        self.decide_button.setDefault(False)
        self.decide_button.setVisible(user.can(Permission.APPROVE_RECORDS))
        self.sample_count = muted_label("")
        row.addWidget(self.decide_button)
        row.addStretch()
        row.addWidget(self.sample_count)
        layout.addLayout(row)
        self.sample_table = _table(self.SAMPLE_COLUMNS, 1)
        layout.addWidget(self.sample_table, 1)

        self.review_button.clicked.connect(lambda *_: self._review())
        self.decide_button.clicked.connect(lambda *_: self._decide())
        self.test_table.doubleClicked.connect(lambda *_: self._review())
        self.sample_table.doubleClicked.connect(lambda *_: self._decide())
        self.test_table.itemSelectionChanged.connect(self._update_buttons)
        self.sample_table.itemSelectionChanged.connect(self._update_buttons)
        self._update_buttons()

    def refresh(self) -> None:
        with self.db.session() as session:
            self.tests = review.tests_awaiting_review(session, self.user)
            self.samples = review.samples_awaiting_decision(session, self.user)
        self.test_table.setRowCount(len(self.tests))
        for r, t in enumerate(self.tests):
            for c, value in enumerate([t.sample_id, t.material or "", f"{t.method_code} v{t.method_version} – "
                                       f"{t.method_name}", t.completed_by or "", format_local(t.completed_at),
                                       t.priority]):
                self.test_table.setItem(r, c, QTableWidgetItem(value))
        self.sample_table.setRowCount(len(self.samples))
        for r, s in enumerate(self.samples):
            for c, value in enumerate([s.sample_id, s.material or "", s.lot_number or "", s.priority,
                                       s.due_date.isoformat() if s.due_date else "", format_local(s.updated_at)]):
                self.sample_table.setItem(r, c, QTableWidgetItem(value))
        self.test_count.setText(f"{len(self.tests)} test(s)")
        self.sample_count.setText(f"{len(self.samples)} sample(s)")
        self._update_buttons()

    @staticmethod
    def _row(table: QTableWidget, rows: list):
        model = table.selectionModel()
        idx = model.selectedRows() if model else []
        return rows[idx[0].row()] if idx and idx[0].row() < len(rows) else None

    def _update_buttons(self) -> None:
        self.review_button.setEnabled(self._row(self.test_table, self.tests) is not None)
        self.decide_button.setEnabled(self._row(self.sample_table, self.samples) is not None)

    def _review(self) -> None:
        test = self._row(self.test_table, self.tests)
        if test is not None:
            ResultEntryDialog(self.db, self.user, test.id, self, review_mode=True).exec()
            self.refresh()

    def _decide(self) -> None:
        sample = self._row(self.sample_table, self.samples)
        if sample is not None and self.user.can(Permission.APPROVE_RECORDS):
            DecisionDialog(self.db, self.user, sample.id, self).exec()
            self.refresh()
