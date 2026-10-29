"""SOP index and bug reports."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import Role
from aurum_lims.services import bug_reports, documents
from aurum_lims.services.bug_reports import BugStatus

PDF_V1 = b"%PDF-1.7\n% SOP revision 01\n%%EOF"
PDF_V2 = b"%PDF-1.7\n% SOP revision 02\n%%EOF"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _sop(db, admin, number="SOP-QC-101", parent_id=None, doc_type="SOP"):
    with db.session() as s:
        return documents.create_document(
            s, admin, number=number, title="Operation of HPLC systems", doc_type=doc_type,
            category="Quality Control", revision="01", effective_date=date(2026, 1, 15), file_name="sop.pdf",
            data=PDF_V1, parent_id=parent_id).id


def test_create_and_view(db, admin, make_user):
    viewer = make_user("viewer1", Role.VIEWER)
    sop = _sop(db, admin)
    form = _sop(db, admin, "FORM-QC-101-01", parent_id=sop, doc_type="Form")
    with db.session() as s:
        rows = documents.list_documents(s, viewer)
        assert [(r.number, r.parent_id) for r in rows] == [("FORM-QC-101-01", sop), ("SOP-QC-101", None)]
        label, pdf = documents.open_revision(s, viewer, rows[1].revision_id)
        assert pdf == PDF_V1 and label == "SOP-QC-101 rev 01"
        assert [r.number for r in documents.list_documents(s, viewer, search="FORM-QC")] == \
            ["FORM-QC-101-01", "SOP-QC-101"]  # the parent SOP stays visible
        with pytest.raises(PermissionDenied):
            documents.create_document(s, viewer, number="SOP-X", title="x", doc_type="SOP", category=None,
                                      revision="01", effective_date=date.today(), file_name="x.pdf", data=PDF_V1)
        with pytest.raises(ValidationError, match="top-level"):
            documents.create_document(s, admin, number="FORM-X", title="x", doc_type="Form", category=None,
                                      revision="01", effective_date=date.today(), file_name="x.pdf", data=PDF_V1,
                                      parent_id=form)


def test_only_pdfs_and_unique_numbers(db, admin):
    _sop(db, admin)
    with db.session() as s:
        with pytest.raises(ValidationError, match="Only PDF"):
            documents.create_document(s, admin, number="SOP-QC-102", title="x", doc_type="SOP", category=None,
                                      revision="01", effective_date=date.today(), file_name="x.docx",
                                      data=b"PK\x03\x04 word file")
        with pytest.raises(ValidationError, match="already in the index"):
            documents.create_document(s, admin, number="sop-qc-101", title="x", doc_type="SOP", category=None,
                                      revision="01", effective_date=date.today(), file_name="x.pdf", data=PDF_V1)


def test_new_revision_supersedes_old(db, admin, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    sop = _sop(db, admin)
    with db.session() as s:
        old_rev = documents.list_documents(s, admin)[0].revision_id
        with pytest.raises(ValidationError, match="what changed"):
            documents.upload_revision(s, admin, sop, revision="02", effective_date=date.today(), file_name="v2.pdf",
                                      data=PDF_V2, change_summary="")
        documents.upload_revision(s, admin, sop, revision="02", effective_date=date.today(), file_name="v2.pdf",
                                  data=PDF_V2, change_summary="Added column equilibration step")
    with db.session() as s:
        row = documents.list_documents(s, analyst)[0]
        assert row.revision == "02" and row.revision_count == 2
        assert documents.open_revision(s, analyst, row.revision_id)[1] == PDF_V2
        with pytest.raises(PermissionDenied):  # analysts cannot open the superseded copy
            documents.open_revision(s, analyst, old_rev)
        label, _ = documents.open_revision(s, admin, old_rev)  # managers can, clearly marked
        assert "SUPERSEDED" in label
        with pytest.raises(ValidationError, match="already has a revision 02"):
            documents.upload_revision(s, admin, sop, revision="02", effective_date=date.today(), file_name="v.pdf",
                                      data=PDF_V2, change_summary="again")


def test_retire_hides_document(db, admin, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    sop = _sop(db, admin)
    with db.session() as s:
        documents.set_active(s, admin, sop, False, "Process discontinued")
    with db.session() as s:
        assert documents.list_documents(s, analyst) == []
        retired = documents.list_documents(s, admin, include_retired=True)[0]
        with pytest.raises(PermissionDenied):
            documents.open_revision(s, analyst, retired.revision_id)


def test_revisions_immutable_and_verified(db, admin):
    _sop(db, admin)
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE document_revisions SET revision = '99'"))
    with db.engine.begin() as conn:
        conn.exec_driver_sql("DROP TRIGGER document_revisions_no_update")
        conn.execute(text("UPDATE document_revisions SET pdf = X'255044462D3120'"))
    with db.session() as s:
        row = documents.list_documents(s, admin)[0]
        with pytest.raises(documents.DocumentIntegrityError):
            documents.open_revision(s, admin, row.revision_id)


# ----- bug reports -------------------------------------------------------------

def test_bug_reports(db, admin, make_user):
    analyst = make_user("jsmith", Role.ANALYST)
    other = make_user("asmith", Role.ANALYST)
    with db.session() as s:
        with pytest.raises(ValidationError, match="title"):
            bug_reports.submit(s, analyst, title="", area="Samples", severity="Low", description="x")
        with pytest.raises(ValidationError, match="PNG or JPEG"):
            bug_reports.submit(s, analyst, title="x", area="Samples", severity="Low", description="x",
                               screenshots=[b"GIF89a"])
        report = bug_reports.submit(s, analyst, title="Due date not saved", area="Samples", severity="Medium",
                                    description="Registered a sample with a due date; it shows blank.",
                                    steps="1. Register\n2. Tick due date", expected="Due date shown",
                                    screenshots=[PNG])
        assert report.number == "BUG-0001"
    with db.session() as s:
        assert [r.number for r in bug_reports.list_reports(s, analyst)] == ["BUG-0001"]
        assert bug_reports.list_reports(s, other) == []  # only your own reports
        assert len(bug_reports.list_reports(s, None)) == 1  # developer tool sees all
        assert bug_reports.screenshots(s, admin, report.id) == [PNG]
        with pytest.raises(PermissionDenied):
            bug_reports.screenshots(s, other, report.id)
        with pytest.raises(PermissionDenied):
            bug_reports.update_status(s, analyst, report.id, BugStatus.FIXED, "x")
        with pytest.raises(ValidationError, match="resolution"):
            bug_reports.update_status(s, admin, report.id, BugStatus.FIXED)
        bug_reports.update_status(s, admin, report.id, BugStatus.FIXED, "Fixed in version 0.1.1")
    with db.session() as s:
        assert bug_reports.list_reports(s, admin, include_closed=False) == []
