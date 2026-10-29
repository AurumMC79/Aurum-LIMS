"""SOP Index page: everyone can open the SOPs and forms in effect."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from PySide6.QtCore import QDate, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.db import Database
from aurum_lims.errors import AurumError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import documents
from aurum_lims.services.documents import MAX_PDF_BYTES, DocType
from aurum_lims.ui.common import (
    GridTable,
    ask_reason,
    error_label,
    format_local,
    muted_label,
    open_pdf,
    primary_button,
    show_error,
    show_label_message,
)
from aurum_lims.ui.views.instruments import date_edit


class PdfPicker(QWidget):
    """'Browse…' button plus the chosen file name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.path: Path | None = None
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("No file chosen")
        self.label.setObjectName("Muted")
        browse = QPushButton("Choose PDF…")
        browse.clicked.connect(lambda *_: self._browse())
        row.addWidget(browse)
        row.addWidget(self.label, 1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose PDF", "", "PDF files (*.pdf)")
        if path:
            self.path = Path(path)
            size = self.path.stat().st_size / (1024 * 1024)
            self.label.setText(f"{self.path.name}  ({size:.1f} MB)")

    def data(self) -> bytes:
        if self.path is None:
            raise AurumError("Choose the PDF file to upload.")
        if self.path.stat().st_size > MAX_PDF_BYTES:
            raise AurumError(f"The file is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
        return self.path.read_bytes()


class AddDocumentDialog(QDialog):
    def __init__(self, categories: list[str], parents: list[tuple[int, str]], parent=None,
                 attach_to: int | None = None):
        super().__init__(parent)
        self.setWindowTitle("Add document to the SOP index")
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.number = QLineEdit()
        self.number.setPlaceholderText("e.g. SOP-QC-101 or FORM-QC-101-01")
        self.title = QLineEdit()
        self.doc_type = QComboBox()
        for t in DocType:
            self.doc_type.addItem(t.value)
        self.category = QComboBox()
        self.category.setEditable(True)
        self.category.addItems(categories)
        self.category.setCurrentText("Quality Control")
        self.parent_doc = QComboBox()
        self.parent_doc.addItem("(none – top level)", None)
        for doc_id, label in parents:
            self.parent_doc.addItem(label, doc_id)
        if attach_to is not None:
            self.parent_doc.setCurrentIndex(max(self.parent_doc.findData(attach_to), 0))
            self.doc_type.setCurrentText(DocType.FORM.value)
        self.revision = QLineEdit("01")
        self.effective = date_edit()
        self.file = PdfPicker()
        form.addRow("Document number *", self.number)
        form.addRow("Title *", self.title)
        form.addRow("Type", self.doc_type)
        form.addRow("Category", self.category)
        form.addRow("Attach to", self.parent_doc)
        form.addRow("Revision *", self.revision)
        form.addRow("Effective date", self.effective)
        form.addRow("PDF file *", self.file)
        layout.addLayout(form)
        layout.addWidget(muted_label("Forms and attachments can be attached to their SOP so they are listed under it."))
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Upload")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {
            "number": self.number.text(), "title": self.title.text(), "doc_type": self.doc_type.currentText(),
            "category": self.category.currentText(), "parent_id": self.parent_doc.currentData(),
            "revision": self.revision.text(), "effective_date": self.effective.date().toPython(),
            "file_name": self.file.path.name if self.file.path else "", "data": self.file.data(),
            "change_summary": "Initial upload",
        }


class RevisionDialog(QDialog):
    def __init__(self, number: str, current: str | None, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"New revision of {number}")
        self.setMinimumWidth(500)
        layout = QVBoxLayout(self)
        layout.addWidget(muted_label(f"The current revision is {current or '—'}. The new revision becomes the one in "
                                     "effect; the current one is kept on record as superseded."))
        form = QFormLayout()
        self.revision = QLineEdit()
        self.effective = date_edit()
        self.summary = QPlainTextEdit()
        self.summary.setFixedHeight(60)
        self.summary.setPlaceholderText("What changed in this revision (required)")
        self.file = PdfPicker()
        form.addRow("New revision *", self.revision)
        form.addRow("Effective date", self.effective)
        form.addRow("What changed *", self.summary)
        form.addRow("PDF file *", self.file)
        layout.addLayout(form)
        self.error = error_label()
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Upload revision")
        buttons.button(QDialogButtonBox.Ok).setProperty("primary", True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {"revision": self.revision.text(), "effective_date": self.effective.date().toPython(),
                "file_name": self.file.path.name if self.file.path else "", "data": self.file.data(),
                "change_summary": self.summary.toPlainText()}


class HistoryDialog(QDialog):
    def __init__(self, db: Database, user: SessionUser, document_id: int, number: str, parent=None):
        super().__init__(parent)
        self.db, self.user = db, user
        self.setWindowTitle(f"Revision history – {number}")
        self.resize(820, 360)
        with db.session() as session:
            self.rows = documents.revisions(session, user, document_id)
        layout = QVBoxLayout(self)
        self.table = GridTable(len(self.rows), 6)
        self.table.setHorizontalHeaderLabels(["Revision", "Effective", "Status", "What changed", "Uploaded",
                                              "File"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for r, rev in enumerate(self.rows):
            values = [rev.revision, rev.effective_date.isoformat(), "Current" if rev.current else "Superseded",
                      rev.change_summary or "", f"{format_local(rev.uploaded_at)} by {rev.uploaded_by}",
                      rev.file_name]
            for c, value in enumerate(values):
                item = QTableWidgetItem(value)
                if c == 2:
                    item.setForeground(QColor("#067647" if rev.current else "#8b98a8"))
                self.table.setItem(r, c, item)
        layout.addWidget(self.table)
        if not user.can(Permission.MANAGE_DOCUMENTS):
            layout.addWidget(muted_label("Only the current revision can be opened. Superseded revisions are kept "
                                         "on record for QA."))
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        open_button = buttons.addButton("Open selected", QDialogButtonBox.ActionRole)
        open_button.clicked.connect(lambda *_: self._open())
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _open(self) -> None:
        idx = self.table.selectionModel().selectedRows()
        if idx:
            open_document(self, self.db, self.user, self.rows[idx[0].row()].id)


def open_document(parent: QWidget, db: Database, user: SessionUser, revision_id: int) -> None:
    try:
        with db.session() as session:
            label, pdf = documents.open_revision(session, user, revision_id)
    except AurumError as exc:
        show_error(parent, exc, "Cannot open")
        return
    open_pdf(label, pdf)


class DocumentsView(QWidget):
    title = "SOP Index"
    COLUMNS = ["Number", "Title", "Type", "Category", "Revision", "Effective"]

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        self.rows: dict[int, documents.DocumentRow] = {}
        can_manage = user.can(Permission.MANAGE_DOCUMENTS)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        layout.addWidget(muted_label(
            "The current, approved revision of each SOP, form and controlled document. Double-click to open it "
            "(read-only). Forms are listed under their SOP."
        ))
        toolbar = QHBoxLayout()
        self.open_button = primary_button("Open")
        self.open_button.setDefault(False)
        self.history_button = QPushButton("Revision history")
        self.add_button = QPushButton("Add document…")
        self.attach_button = QPushButton("Attach form to this SOP…")
        self.revision_button = QPushButton("Upload new revision…")
        self.edit_button = QPushButton("Edit details…")
        self.retire_button = QPushButton("Retire…")
        for b in (self.add_button, self.attach_button, self.revision_button, self.edit_button, self.retire_button):
            b.setVisible(can_manage)
        for b in (self.open_button, self.history_button, self.add_button, self.attach_button, self.revision_button,
                  self.edit_button, self.retire_button):
            toolbar.addWidget(b)
        toolbar.addStretch()
        layout.addLayout(toolbar)
        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search number, title or category…")
        self.search.textChanged.connect(lambda *_: self.refresh())
        self.show_retired = QCheckBox("Show retired")
        self.show_retired.setVisible(can_manage)
        self.show_retired.toggled.connect(lambda *_: self.refresh())
        self.count = muted_label("")
        filters.addWidget(self.search, 1)
        filters.addWidget(self.show_retired)
        filters.addWidget(self.count)
        layout.addLayout(filters)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(self.COLUMNS)
        self.tree.setAlternatingRowColors(True)
        self.tree.header().setStretchLastSection(False)
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.itemSelectionChanged.connect(self._update_buttons)
        self.tree.itemDoubleClicked.connect(lambda *_: self._open())
        layout.addWidget(self.tree, 1)

        self.open_button.clicked.connect(lambda *_: self._open())
        self.history_button.clicked.connect(lambda *_: self._history())
        self.add_button.clicked.connect(lambda *_: self._add(None))
        self.attach_button.clicked.connect(lambda *_: self._add(self._selected().id if self._selected() else None))
        self.revision_button.clicked.connect(lambda *_: self._revise())
        self.edit_button.clicked.connect(lambda *_: self._edit())
        self.retire_button.clicked.connect(lambda *_: self._toggle_active())
        self._update_buttons()

    def refresh(self) -> None:
        selected = self._selected()
        with self.db.session() as session:
            rows = documents.list_documents(session, self.user, include_retired=self.show_retired.isChecked(),
                                            search=self.search.text())
            self.categories = documents.categories(session)
        self.rows = {r.id: r for r in rows}
        self.tree.clear()
        items: dict[int, QTreeWidgetItem] = {}
        for row in sorted(rows, key=lambda r: (r.parent_id is not None, r.number)):
            item = QTreeWidgetItem([row.number, row.title + ("" if row.is_active else "  (retired)"), row.doc_type,
                                    row.category or "", row.revision or "", row.effective_date.isoformat()
                                    if row.effective_date else ""])
            item.setData(0, Qt.UserRole, row.id)
            if not row.is_active:
                for c in range(len(self.COLUMNS)):
                    item.setForeground(c, QColor("#8b98a8"))
            parent = items.get(row.parent_id)
            (parent.addChild(item) if parent else self.tree.addTopLevelItem(item))
            items[row.id] = item
        self.tree.expandAll()
        if selected and selected.id in items:
            self.tree.setCurrentItem(items[selected.id])
        top = sum(1 for r in rows if r.parent_id is None)
        self.count.setText(f"{top} document(s), {len(rows) - top} attached form(s)")
        self._update_buttons()

    def _selected(self) -> documents.DocumentRow | None:
        item = self.tree.currentItem()
        if item is None or not item.isSelected():
            return None
        return self.rows.get(item.data(0, Qt.UserRole))

    def _update_buttons(self) -> None:
        row = self._selected()
        self.open_button.setEnabled(row is not None and row.revision_id is not None and row.is_active)
        self.history_button.setEnabled(row is not None)
        self.attach_button.setEnabled(row is not None and row.parent_id is None and row.is_active)
        self.revision_button.setEnabled(row is not None and row.is_active)
        self.edit_button.setEnabled(row is not None)
        self.retire_button.setEnabled(row is not None)
        self.retire_button.setText("Reactivate…" if row is not None and not row.is_active else "Retire…")

    def _run(self, action) -> bool:
        try:
            with self.db.session() as session:
                action(session)
        except AurumError as exc:
            show_error(self, exc)
            return False
        self.refresh()
        return True

    def _open(self) -> None:
        row = self._selected()
        if row is not None and row.revision_id is not None:
            open_document(self, self.db, self.user, row.revision_id)

    def _history(self) -> None:
        row = self._selected()
        if row is not None:
            HistoryDialog(self.db, self.user, row.id, row.number, self).exec()

    def _add(self, attach_to: int | None) -> None:
        parents = [(r.id, f"{r.number} – {r.title}") for r in self.rows.values() if r.parent_id is None and r.is_active]
        dialog = AddDocumentDialog(self.categories, sorted(parents, key=lambda p: p[1]), self, attach_to)
        while dialog.exec() == QDialog.Accepted:
            try:
                values = dialog.values()
            except AurumError as exc:
                show_label_message(dialog.error, str(exc))
                continue
            if self._run(lambda s: documents.create_document(s, self.user, **values)):
                return

    def _revise(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = RevisionDialog(row.number, row.revision, self)
        while dialog.exec() == QDialog.Accepted:
            try:
                values = dialog.values()
            except AurumError as exc:
                show_label_message(dialog.error, str(exc))
                continue
            if self._run(lambda s: documents.upload_revision(s, self.user, row.id, **values)):
                return

    def _edit(self) -> None:
        row = self._selected()
        if row is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle(f"Edit {row.number}")
        dialog.setMinimumWidth(460)
        form = QFormLayout(dialog)
        title = QLineEdit(row.title)
        doc_type = QComboBox()
        for t in DocType:
            doc_type.addItem(t.value)
        doc_type.setCurrentText(row.doc_type)
        category = QComboBox()
        category.setEditable(True)
        category.addItems(self.categories)
        category.setCurrentText(row.category or "")
        form.addRow("Title", title)
        form.addRow("Type", doc_type)
        form.addRow("Category", category)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        # If saving fails (or the reason is cancelled) the window comes back with what was typed.
        while dialog.exec() == QDialog.Accepted:
            reason = ask_reason(self, "Edit document", f"Changing the details of {row.number}.")
            if reason is not None and self._run(lambda s: documents.update_document(
                    s, self.user, row.id, title=title.text(), doc_type=doc_type.currentText(),
                    category=category.currentText(), reason=reason)):
                return

    def _toggle_active(self) -> None:
        row = self._selected()
        if row is None:
            return
        verb = "Reactivate" if not row.is_active else "Retire"
        reason = ask_reason(self, f"{verb} document", f"{verb} {row.number} – {row.title}?")
        if reason is not None:
            self._run(lambda s: documents.set_active(s, self.user, row.id, not row.is_active, reason))
