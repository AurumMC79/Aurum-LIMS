"""Sample intaking: logging new samples in through the laboratory's own form.

The form is designed in the app (Sample Intaking → Design form…). It is a list
of fields in order. Two kinds of field:

* **Built-in fields** fill in the sample record itself: sample type (always on
  the form), material, lot, quantity, due date, sampling details, and the
  receipt fields. When a storage location is given the sample is received
  straight away; otherwise it waits for receipt. All the usual sample rules
  apply (for example the fields a sample type requires).
* **Custom fields** are anything else the laboratory wants to capture (text,
  number, date, date and time, dropdown, checkbox). Section headings organise
  the form and hold no value.

The scan settings (how barcodes fill the form, see :mod:`barcodes`) are saved
with the design. Every saved design is a new, numbered, immutable version. Each sample keeps an
immutable record of exactly what was entered, with the labels as they appeared,
so later changes to the form never change what was recorded. Until a form is
saved, a built-in starting form (version 0) is used.

Values arrive from the screen as Python values: text as ``str``, numbers as
``str`` (validated here), dates as ``date``, date-times as timezone-aware
``datetime``, checkboxes as ``bool``, dropdowns as ``str`` (storage location
and sample type as their ids).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from aurum_lims.db.models import IntakeFormVersion, IntakeSubmission, Sample, SampleType, User
from aurum_lims.errors import ValidationError
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission, require
from aurum_lims.services import audit, samples, sample_types, storage
from aurum_lims.services.barcodes import GS, ScanMode, ScanSettings
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.samples import MAX_BATCH, PRIORITIES, RECEIPT_CONDITIONS, Receipt, SampleDetails

MAX_FIELDS = 80
KEY_PATTERN = re.compile(r"[^a-z0-9]+")
UNITS = ["g", "mg", "kg", "mL", "L", "µL", "units", "tablets", "capsules", "vials", "bottles", "swabs", "plates"]


class FieldType(StrEnum):
    TEXT = "text"
    MULTILINE = "multiline"
    NUMBER = "number"
    DATE = "date"
    DATETIME = "datetime"
    DROPDOWN = "dropdown"
    CHECKBOX = "checkbox"
    HEADING = "heading"

    @property
    def label(self) -> str:
        return {"text": "Text", "multiline": "Text (several lines)", "number": "Number", "date": "Date",
                "datetime": "Date and time", "dropdown": "Dropdown list", "checkbox": "Checkbox (yes / no)",
                "heading": "Section heading"}[self.value]


CUSTOM_TYPES = [t for t in FieldType if t is not FieldType.HEADING]


@dataclass(frozen=True)
class Builtin:
    label: str
    type: FieldType
    help: str = ""
    choices: tuple[str, ...] = ()  # fixed choices; sample type and storage location are filled from the system


BUILTINS: dict[str, Builtin] = {
    "sample_type": Builtin("Sample type", FieldType.DROPDOWN, "Decides the sample ID format and required fields."),
    "count": Builtin("Number of samples", FieldType.NUMBER, f"Registers this many samples with the same details "
                     f"(1–{MAX_BATCH})."),
    "material_name": Builtin(sample_types.FIELD_LABELS["material_name"], FieldType.TEXT),
    "lot_number": Builtin(sample_types.FIELD_LABELS["lot_number"], FieldType.TEXT),
    "supplier": Builtin(sample_types.FIELD_LABELS["supplier"], FieldType.TEXT),
    "sampling_point": Builtin(sample_types.FIELD_LABELS["sampling_point"], FieldType.TEXT),
    "stability_study": Builtin(sample_types.FIELD_LABELS["stability_study"], FieldType.TEXT),
    "stability_timepoint": Builtin(sample_types.FIELD_LABELS["stability_timepoint"], FieldType.TEXT),
    "stability_condition": Builtin(sample_types.FIELD_LABELS["stability_condition"], FieldType.TEXT),
    "quantity": Builtin("Quantity", FieldType.NUMBER),
    "quantity_unit": Builtin("Quantity unit", FieldType.DROPDOWN, choices=tuple(UNITS)),
    "container": Builtin("Container", FieldType.TEXT),
    "priority": Builtin("Priority", FieldType.DROPDOWN, choices=tuple(PRIORITIES)),
    "due_date": Builtin("Due date", FieldType.DATE),
    "sampled_at": Builtin("Sampled on", FieldType.DATETIME),
    "sampled_by": Builtin("Sampled by", FieldType.TEXT),
    "notes": Builtin("Notes", FieldType.MULTILINE),
    "location_id": Builtin("Storage location", FieldType.DROPDOWN,
                           "When filled in, the sample is received into this location straight away."),
    "receipt_condition": Builtin("Condition on receipt", FieldType.DROPDOWN, choices=tuple(RECEIPT_CONDITIONS)),
    "receipt_temperature": Builtin("Temperature on receipt", FieldType.TEXT),
    "receipt_notes": Builtin("Receipt notes", FieldType.MULTILINE,
                             "Required when the condition on receipt is not Acceptable."),
}
DETAIL_FIELDS = {f for f in SampleDetails.__dataclass_fields__}
RECEIPT_FIELDS = {"location_id", "receipt_condition", "receipt_temperature", "receipt_notes"}


@dataclass
class FieldSpec:
    key: str
    label: str
    type: str
    builtin: bool = False
    required: bool = False
    help: str = ""
    choices: list[str] = field(default_factory=list)
    default: str = ""


def builtin_field(key: str, required: bool = False, label: str | None = None) -> FieldSpec:
    info = BUILTINS[key]
    return FieldSpec(key, label or info.label, info.type.value, True, required or key == "sample_type", info.help,
                     list(info.choices), "Normal" if key == "priority" else "Acceptable"
                     if key == "receipt_condition" else "1" if key == "count" else "")


def heading(text: str, key: str) -> FieldSpec:
    return FieldSpec(key, text, FieldType.HEADING.value)


DEFAULT_FORM: list[FieldSpec] = [
    heading("Sample", "h_sample"), builtin_field("sample_type"), builtin_field("material_name"),
    builtin_field("lot_number"), builtin_field("supplier"), builtin_field("quantity"), builtin_field("quantity_unit"),
    builtin_field("container"), builtin_field("count"),
    heading("Sampling", "h_sampling"), builtin_field("sampled_at"), builtin_field("sampled_by"),
    heading("Scheduling", "h_scheduling"), builtin_field("priority"), builtin_field("due_date"),
    heading("Receipt", "h_receipt"), builtin_field("location_id"), builtin_field("receipt_condition"),
    builtin_field("receipt_temperature"), builtin_field("receipt_notes"),
    heading("Other", "h_other"), builtin_field("notes"),
]


def make_key(label: str, taken: set[str]) -> str:
    """A stable key for a custom field from its label, e.g. 'PO number' → 'po_number'."""
    base = KEY_PATTERN.sub("_", label.lower()).strip("_")[:40] or "field"
    base = f"c_{base}"  # never clashes with a built-in
    key, n = base, 2
    while key in taken:
        key, n = f"{base}_{n}", n + 1
    return key


# ---------------------------------------------------------------------------
# the form design
# ---------------------------------------------------------------------------


def validate_form(fields: list[FieldSpec]) -> list[FieldSpec]:
    if not fields:
        raise ValidationError("The form needs at least the sample type field.")
    if len(fields) > MAX_FIELDS:
        raise ValidationError(f"A form can have at most {MAX_FIELDS} fields.")
    cleaned, keys = [], set()
    for index, f in enumerate(fields, start=1):
        label = " ".join((f.label or "").split())
        where = f"Field {index} ({label or f.key or 'unnamed'})"
        if not label:
            raise ValidationError(f"{where}: give it a label.")
        kind = FieldType(f.type)
        if f.builtin:
            if f.key not in BUILTINS:
                raise ValidationError(f"{where}: unknown built-in field.")
            kind = BUILTINS[f.key].type
        elif not f.key.startswith(("c_", "h_")):
            raise ValidationError(f"{where}: custom field keys start with c_.")
        if f.key in keys:
            raise ValidationError(f"{where}: this field is on the form twice.")
        keys.add(f.key)
        choices = [c.strip() for c in f.choices if c.strip()]
        if f.builtin and BUILTINS[f.key].choices:
            choices = list(BUILTINS[f.key].choices)
        if kind is FieldType.DROPDOWN and not f.builtin and not choices:
            raise ValidationError(f"{where}: a dropdown needs at least one choice.")
        if len(set(choices)) != len(choices):
            raise ValidationError(f"{where}: a dropdown choice is listed twice.")
        default = (f.default or "").strip()
        if default and kind is FieldType.DROPDOWN and choices and default not in choices:
            raise ValidationError(f"{where}: the default must be one of the choices.")
        if default and kind is FieldType.NUMBER and _decimal(default) is None:
            raise ValidationError(f"{where}: the default must be a number.")
        required = f.required or f.key == "sample_type"
        if kind is FieldType.HEADING:
            required, default, choices = False, "", []
        cleaned.append(FieldSpec(f.key, label, kind.value, f.builtin, required, (f.help or "").strip(), choices,
                                 default))
    if "sample_type" not in keys:
        raise ValidationError("The sample type field must stay on the form.")
    if "receipt_condition" in keys or "receipt_temperature" in keys or "receipt_notes" in keys:
        if "location_id" not in keys:
            raise ValidationError("Receipt fields need the Storage location field on the form too.")
    return cleaned


def validate_scan(scan: ScanSettings, fields: list[FieldSpec]) -> ScanSettings:
    """Scan settings may only fill fields that are on the form and hold a value."""
    usable = {f.key: f for f in fields if f.type != FieldType.HEADING}
    for key in scan.used_keys():
        if key not in usable:
            raise ValidationError("Barcode scanning fills a field that is no longer on the form. Open Barcode "
                                  "scanning… and choose another field.")
    mode = ScanMode(scan.mode)
    if len(scan.gs_substitute) > 1:
        raise ValidationError("The GS substitute is a single character, e.g. ^ or ~.")
    if scan.enabled and mode in (ScanMode.DELIMITED,) and not scan.separator:
        raise ValidationError("Choose the separator between the values in the barcode.")
    return ScanSettings(bool(scan.enabled), mode.value, {ai: k for ai, k in scan.gs1_fields.items() if k},
                        scan.separator or "|", list(scan.positions), scan.single_field, scan.gs_substitute)


def _form_json(fields: list[FieldSpec], scan: ScanSettings) -> str:
    return json.dumps({"fields": [asdict(f) for f in fields], "scan": asdict(scan)}, ensure_ascii=False)


def _parse(row: IntakeFormVersion) -> tuple[list[FieldSpec], ScanSettings]:
    data = json.loads(row.fields)
    if isinstance(data, list):  # versions saved before barcode scanning existed
        return [FieldSpec(**f) for f in data], ScanSettings()
    return [FieldSpec(**f) for f in data["fields"]], ScanSettings(**data.get("scan", {}))


def current_form(session: Session) -> tuple[IntakeFormVersion | None, list[FieldSpec]]:
    """The latest saved form, or the built-in starting form when none has been saved yet."""
    row = session.scalar(select(IntakeFormVersion).order_by(IntakeFormVersion.version.desc()).limit(1))
    if row is None:
        return None, [FieldSpec(**asdict(f)) for f in DEFAULT_FORM]
    return row, _parse(row)[0]


def current_scan(session: Session) -> ScanSettings:
    """How barcodes fill the current form (scanning is off until it is set up)."""
    row = session.scalar(select(IntakeFormVersion).order_by(IntakeFormVersion.version.desc()).limit(1))
    return _parse(row)[1] if row is not None else ScanSettings()


def save_form(session: Session, actor: SessionUser, fields: list[FieldSpec], change_note: str,
              scan: ScanSettings | None = None) -> IntakeFormVersion:
    """Save a new version of the form (and its scan settings; ``None`` keeps the current ones)."""
    require(actor, Permission.MANAGE_MASTER_DATA)
    note = (change_note or "").strip()
    if not note:
        raise ValidationError("Describe what changed in the form.")
    fields = validate_form(fields)
    previous, old_fields = current_form(session)
    old_scan = current_scan(session)
    scan = validate_scan(old_scan if scan is None else scan, fields)
    if [asdict(f) for f in fields] == [asdict(f) for f in old_fields] and asdict(scan) == asdict(old_scan):
        raise ValidationError("Nothing was changed.")
    version = (session.scalar(select(func.max(IntakeFormVersion.version))) or 0) + 1
    row = IntakeFormVersion(version=version, fields=_form_json(fields, scan), change_note=note,
                            created_by_id=actor.id)
    session.add(row)
    session.flush()
    old_labels, new_labels = {f.key: f.label for f in old_fields}, {f.key: f.label for f in fields}
    audit.record(session, AuditAction.INTAKE_FORM_SAVED, actor=actor, entity_type="intake_form",
                 entity_id=f"v{version}", reason=note,
                 old={"version": previous.version if previous else 0},
                 new={"version": version, "fields": len(fields), "barcode_scanning": scan.enabled,
                      "added": [new_labels[k] for k in new_labels if k not in old_labels],
                      "removed": [old_labels[k] for k in old_labels if k not in new_labels]})
    return row


@dataclass
class FormVersionRow:
    version: int
    created_at: datetime
    created_by: str
    change_note: str
    fields: int


def history(session: Session) -> list[FormVersionRow]:
    rows = session.execute(select(IntakeFormVersion, User.full_name)
                           .join(User, User.id == IntakeFormVersion.created_by_id)
                           .order_by(IntakeFormVersion.version.desc()))
    return [FormVersionRow(r.version, r.created_at, who, r.change_note, len(_parse(r)[0])) for r, who in rows]


# ---------------------------------------------------------------------------
# submitting the form
# ---------------------------------------------------------------------------


def _decimal(text) -> Decimal | None:
    try:
        value = Decimal(str(text).strip())
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _display(f: FieldSpec, value, session: Session) -> str:
    """How a value is kept in the intake record (readable on its own)."""
    if _empty(value):
        return ""
    if f.key == "sample_type":
        sample_type = session.get(SampleType, int(value))
        return f"{sample_type.code} – {sample_type.name}" if sample_type else str(value)
    if f.key == "location_id":
        return storage.paths(session).get(int(value), str(value))
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, datetime):
        return value.astimezone().strftime("%Y-%m-%d %H:%M %Z").strip()
    if isinstance(value, date):
        return value.isoformat()
    return str(value).strip()


def check_values(fields: list[FieldSpec], values: dict) -> dict:
    """Check required fields and types; return the cleaned values (headings dropped)."""
    cleaned, missing = {}, []
    for f in fields:
        kind = FieldType(f.type)
        if kind is FieldType.HEADING:
            continue
        value = values.get(f.key)
        if isinstance(value, str):
            value = value.strip()
        if kind is FieldType.CHECKBOX:
            value = bool(value)
            if f.required and not value:
                missing.append(f.label)
        elif _empty(value):
            if f.required:
                missing.append(f.label)
            value = None
        elif kind is FieldType.NUMBER and _decimal(value) is None:
            raise ValidationError(f"{f.label}: '{value}' is not a number.")
        elif kind is FieldType.DROPDOWN and f.choices and value not in f.choices:
            raise ValidationError(f"{f.label}: choose one of the listed options.")
        elif kind is FieldType.DATE and not isinstance(value, date):
            raise ValidationError(f"{f.label}: enter a date.")
        elif kind is FieldType.DATETIME and not (isinstance(value, datetime) and value.tzinfo):
            raise ValidationError(f"{f.label}: enter a date and time.")
        cleaned[f.key] = value
    if missing:
        raise ValidationError("Please fill in: " + ", ".join(missing) + ".")
    return cleaned


def submit(session: Session, actor: SessionUser, values: dict) -> list[Sample]:
    """Register (and, with a storage location, receive) the sample(s) described by the filled-in current form."""
    require(actor, Permission.CREATE_RECORDS)
    form_row, fields = current_form(session)
    clean = check_values(fields, values)
    sample_type_id = int(clean["sample_type"])
    count = 1
    if clean.get("count") is not None:
        number = _decimal(clean["count"])
        if number != number.to_integral_value() or not 1 <= number <= MAX_BATCH:
            raise ValidationError(f"Number of samples must be a whole number from 1 to {MAX_BATCH}.")
        count = int(number)
    details = SampleDetails(**{k: (str(v) if k == "quantity" else v) for k, v in clean.items()
                               if k in DETAIL_FIELDS and v is not None})
    receipt = None
    if clean.get("location_id") is not None:
        receipt = Receipt(location_id=int(clean["location_id"]),
                          condition=clean.get("receipt_condition") or "Acceptable",
                          temperature=clean.get("receipt_temperature"), notes=clean.get("receipt_notes"))
    elif any(clean.get(k) for k in ("receipt_temperature", "receipt_notes")):
        raise ValidationError("Choose the storage location the sample is received into, or leave the receipt "
                              "fields empty.")
    created = samples.register_samples(session, actor, sample_type_id, details, count=count, receipt=receipt)
    entries = [{"key": f.key, "label": f.label, "type": f.type, "value": _display(f, clean.get(f.key), session)}
               for f in fields if f.type != FieldType.HEADING]
    scans = [str(code).replace(GS, "<GS>") for code in values.get("_scans") or [] if str(code).strip()]
    if scans:
        entries.append({"key": "_scan", "label": "Barcode scanned", "type": "text", "value": "  |  ".join(scans)})
    version = form_row.version if form_row else 0
    for sample in created:
        session.add(IntakeSubmission(sample_id=sample.id, form_version_id=form_row.id if form_row else None,
                                     form_version=version, entries=json.dumps(entries, ensure_ascii=False),
                                     submitted_by_id=actor.id))
    custom = {e["label"]: e["value"] for e in entries if e["key"].startswith("c_") and e["value"]}
    audit.record(session, AuditAction.SAMPLE_INTAKE, actor=actor, entity_type="sample",
                 entity_id=", ".join(s.sample_id for s in created),
                 new={"form_version": version, "samples": len(created), "received": receipt is not None,
                      **({"barcodes": scans} if scans else {}),
                      **({"custom_fields": custom} if custom else {})})
    session.flush()
    return created


@dataclass
class IntakeRecord:
    form_version: int
    submitted_at: datetime
    submitted_by: str
    entries: list[tuple[str, str]]  # (label, value) as entered, empty values left out


def intake_record(session: Session, sample_pk: int) -> IntakeRecord | None:
    row = session.execute(select(IntakeSubmission, User.full_name)
                          .join(User, User.id == IntakeSubmission.submitted_by_id)
                          .where(IntakeSubmission.sample_id == sample_pk)).first()
    if row is None:
        return None
    submission, who = row
    entries = [(e["label"], e["value"]) for e in json.loads(submission.entries) if e["value"]]
    return IntakeRecord(submission.form_version, submission.submitted_at, who, entries)
