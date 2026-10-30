"""Importing results from instrument CSV / TXT exports.

Most instruments (chromatography data systems, balances, pH meters, Karl
Fischer and autotitrators, UV-Vis) can export a table. An **import template**,
set up once per method by Admin / QA, says how to read that table:

* which line holds the column headings (0 = no headings: columns are then
  called "Column 1", "Column 2", ...), the separator, and whether numbers use a
  decimal comma;
* one **rule** per method parameter: the column holding the value, which rows
  to use (rows whose *match column* contains the *match text*; ``{sample_id}``
  stands for the sample being tested), and how to use them: each row is a
  replicate, the average of the rows, or the first row.

Importing never saves results by itself: the values fill the result entry form,
and the analyst checks and saves them as usual. The file is stored exactly as
read (immutable, with its SHA-256 fingerprint), together with the template
settings used and the values found, so the reviewer can trace every number
back to the instrument export.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from aurum_lims.db.models import Analysis, ImportedFile, ImportTemplate, Sample, TestMethod, User, utcnow
from aurum_lims.errors import NotFound, ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, formulas, methods
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.methods import ParameterSpec, ResultType

MAX_FILE_BYTES = 20 * 1024 * 1024
SAMPLE_PLACEHOLDER = "{sample_id}"
DELIMITERS = {"auto": "Detect automatically", ",": "Comma (,)", ";": "Semicolon (;)", "\t": "Tab", "|": "Bar (|)"}


class TakeMode(StrEnum):
    REPLICATES = "replicates"
    MEAN = "mean"
    FIRST = "first"

    @property
    def label(self) -> str:
        return {"replicates": "Each row is a replicate", "mean": "Average of the rows",
                "first": "First matching row"}[self.value]


@dataclass
class RuleSpec:
    parameter_key: str
    column: str
    match_column: str = ""
    match_text: str = ""
    take: str = TakeMode.REPLICATES


@dataclass
class TemplateSpec:
    name: str
    method_code: str
    header_row: int = 1
    delimiter: str = "auto"
    decimal_comma: bool = False
    rules: list[RuleSpec] = field(default_factory=list)

    def settings_json(self) -> str:
        data = asdict(self)
        del data["name"], data["method_code"]
        return json.dumps(data, sort_keys=True, ensure_ascii=False)

    @classmethod
    def from_row(cls, row: ImportTemplate) -> TemplateSpec:
        data = json.loads(row.settings)
        return cls(row.name, row.method_code, int(data.get("header_row", 1)), data.get("delimiter", "auto"),
                   bool(data.get("decimal_comma", False)), [RuleSpec(**r) for r in data.get("rules", [])])


# ---------------------------------------------------------------------------
# reading files (pure functions, also used for the live preview)
# ---------------------------------------------------------------------------


@dataclass
class Table:
    headers: list[str]
    rows: list[list[str]]
    delimiter: str

    def column_index(self, name: str) -> int | None:
        wanted = name.strip().lower()
        for index, header in enumerate(self.headers):
            if header.strip().lower() == wanted:
                return index
        return None


def decode(data: bytes) -> str:
    """Text of an export: UTF-8 / UTF-16 (with BOM), else UTF-8, else Windows-1252."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def _guess_delimiter(lines: list[str]) -> str:
    sample = "\n".join(lines[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        counts = {d: sample.count(d) for d in ",;\t|"}
        best = max(counts, key=counts.get)
        return best if counts[best] else ","


def parse_table(data: bytes, header_row: int = 1, delimiter: str = "auto") -> Table:
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError(f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    lines = decode(data).splitlines()
    if header_row < 0 or header_row > len(lines):
        raise ValidationError(f"The file has {len(lines)} line(s), so line {header_row} cannot be the headings.")
    body = lines[header_row:] if header_row else lines
    used = _guess_delimiter(lines[max(header_row - 1, 0):]) if delimiter == "auto" else delimiter
    rows = [[cell.strip() for cell in row] for row in csv.reader(body, delimiter=used)]
    rows = [row for row in rows if any(row)]
    width = max([len(r) for r in rows] + [0])
    headers = next(csv.reader([lines[header_row - 1]], delimiter=used)) if header_row else []
    headers = [h.strip() for h in headers]
    width = max(width, len(headers))
    headers = [h if h else f"Column {i + 1}" for i, h in enumerate(headers + [""] * (width - len(headers)))]
    return Table(headers, rows, used)


def _number(text: str, decimal_comma: bool) -> Decimal | None:
    value = text.replace(" ", "").replace(" ", "")
    if decimal_comma:
        value = value.replace(".", "").replace(",", ".")
    try:
        return formulas.parse_decimal(value)
    except ValidationError:
        return None


def _as_entered(value: Decimal) -> str:
    """The number with exactly the digits the instrument reported (25.30 stays 25.30)."""
    return format(value, "f")


@dataclass
class RuleOutcome:
    parameter_key: str
    parameter_name: str
    rows_matched: int = 0
    raw_values: list[str] = field(default_factory=list)
    filled: dict[int, str] = field(default_factory=dict)  # replicate -> value
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)  # information only, e.g. which rows were averaged
    error: str | None = None


@dataclass
class ImportResult:
    outcomes: list[RuleOutcome]

    @property
    def values(self) -> dict[tuple[str, int], str]:
        return {(o.parameter_key, rep): v for o in self.outcomes if not o.error for rep, v in o.filled.items()}

    @property
    def errors(self) -> list[str]:
        return [f"{o.parameter_name}: {o.error}" for o in self.outcomes if o.error]

    @property
    def warnings(self) -> list[str]:
        return [f"{o.parameter_name}: {w}" for o in self.outcomes for w in o.warnings]

    @property
    def notes(self) -> list[str]:
        return [f"{o.parameter_name}: {n}" for o in self.outcomes for n in o.notes]

    def as_record(self) -> list[dict]:
        return [{"parameter": o.parameter_key, "rows_matched": o.rows_matched, "raw": o.raw_values,
                 "filled": {str(k): v for k, v in o.filled.items()}, "warnings": o.warnings, "notes": o.notes,
                 "error": o.error}
                for o in self.outcomes]


def _apply_rule(rule: RuleSpec, p: ParameterSpec, table: Table, sample_id: str, decimal_comma: bool) -> RuleOutcome:
    out = RuleOutcome(p.key, p.name)
    column = table.column_index(rule.column)
    if column is None:
        out.error = f"column '{rule.column}' is not in the file (columns: {', '.join(table.headers)})."
        return out
    rows = table.rows
    if rule.match_column.strip():
        match_index = table.column_index(rule.match_column)
        if match_index is None:
            out.error = f"match column '{rule.match_column}' is not in the file."
            return out
        needle = rule.match_text.replace(SAMPLE_PLACEHOLDER, sample_id).strip().lower()
        rows = [r for r in rows if match_index < len(r) and needle in r[match_index].lower()]
    out.raw_values = [r[column] for r in rows if column < len(r) and r[column] != ""]
    out.rows_matched = len(out.raw_values)
    if not out.raw_values:
        out.error = "no matching rows with a value were found."
        return out

    take = TakeMode(rule.take)
    if p.result_type != ResultType.NUMERIC:
        if take is TakeMode.MEAN:
            out.error = "text and Pass / Fail results cannot be averaged."
            return out
        value = out.raw_values[0]
        if p.result_type == ResultType.PASS_FAIL:
            value = value.strip().capitalize()
            if value not in ("Pass", "Fail"):
                out.error = f"'{out.raw_values[0]}' is not Pass or Fail."
                return out
        out.filled[1] = value
        if len(out.raw_values) > 1:
            out.warnings.append(f"{len(out.raw_values)} rows matched; the first was used.")
        return out

    numbers = [_number(v, decimal_comma) for v in out.raw_values]
    bad = [raw for raw, n in zip(out.raw_values, numbers) if n is None]
    if bad:
        out.error = f"not a number: {', '.join(repr(b) for b in bad[:5])}."
        return out
    if take is TakeMode.REPLICATES:
        if len(numbers) < p.replicates:
            out.warnings.append(f"only {len(numbers)} row(s) matched; the method has {p.replicates} replicate(s).")
        elif len(numbers) > p.replicates:
            out.warnings.append(f"{len(numbers)} rows matched; only the first {p.replicates} were used.")
        for i, n in enumerate(numbers[:p.replicates], start=1):
            out.filled[i] = _as_entered(n)
        return out
    if p.replicates > 1:
        out.error = f"this parameter has {p.replicates} replicates; use 'Each row is a replicate'."
        return out
    if take is TakeMode.MEAN:
        out.filled[1] = formulas.format_decimal(formulas.mean(numbers))
        if len(numbers) > 1:
            out.notes.append(f"average of {len(numbers)} rows: {', '.join(out.raw_values)}.")
    else:
        out.filled[1] = _as_entered(numbers[0])
        if len(numbers) > 1:
            out.warnings.append(f"{len(numbers)} rows matched; the first was used.")
    return out


def apply_template(spec: TemplateSpec, table: Table, parameters: list[ParameterSpec], sample_id: str) -> ImportResult:
    by_key = {p.key: p for p in parameters}
    outcomes = []
    for rule in spec.rules:
        p = by_key.get(rule.parameter_key)
        if p is None or p.calculated:
            outcomes.append(RuleOutcome(rule.parameter_key, rule.parameter_key,
                                        error="not an entered parameter of this method version."))
            continue
        outcomes.append(_apply_rule(rule, p, table, sample_id, spec.decimal_comma))
    return ImportResult(outcomes)


def read_file(spec: TemplateSpec, data: bytes, parameters: list[ParameterSpec],
              sample_id: str) -> tuple[Table, ImportResult]:
    table = parse_table(data, spec.header_row, spec.delimiter)
    return table, apply_template(spec, table, parameters, sample_id)


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------


def importable_parameters(session: Session, method_code: str) -> list[ParameterSpec]:
    """Entered (not calculated) parameters of the newest version of a method."""
    method = session.scalar(select(TestMethod).where(TestMethod.code == method_code)
                            .order_by(TestMethod.version.desc()).limit(1))
    if method is None:
        return []
    return [p for p in (methods.to_spec(x) for x in methods.parameters_of(session, method.id)) if not p.calculated]


def validate_template(session: Session, spec: TemplateSpec) -> TemplateSpec:
    name = " ".join((spec.name or "").split())
    if not name:
        raise ValidationError("Give the template a name, e.g. Empower assay export.")
    code = (spec.method_code or "").strip().upper()
    params = {p.key: p for p in importable_parameters(session, code)}
    if not params:
        raise ValidationError(f"Method {code or '?'} not found, or it has no entered parameters.")
    if not 0 <= int(spec.header_row) <= 100:
        raise ValidationError("The headings line must be between 0 (no headings) and 100.")
    if spec.delimiter not in DELIMITERS:
        raise ValidationError("Choose a separator.")
    rules, seen = [], set()
    for index, rule in enumerate(spec.rules, start=1):
        key = (rule.parameter_key or "").strip()
        where = f"Rule {index} ({params[key].name if key in params else key or 'no parameter'})"
        if key not in params:
            raise ValidationError(f"{where}: choose an entered parameter of method {code}.")
        if key in seen:
            raise ValidationError(f"{where}: this parameter already has a rule.")
        seen.add(key)
        column = rule.column.strip()
        if not column:
            raise ValidationError(f"{where}: enter the column that holds the value.")
        take = TakeMode(rule.take)
        match_column, match_text = rule.match_column.strip(), rule.match_text.strip()
        if match_text and not match_column:
            raise ValidationError(f"{where}: choose the column the match text is looked for in.")
        rules.append(RuleSpec(key, column, match_column, match_text if match_column else "", take.value))
    if not rules:
        raise ValidationError("Add at least one rule.")
    return TemplateSpec(name, code, int(spec.header_row), spec.delimiter, bool(spec.decimal_comma), rules)


def create_template(session: Session, actor: SessionUser, spec: TemplateSpec) -> ImportTemplate:
    require(actor, Permission.MANAGE_MASTER_DATA)
    spec = validate_template(session, spec)
    _check_unique(session, spec)
    row = ImportTemplate(name=spec.name, method_code=spec.method_code, settings=spec.settings_json(),
                         is_active=True, created_by_id=actor.id)
    session.add(row)
    session.flush()
    audit.record(session, AuditAction.IMPORT_TEMPLATE_CREATED, actor=actor, entity_type="import_template",
                 entity_id=f"{spec.method_code} / {spec.name}", new=json.loads(spec.settings_json()))
    return row


def update_template(session: Session, actor: SessionUser, template_id: int, spec: TemplateSpec,
                    reason: str) -> ImportTemplate:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason for the change is required.")
    row = _get(session, template_id)
    spec = validate_template(session, TemplateSpec(spec.name, row.method_code, spec.header_row, spec.delimiter,
                                                   spec.decimal_comma, spec.rules))
    _check_unique(session, spec, template_id)
    old = {"name": row.name, **json.loads(row.settings)}
    new = {"name": spec.name, **json.loads(spec.settings_json())}
    if old == new:
        raise ValidationError("Nothing was changed.")
    row.name, row.settings = spec.name, spec.settings_json()
    row.updated_at, row.updated_by_id = utcnow(), actor.id
    audit.record(session, AuditAction.IMPORT_TEMPLATE_UPDATED, actor=actor, entity_type="import_template",
                 entity_id=f"{row.method_code} / {row.name}", old=old, new=new, reason=reason.strip())
    return row


def set_active(session: Session, actor: SessionUser, template_id: int, active: bool, reason: str) -> ImportTemplate:
    require(actor, Permission.MANAGE_MASTER_DATA)
    if not reason or not reason.strip():
        raise ValidationError("A reason is required.")
    row = _get(session, template_id)
    if row.is_active == active:
        raise ValidationError("The template is already " + ("active." if active else "inactive."))
    row.is_active, row.updated_at, row.updated_by_id = active, utcnow(), actor.id
    audit.record(session, AuditAction.IMPORT_TEMPLATE_UPDATED, actor=actor, entity_type="import_template",
                 entity_id=f"{row.method_code} / {row.name}", old={"active": not active}, new={"active": active},
                 reason=reason.strip())
    return row


def _get(session: Session, template_id: int) -> ImportTemplate:
    row = session.get(ImportTemplate, template_id)
    if row is None:
        raise NotFound("Import template not found.")
    return row


def _check_unique(session: Session, spec: TemplateSpec, template_id: int | None = None) -> None:
    clash = session.scalar(select(ImportTemplate.id).where(ImportTemplate.method_code == spec.method_code,
                                                           ImportTemplate.name == spec.name))
    if clash is not None and clash != template_id:
        raise ValidationError(f"Method {spec.method_code} already has a template called '{spec.name}'.")


def templates_for(session: Session, method_code: str, include_inactive: bool = False) -> list[ImportTemplate]:
    stmt = select(ImportTemplate).where(ImportTemplate.method_code == method_code).order_by(ImportTemplate.name)
    if not include_inactive:
        stmt = stmt.where(ImportTemplate.is_active.is_(True))
    return list(session.scalars(stmt))


# ---------------------------------------------------------------------------
# importing into a test
# ---------------------------------------------------------------------------


def import_file(session: Session, actor: SessionUser, analysis_id: int, template_id: int, file_name: str,
                data: bytes) -> tuple[ImportedFile, ImportResult]:
    """Read ``data`` with the template, store the file and what was found, and
    return the values for the result entry form (not saved as results)."""
    from aurum_lims.services.testing import OPEN_ANALYSIS

    require(actor, Permission.EDIT_RECORDS)
    analysis = session.get(Analysis, analysis_id)
    if analysis is None:
        raise NotFound("Test not found.")
    if analysis.status not in OPEN_ANALYSIS:
        raise ValidationError(f"Results can no longer be entered on this test ({analysis.status}).")
    if not data:
        raise ValidationError("The file is empty.")
    template = _get(session, template_id)
    method = session.get(TestMethod, analysis.method_id)
    if template.method_code != method.code or not template.is_active:
        raise ValidationError("This template does not belong to the test's method or is no longer active.")
    sample = session.get(Sample, analysis.sample_id)
    spec = TemplateSpec.from_row(template)
    parameters = [methods.to_spec(p) for p in methods.parameters_of(session, method.id)]
    _, result = read_file(spec, data, parameters, sample.sample_id)
    if not result.values:
        raise ValidationError("No values could be read from the file:\n" + "\n".join(result.errors))
    sha = hashlib.sha256(data).hexdigest()
    record = ImportedFile(analysis_id=analysis.id, template_id=template.id, template_name=template.name,
                          settings=spec.settings_json(), file_name=file_name[:255], content=data,
                          size_bytes=len(data), sha256=sha,
                          values_found=json.dumps(result.as_record(), ensure_ascii=False),
                          imported_by_id=actor.id)
    session.add(record)
    session.flush()
    audit.record(session, AuditAction.RESULTS_IMPORTED, actor=actor, entity_type="test",
                 entity_id=f"{sample.sample_id} / {method.code} v{method.version} (#{analysis.id})",
                 new={"file": file_name, "sha256": sha, "template": template.name,
                      "values": {f"{k}[{r}]": v for (k, r), v in result.values.items()},
                      "problems": result.errors})
    return record, result


@dataclass
class ImportedFileRow:
    id: int
    file_name: str
    template_name: str
    imported_at: object
    imported_by: str
    size_bytes: int
    sha256: str


def imported_files_for(session: Session, analysis_id: int) -> list[ImportedFileRow]:
    rows = session.execute(select(ImportedFile, User.full_name).join(User, ImportedFile.imported_by_id == User.id)
                           .where(ImportedFile.analysis_id == analysis_id).order_by(ImportedFile.id))
    return [ImportedFileRow(f.id, f.file_name, f.template_name, f.imported_at, who, f.size_bytes, f.sha256)
            for f, who in rows]


def get_file(session: Session, actor: SessionUser, file_id: int) -> tuple[str, bytes]:
    """The stored file, after checking it still matches its fingerprint."""
    require(actor, Permission.VIEW_RECORDS)
    record = session.get(ImportedFile, file_id)
    if record is None:
        raise NotFound("Imported file not found.")
    if hashlib.sha256(record.content).hexdigest() != record.sha256:
        raise ValidationError(f"{record.file_name} does not match its recorded fingerprint and may have been altered.")
    return record.file_name, record.content


def sample_text(data: bytes, lines: int = 12) -> str:
    """The first lines of a file, for showing next to the headings setting."""
    return "\n".join(decode(data).splitlines()[:lines])

