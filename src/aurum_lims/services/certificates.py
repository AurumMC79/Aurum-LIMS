"""Certificates of Analysis and Test Reports.

A certificate is generated as a PDF at the moment QA decides on a sample and
stored in the database exactly as issued, together with its SHA-256
fingerprint. Re-printing always returns those stored bytes, so every copy is
identical to the original; the fingerprint is re-checked each time.
"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims import config
from aurum_lims.db.models import (
    Analysis,
    Certificate,
    ElectronicSignature,
    IdCounter,
    Sample,
    SampleDecision,
    SampleType,
    Specification,
    TestMethod,
    User,
)
from aurum_lims.errors import AurumError, NotFound
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, formulas, testing
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.lab_info import get_lab_info
from aurum_lims.services.sample_types import FIELD_LABELS

COA = "Certificate of Analysis"
TEST_REPORT = "Test Report"
PREFIX = {COA: "CoA", TEST_REPORT: "TR"}
AMENDMENT_SUFFIX = re.compile(r"-A\d+$")


class CertificateIntegrityError(AurumError):
    pass


def _esc(value) -> str:
    return html.escape("" if value is None else str(value))


def _when(moment: datetime | None) -> str:
    """Local date/time with its UTC offset, e.g. 2026-10-28 09:41 (UTC-07:00)."""
    if not moment:
        return ""
    local = moment.astimezone()
    offset = local.strftime("%z")
    return f"{local:%Y-%m-%d %H:%M} (UTC{offset[:3]}:{offset[3:]})"


def _next_number(session: Session, kind: str) -> str:
    year = datetime.now().astimezone().year
    scope = PREFIX[kind].lower()
    counter = session.get(IdCounter, (scope, str(year)))
    if counter is None:
        counter = IdCounter(scope=scope, period=str(year), last_value=0)
        session.add(counter)
    counter.last_value += 1
    session.flush()
    return f"{PREFIX[kind]}-{year}-{counter.last_value:04d}"


def build_html(session: Session, sample: Sample, decision: SampleDecision, number: str, kind: str,
               supersedes: Certificate | None = None, amendment_reason: str | None = None) -> str:
    lab = get_lab_info(session)
    sample_type = session.get(SampleType, sample.sample_type_id)
    spec = session.get(Specification, sample.specification_id) if sample.specification_id else None
    names = {u.id: u.full_name for u in session.scalars(select(User))}
    signer = session.get(ElectronicSignature, decision.signature_id)

    info_rows = [("Sample ID", sample.sample_id), ("Sample type", sample_type.name)]
    info_rows += [(label, getattr(sample, name)) for name, label in FIELD_LABELS.items() if getattr(sample, name)]
    info_rows += [
        ("Received", _when(sample.received_at)),
        ("Specification", f"{spec.code} v{spec.version} – {spec.name}" if spec else "None (results reported only)"),
    ]
    info = "".join(f"<tr><td class='k'>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in info_rows if v)

    result_rows, signatures, detection_notes = [], [], []
    analyses = session.scalars(select(Analysis).where(Analysis.sample_id == sample.id,
                                                      Analysis.status == testing.AnalysisStatus.REVIEWED)
                               .order_by(Analysis.id))
    for analysis in analyses:
        method = session.get(TestMethod, analysis.method_id)
        for res in testing.reportable_results(session, analysis.id):
            colour = {"Pass": "#067647", "Fail": "#b42318"}.get(res.conformance, "#4a5562")
            label = {"Pass": "Conforms", "Fail": "Does not conform", "Report": "Reported"}.get(res.conformance, "")
            if res.reported_value == formulas.NOT_DETECTED:
                detection_notes.append(f"{res.parameter_name}: ND = not detected, below the limit of detection "
                                       f"({formulas.with_unit(res.lod, res.unit)}).")
            elif res.loq and res.reported_value.startswith("<"):
                detection_notes.append(f"{res.parameter_name}: detected below the limit of quantitation "
                                       f"({formulas.with_unit(res.loq, res.unit)}).")
            result_rows.append(
                f"<tr><td>{_esc(method.name)}<br/><span class='m'>{_esc(method.code)} v{method.version}</span></td>"
                f"<td>{_esc(res.parameter_name)}</td><td><b>{_esc(formulas.with_unit(res.reported_value, res.unit))}</b></td>"
                f"<td>{_esc(res.spec_text or '—')}</td><td style='color:{colour}'><b>{label}</b></td></tr>"
            )
        signatures.append(
            f"<tr><td>{_esc(method.code)} v{method.version}</td>"
            f"<td>Performed by {_esc(names.get(analysis.completed_by_id))}<br/>"
            f"<span class='m'>{_esc(_when(analysis.completed_at))}</span></td>"
            f"<td>Reviewed by {_esc(names.get(analysis.reviewed_by_id))}<br/>"
            f"<span class='m'>{_esc(_when(analysis.reviewed_at))}</span></td></tr>"
        )

    if decision.decision == "Approved":
        conclusion = (f"The sample <b>conforms</b> to specification {_esc(spec.code)} v{spec.version}." if spec
                      else "All tests were performed and reviewed. Results are reported without limits.")
        conclusion_colour = "#067647"
    else:
        conclusion = ("The sample <b>does not conform</b> to specification "
                      f"{_esc(spec.code)} v{spec.version} and has been <b>rejected</b>." if spec and not decision.conforms
                      else "The sample has been <b>rejected</b>.")
        conclusion_colour = "#b42318"
    comment = f"<p><b>Comment:</b> {_esc(decision.comment)}</p>" if decision.comment else ""
    notes = "".join(f"<br/>{_esc(n)}" for n in detection_notes)
    notes = f"<p class='small'>{notes.removeprefix('<br/>')}</p>" if notes else ""
    address = "<br/>".join(_esc(line) for line in lab.lab_address.splitlines() if line.strip())
    title = f"Amended {kind}" if supersedes is not None else kind
    amended = "" if supersedes is None else (
        "<br/><table width='100%' cellspacing='0' cellpadding='8' style='border: 1px solid #c9a227'>"
        "<tr><td bgcolor='#fbf3dc'>"
        f"<b>Amended certificate.</b> This document supersedes {_esc(supersedes.kind)} "
        f"<b>{_esc(supersedes.number)}</b> issued {_esc(_when(supersedes.issued_at))}, which must no longer be used."
        f"<br/>Reason for amendment: {_esc(amendment_reason or 'Not recorded')}</td></tr></table>")

    return f"""
