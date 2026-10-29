"""SOP index: SOPs, forms and other controlled documents, stored as PDFs.

Everyone can open the current revision of an active document. Administrators
and QA Reviewers add documents and upload new revisions; uploading a revision
makes it the one in effect, and earlier revisions are kept (immutable) and
remain viewable to them as superseded copies. Every PDF is stored exactly as
uploaded with a SHA-256 fingerprint that is re-checked whenever it is opened.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import ControlledDocument, DocumentRevision, User
from aurum_lims.errors import AurumError, NotFound, PermissionDenied, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction

MAX_PDF_BYTES = 50 * 1024 * 1024
NUMBER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9\-_./]{0,39}$")
REVISION_PATTERN = re.compile(r"^[A-Za-z0-9.\-]{1,16}$")
DEFAULT_CATEGORIES = ["Quality Control", "Quality Assurance", "Production", "Microbiology", "Stability",
                      "Equipment", "Safety", "Document control", "General"]


class DocType(StrEnum):
    SOP = "SOP"
    FORM = "Form"
    WORK_INSTRUCTION = "Work instruction"
    POLICY = "Policy"
    SPECIFICATION = "Specification"
    OTHER = "Other"


class DocumentIntegrityError(AurumError):
    pass


@dataclass
class DocumentRow:
    id: int
    number: str
    title: str
    doc_type: str
    category: str | None
    parent_id: int | None
    is_active: bool
    revision: str | None
    effective_date: date | None
    revision_id: int | None
    revision_count: int


@dataclass
class RevisionRow:
    id: int
    revision: str
    effective_date: date
    change_summary: str | None
    file_name: str
    size_bytes: int
    uploaded_at: datetime
    uploaded_by: str
    current: bool


def _clean(value: str | None) -> str | None:
    return " ".join(value.split()) if value and value.strip() else None


def _check_pdf(data: bytes) -> None:
    if not data:
        raise ValidationError("The file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise ValidationError(f"The file is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not data.lstrip()[:5] == b"%PDF-":
        raise ValidationError("Only PDF files can be uploaded.")


def _check_revision_fields(revision: str, effective_date: date) -> str:
    revision = (revision or "").strip()
    if not REVISION_PATTERN.match(revision):
        raise ValidationError("Revision must be 1-16 letters, numbers, dots or dashes (e.g. 03 or B).")
    if effective_date is None:
        raise ValidationError("Enter the effective date.")
    return revision


def get_document(session: Session, document_id: int) -> ControlledDocument:
    document = session.get(ControlledDocument, document_id)
    if document is None:
        raise NotFound("Document not found.")
    return document


def list_documents(session: Session, actor: SessionUser, *, include_retired: bool = False,
                   search: str | None = None) -> list[DocumentRow]:
    require(actor, Permission.VIEW_RECORDS)
    counts = dict(session.execute(select(DocumentRevision.document_id, func.count())
                                  .group_by(DocumentRevision.document_id)).all())
    stmt = (select(ControlledDocument, DocumentRevision)
            .outerjoin(DocumentRevision, ControlledDocument.current_revision_id == DocumentRevision.id)
            .order_by(ControlledDocument.number))
    if not include_retired:
        stmt = stmt.where(ControlledDocument.is_active.is_(True))
    rows = []
    for d, r in session.execute(stmt):
        rows.append(DocumentRow(d.id, d.number, d.title, d.doc_type, d.category, d.parent_id, d.is_active,
                                r.revision if r else None, r.effective_date if r else None, r.id if r else None,
                                counts.get(d.id, 0)))
    if search:
        needle = search.strip().lower()
        matched = {r.id for r in rows if needle in f"{r.number} {r.title} {r.category or ''}".lower()}
        # Keep the parent SOP of a matching form, and the forms of a matching SOP, so the tree stays readable.
        matched |= {r.parent_id for r in rows if r.id in matched and r.parent_id}
        matched |= {r.id for r in rows if r.parent_id in matched}
        rows = [r for r in rows if r.id in matched]
    return rows


def categories(session: Session) -> list[str]:
    used = {c for c in session.scalars(select(ControlledDocument.category)) if c}
    return sorted(used | set(DEFAULT_CATEGORIES))


def create_document(
    session: Session,
    actor: SessionUser,
    *,
    number: str,
    title: str,
    doc_type: DocType | str,
    category: str | None,
    revision: str,
    effective_date: date,
    file_name: str,
    data: bytes,
    parent_id: int | None = None,
    change_summary: str | None = None,
) -> ControlledDocument:
    """Add a document to the index with its first PDF revision."""
    require(actor, Permission.MANAGE_DOCUMENTS)
    number = (number or "").strip().upper()
    if not NUMBER_PATTERN.match(number):
        raise ValidationError("Document number must be 1-40 capital letters, numbers, - _ . or / (e.g. SOP-QC-101).")
    if session.scalar(select(ControlledDocument.id).where(ControlledDocument.number == number)) is not None:
        raise ValidationError(f"{number} is already in the index. Upload a new revision of it instead.")
    title = _clean(title)
    if not title:
        raise ValidationError("Title is required.")
    doc_type = DocType(doc_type)
    if parent_id is not None:
        parent = get_document(session, parent_id)
        if parent.parent_id is not None:
            raise ValidationError("Documents can only be attached to a top-level document (e.g. a form to an SOP).")
    revision = _check_revision_fields(revision, effective_date)
    _check_pdf(data)
    document = ControlledDocument(number=number, title=title, doc_type=doc_type.value, category=_clean(category),
                                  parent_id=parent_id, is_active=True, created_by_id=actor.id)
    session.add(document)
    session.flush()
    rev = _add_revision(session, actor, document, revision, effective_date, file_name, data, change_summary)
    audit.record(session, AuditAction.DOCUMENT_CREATED, actor=actor, entity_type="document", entity_id=number,
                 new={"title": title, "type": doc_type.value, "category": document.category,
                      "attached_to": session.get(ControlledDocument, parent_id).number if parent_id else None,
                      "revision": revision, "effective_date": effective_date, "file": rev.file_name,
                      "sha256": rev.sha256})
    return document


def _add_revision(session, actor, document, revision, effective_date, file_name, data, change_summary):
    rev = DocumentRevision(
        document_id=document.id, revision=revision, effective_date=effective_date,
        change_summary=(change_summary or "").strip() or None, file_name=Path(file_name or "document.pdf").name[:255],
        pdf=data, size_bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), uploaded_by_id=actor.id,
    )
    session.add(rev)
    session.flush()
    document.current_revision_id = rev.id
    session.flush()
    return rev


def upload_revision(session: Session, actor: SessionUser, document_id: int, *, revision: str, effective_date: date,
                    file_name: str, data: bytes, change_summary: str) -> DocumentRevision:
    """Upload a new revision; it becomes the one in effect and the previous one is superseded."""
    require(actor, Permission.MANAGE_DOCUMENTS)
    document = get_document(session, document_id)
    if not document.is_active:
        raise ValidationError(f"{document.number} is retired. Reactivate it first.")
    revision = _check_revision_fields(revision, effective_date)
    if not (change_summary or "").strip():
        raise ValidationError("Describe what changed in this revision.")
    if session.scalar(select(DocumentRevision.id).where(DocumentRevision.document_id == document.id,
                                                        DocumentRevision.revision == revision)) is not None:
        raise ValidationError(f"{document.number} already has a revision {revision}.")
    _check_pdf(data)
    previous = session.get(DocumentRevision, document.current_revision_id) if document.current_revision_id else None
    rev = _add_revision(session, actor, document, revision, effective_date, file_name, data, change_summary)
    audit.record(session, AuditAction.DOCUMENT_REVISED, actor=actor, entity_type="document", entity_id=document.number,
                 old={"revision": previous.revision} if previous else None,
                 new={"revision": revision, "effective_date": effective_date, "file": rev.file_name,
                      "sha256": rev.sha256}, reason=change_summary.strip())
    return rev


def update_document(session: Session, actor: SessionUser, document_id: int, *, title: str, doc_type: str,
                    category: str | None, reason: str) -> ControlledDocument:
    require(actor, Permission.MANAGE_DOCUMENTS)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    document = get_document(session, document_id)
    new = {"title": _clean(title), "doc_type": DocType(doc_type).value, "category": _clean(category)}
    if not new["title"]:
        raise ValidationError("Title is required.")
    old = {k: getattr(document, k) for k in new}
    changed = {k: v for k, v in old.items() if new[k] != v}
    if not changed:
        raise ValidationError("Nothing was changed.")
    for key, value in new.items():
        setattr(document, key, value)
    audit.record(session, AuditAction.DOCUMENT_UPDATED, actor=actor, entity_type="document",
                 entity_id=document.number, old=changed, new={k: new[k] for k in changed}, reason=reason.strip())
    return document


def set_active(session: Session, actor: SessionUser, document_id: int, active: bool, reason: str) -> None:
    require(actor, Permission.MANAGE_DOCUMENTS)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    document = get_document(session, document_id)
    if document.is_active == active:
        raise ValidationError(f"{document.number} is already {'active' if active else 'retired'}.")
    document.is_active = active
    audit.record(session, AuditAction.DOCUMENT_UPDATED, actor=actor, entity_type="document",
                 entity_id=document.number, old={"is_active": not active}, new={"is_active": active},
                 reason=reason.strip())


def revisions(session: Session, actor: SessionUser, document_id: int) -> list[RevisionRow]:
    require(actor, Permission.VIEW_RECORDS)
    document = get_document(session, document_id)
    rows = session.execute(select(DocumentRevision, User.full_name)
                           .join(User, DocumentRevision.uploaded_by_id == User.id)
                           .where(DocumentRevision.document_id == document_id)
                           .order_by(DocumentRevision.id.desc()))
    return [RevisionRow(r.id, r.revision, r.effective_date, r.change_summary, r.file_name, r.size_bytes,
                        r.uploaded_at, who, r.id == document.current_revision_id) for r, who in rows]


def open_revision(session: Session, actor: SessionUser, revision_id: int) -> tuple[str, bytes]:
    """The PDF of a revision, after checking its fingerprint. Everyone may open the
    current revision of an active document; superseded or retired copies are
    restricted to document managers so obsolete SOPs are not used by mistake."""
    require(actor, Permission.VIEW_RECORDS)
    rev = session.get(DocumentRevision, revision_id)
    if rev is None:
        raise NotFound("Revision not found.")
    document = session.get(ControlledDocument, rev.document_id)
    in_effect = document.is_active and document.current_revision_id == rev.id
    if not in_effect and not actor.can(Permission.MANAGE_DOCUMENTS):
        raise PermissionDenied("Only the current revision of an active document can be opened.")
    if hashlib.sha256(rev.pdf).hexdigest() != rev.sha256:
        raise DocumentIntegrityError(f"{document.number} rev {rev.revision} does not match its recorded fingerprint "
                                     "and may have been altered outside Aurum LIMS.")
    label = f"{document.number} rev {rev.revision}" + ("" if in_effect else " (SUPERSEDED - for reference only)")
    return label, rev.pdf