<html><head><style>
 body {{ font-family: 'Segoe UI', Arial, sans-serif; font-size: 9.5pt; color: #1f2933; }}
 h1 {{ font-size: 16pt; color: #9a7b1c; margin: 0; }}
 h2 {{ font-size: 11pt; margin-top: 14px; margin-bottom: 4px; border-bottom: 1px solid #c9a227; }}
 table {{ border-collapse: collapse; }}
 td, th {{ padding: 4px 6px; vertical-align: top; }}
 th {{ background: #f3ecd6; text-align: left; font-weight: 600; }}
 .grid td, .grid th {{ border: 1px solid #d9dde2; }}
 .k {{ color: #4a5562; width: 32%; }}
 .m {{ color: #6b7785; font-size: 8pt; }}
 .small {{ color: #6b7785; font-size: 7.5pt; }}
</style></head><body>
<table width='100%'><tr>
 <td><h1>{_esc(lab.lab_name or 'Laboratory name not set')}</h1>
  <span class='m'>{address}{'<br/>' if address and lab.lab_contact else ''}{_esc(lab.lab_contact)}</span></td>
 <td align='right'><span style='font-size:14pt; font-weight:600'>{_esc(title.upper())}</span><br/>
  No. <b>{_esc(number)}</b><br/><span class='m'>Issued {_esc(_when(decision.decided_at))}</span></td>
</tr></table>
{amended}
<h2>Sample</h2>
<table class='grid' width='100%' cellspacing='0'>{info}</table>
<h2>Results</h2>
<table class='grid' width='100%' cellspacing='0'>
 <tr><th>Test</th><th>Parameter</th><th>Result</th><th>Specification</th><th>Conformance</th></tr>
 {''.join(result_rows) or "<tr><td colspan='5'>No reviewed results.</td></tr>"}
</table>
{notes}
<h2>Conclusion</h2>
<p style='color:{conclusion_colour}; font-size:11pt'>{conclusion}</p>
{comment}
<h2>Electronic signatures</h2>
<table class='grid' width='100%' cellspacing='0'>
 <tr><th>Test</th><th>Performed</th><th>Reviewed</th></tr>
 {''.join(signatures)}
</table>
<p><b>{_esc(signer.meaning)} {_esc(signer.full_name)}</b> ({_esc(signer.username)}) on
 {_esc(_when(signer.signed_at))} — {_esc(decision.decision)}</p>
<p class='small'>{_esc(lab.coa_statement)}</p>
<p class='small'>Signed electronically in {_esc(config.APP_NAME)} {_esc(config.APP_VERSION)}. The electronic signature
 records and audit trail for this document are retained in the system. Document {_esc(number)}.</p>
</body></html>
"""


def render_pdf(document_html: str, title: str, paper_size: str = "Letter") -> bytes:
    """Render HTML to PDF bytes using Qt's built-in PDF writer."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF
    from PySide6.QtGui import QPageLayout, QPageSize, QPdfWriter, QTextDocument
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:  # e.g. when called from a script
        QApplication([])
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setTitle(title)
    writer.setCreator(f"{config.APP_NAME} {config.APP_VERSION}")
    writer.setPageSize(QPageSize(QPageSize.A4 if paper_size == "A4" else QPageSize.Letter))
    # QTextDocument adds its own 2 cm margin (and page numbers) when printing, so the
    # writer itself gets no margin.
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
    document = QTextDocument()
    document.setHtml(document_html)
    document.print_(writer)
    buffer.close()
    return bytes(data)


def issue(session: Session, actor: SessionUser, sample: Sample, decision: SampleDecision) -> Certificate:
    """Generate and store the certificate for a decision (called by the decision service)."""
    kind = COA if decision.decision == "Approved" else TEST_REPORT
    previous = current_certificate(session, sample.id)
    amendment = 0 if previous is None else previous.amendment + 1
    reason = sample.amendment_reason if previous is not None else None
    if previous is not None and previous.kind == kind:
        # An amendment keeps the original number with a suffix: CoA-2026-0001-A1, -A2, ...
        number = f"{AMENDMENT_SUFFIX.sub('', previous.number)}-A{amendment}"
    else:
        number = _next_number(session, kind)
    document_html = build_html(session, sample, decision, number, kind, previous, reason)
    title = f"{'Amended ' if previous else ''}{kind} {number} – {sample.sample_id}"
    pdf = render_pdf(document_html, title, get_lab_info(session).paper_size)
    certificate = Certificate(number=number, kind=kind, sample_id=sample.id, decision_id=decision.id,
                              issued_by_id=actor.id, pdf=pdf, sha256=hashlib.sha256(pdf).hexdigest(),
                              supersedes_id=previous.id if previous else None, amendment=amendment,
                              amendment_reason=reason)
    session.add(certificate)
    sample.amendment_reason = None
    session.flush()
    new = {"kind": kind, "sample": sample.sample_id, "sha256": certificate.sha256}
    if previous is not None:
        new.update(supersedes=previous.number, amendment=amendment)
    audit.record(session, AuditAction.CERTIFICATE_ISSUED, actor=actor, entity_type="certificate", entity_id=number,
                 new=new, reason=reason)
    return certificate


def current_certificate(session: Session, sample_pk: int) -> Certificate | None:
    """The sample's latest certificate (the one not superseded)."""
    return session.scalar(select(Certificate).where(Certificate.sample_id == sample_pk)
                          .order_by(Certificate.id.desc()).limit(1))


@dataclass
class CertificateRow:
    id: int
    number: str
    kind: str
    sample_pk: int
    sample_id: str
    material: str | None
    decision: str
    issued_at: datetime
    issued_by: str
    amendment: int = 0
    superseded_by: str | None = None


def list_certificates(session: Session, actor: SessionUser, sample_pk: int | None = None) -> list[CertificateRow]:
    require(actor, Permission.VIEW_RECORDS)
    stmt = (
        select(Certificate, Sample, SampleDecision.decision, User.full_name)
        .join(Sample, Certificate.sample_id == Sample.id)
        .join(SampleDecision, Certificate.decision_id == SampleDecision.id)
        .join(User, Certificate.issued_by_id == User.id)
        .order_by(Certificate.id.desc())
    )
    if sample_pk is not None:
        stmt = stmt.where(Certificate.sample_id == sample_pk)
    replaced_by = {old: number for old, number in session.execute(
        select(Certificate.supersedes_id, Certificate.number).where(Certificate.supersedes_id.is_not(None)))}
    return [
        CertificateRow(c.id, c.number, c.kind, s.id, s.sample_id, s.material_name or s.sampling_point, d,
                       c.issued_at, who, c.amendment, replaced_by.get(c.id))
        for c, s, d, who in session.execute(stmt)
    ]


def get_pdf(session: Session, actor: SessionUser, certificate_id: int) -> tuple[str, bytes]:
    """The stored PDF, after checking it still matches its fingerprint."""
    require(actor, Permission.VIEW_RECORDS)
    certificate = session.get(Certificate, certificate_id)
    if certificate is None:
        raise NotFound("Certificate not found.")
    if hashlib.sha256(certificate.pdf).hexdigest() != certificate.sha256:
        raise CertificateIntegrityError(
            f"{certificate.number} does not match its recorded fingerprint and may have been altered outside "
            "Aurum LIMS. Do not use it; investigate according to your data-integrity procedures.")
    return certificate.number, certificate.pdf


def export_pdf(session: Session, actor: SessionUser, certificate_id: int, path: Path | str) -> Path:
    number, pdf = get_pdf(session, actor, certificate_id)
    path = Path(path)
    path.write_bytes(pdf)
    audit.record(session, AuditAction.CERTIFICATE_EXPORTED, actor=actor, entity_type="certificate", entity_id=number,
                 new={"file": str(path)})
    return path
