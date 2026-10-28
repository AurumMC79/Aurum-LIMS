"""Database tables.

Schema changes are made through Alembic migrations in ``aurum_lims/migrations``;
whenever a model here changes, a new migration must be added alongside it.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    """Stores datetimes as UTC and always hands back timezone-aware values."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not allowed; use timezone-aware UTC values.")
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {datetime: UTCDateTime}


class User(Base):
    """A person who can sign in. Users are never deleted, only deactivated,
    so every record they created stays attributable to them."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    password_changed_at: Mapped[datetime]
    last_login_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class PasswordHistory(Base):
    """Previous password hashes, used to stop users re-using recent passwords."""

    __tablename__ = "password_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditEntry(Base):
    """One line of the audit trail.

    Rows are append-only: database triggers reject UPDATE and DELETE, and each
    row carries a SHA-256 hash of its contents chained to the previous row, so
    any change made outside the application is detectable.
    """

    __tablename__ = "audit_entries"
    __table_args__ = {"sqlite_autoincrement": True}

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored as fixed-format ISO-8601 text so the hashed value is exactly what is stored.
    timestamp_utc: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    old_values: Mapped[str | None] = mapped_column(Text)
    new_values: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64))


class ElectronicSignature(Base):
    """A 21 CFR Part 11 style electronic signature: who signed, when, and what
    the signature means. Rows are immutable (enforced by triggers)."""

    __tablename__ = "electronic_signatures"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))
    full_name: Mapped[str] = mapped_column(String(128))
    meaning: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str | None] = mapped_column(Text)
    signed_at: Mapped[datetime] = mapped_column(default=utcnow)
    audit_entry_id: Mapped[int] = mapped_column(ForeignKey("audit_entries.id"))


class AppSetting(Base):
    """Key/value store for settings administrators can change in the app."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


# ---------------------------------------------------------------------------
# Phase 2: samples and chain of custody
# ---------------------------------------------------------------------------


class SampleType(Base):
    """A kind of sample (Raw Material, Finished Product, Stability...). The
    category decides which descriptive fields the sample form asks for. A type
    may have its own sample ID format; otherwise the lab-wide format is used."""

    __tablename__ = "sample_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    code: Mapped[str] = mapped_column(String(16), unique=True)
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str | None] = mapped_column(Text)
    id_format: Mapped[str | None] = mapped_column(String(80))
    id_reset: Mapped[str | None] = mapped_column(String(16))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class StorageLocation(Base):
    """A place samples are kept. Locations nest: Lab 1 > Fridge A > Shelf 2."""

    __tablename__ = "storage_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(32))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    condition: Mapped[str | None] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Sample(Base):
    """A physical sample (or aliquot) tracked from registration to disposal."""

    __tablename__ = "samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_id: Mapped[str] = mapped_column(String(64), unique=True)
    sample_type_id: Mapped[int] = mapped_column(ForeignKey("sample_types.id"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("samples.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)

    material_name: Mapped[str | None] = mapped_column(String(200))
    lot_number: Mapped[str | None] = mapped_column(String(100))
    supplier: Mapped[str | None] = mapped_column(String(200))
    sampling_point: Mapped[str | None] = mapped_column(String(200))
    stability_study: Mapped[str | None] = mapped_column(String(100))
    stability_timepoint: Mapped[str | None] = mapped_column(String(50))
    stability_condition: Mapped[str | None] = mapped_column(String(100))
    # Quantity is kept exactly as entered (text) so the original value is preserved.
    quantity: Mapped[str | None] = mapped_column(String(32))
    quantity_unit: Mapped[str | None] = mapped_column(String(16))
    container: Mapped[str | None] = mapped_column(String(100))
    priority: Mapped[str] = mapped_column(String(16), default="Normal")
    due_date: Mapped[date | None]
    sampled_at: Mapped[datetime | None]
    sampled_by: Mapped[str | None] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(Text)

    received_at: Mapped[datetime | None]
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    receipt_condition: Mapped[str | None] = mapped_column(String(64))
    receipt_temperature: Mapped[str | None] = mapped_column(String(32))

    location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"), index=True)
    holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    disposed_at: Mapped[datetime | None]
    disposed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    disposal_method: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Phase 3: the specification whose limits the results are compared with.
    specification_id: Mapped[int | None] = mapped_column(ForeignKey("specifications.id"))


class CustodyEvent(Base):
    """One step in a sample's chain of custody. Immutable (enforced by triggers).

    ``occurred_at`` is when it physically happened (entered by the user);
    ``recorded_at`` is when it was entered into the system."""

    __tablename__ = "custody_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    event: Mapped[str] = mapped_column(String(32))
    occurred_at: Mapped[datetime]
    recorded_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    username: Mapped[str] = mapped_column(String(64))
    from_location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    to_location_id: Mapped[int | None] = mapped_column(ForeignKey("storage_locations.id"))
    from_holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    to_holder_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    details: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class IdCounter(Base):
    """Running numbers for generated IDs, per counter scope and reset period."""

    __tablename__ = "id_counters"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    period: Mapped[str] = mapped_column(String(16), primary_key=True)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------------
# Phase 3: methods, specifications, tests, results, OOS
# ---------------------------------------------------------------------------


class TestMethod(Base):
    """One version of an analytical method. Versions are edited while Draft,
    locked once Active, and replaced by activating a newer version."""

    __tablename__ = "test_methods"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_test_methods_code"),)
    __test__ = False  # not a pytest test class

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(150))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    sop_reference: Mapped[str | None] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime | None]
    activated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    retired_at: Mapped[datetime | None]


class MethodParameter(Base):
    """Something entered or calculated in a method: an input (weight, peak
    area) or a result (assay %, pH, appearance)."""

    __tablename__ = "method_parameters"
    __table_args__ = (UniqueConstraint("method_id", "key", name="uq_method_parameters_method_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    method_id: Mapped[int] = mapped_column(ForeignKey("test_methods.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    key: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(16))
    result_type: Mapped[str] = mapped_column(String(16))
    unit: Mapped[str | None] = mapped_column(String(20))
    decimals: Mapped[int | None] = mapped_column(Integer)
    replicates: Mapped[int] = mapped_column(Integer, default=1)
    formula: Mapped[str | None] = mapped_column(Text)


class Specification(Base):
    """One version of a specification: the limits a material must meet."""

    __tablename__ = "specifications"
    __table_args__ = (UniqueConstraint("code", "version", name="uq_specifications_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(150))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))
    material_name: Mapped[str | None] = mapped_column(String(200))
    sample_type_id: Mapped[int | None] = mapped_column(ForeignKey("sample_types.id"))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    activated_at: Mapped[datetime | None]
    activated_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    retired_at: Mapped[datetime | None]


class SpecLimit(Base):
    """A limit for one result parameter of a method (referenced by method code
    and parameter key, so it applies to every version of the method)."""

    __tablename__ = "spec_limits"
    __table_args__ = (
        UniqueConstraint("spec_id", "method_code", "parameter_key", name="uq_spec_limits_spec_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    spec_id: Mapped[int] = mapped_column(ForeignKey("specifications.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    method_code: Mapped[str] = mapped_column(String(40))
    parameter_key: Mapped[str] = mapped_column(String(40))
    limit_type: Mapped[str] = mapped_column(String(16))
    low: Mapped[str | None] = mapped_column(String(32))
    high: Mapped[str | None] = mapped_column(String(32))
    expected_text: Mapped[str | None] = mapped_column(String(100))


class Analysis(Base):
    """A test (one method version) to be performed on one sample."""

    __tablename__ = "analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    method_id: Mapped[int] = mapped_column(ForeignKey("test_methods.id"))
    status: Mapped[str] = mapped_column(String(16), index=True)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    created_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    started_at: Mapped[datetime | None]
    completed_at: Mapped[datetime | None]
    completed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signature_id: Mapped[int | None] = mapped_column(ForeignKey("electronic_signatures.id"))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    # Phase 4: each re-sign after a review return creates a new revision of the results.
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    reviewed_at: Mapped[datetime | None]
    reviewed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    review_signature_id: Mapped[int | None] = mapped_column(ForeignKey("electronic_signatures.id"))
    review_comment: Mapped[str | None] = mapped_column(Text)


class ResultValue(Base):
    """One entered value: a parameter's replicate, exactly as typed."""

    __tablename__ = "result_values"
    __table_args__ = (
        UniqueConstraint("analysis_id", "parameter_key", "replicate", name="uq_result_values_analysis_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), index=True)
    parameter_key: Mapped[str] = mapped_column(String(40))
    replicate: Mapped[int] = mapped_column(Integer)
    value: Mapped[str] = mapped_column(String(64))
    entered_at: Mapped[datetime] = mapped_column(default=utcnow)
    entered_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))


class ReportableResult(Base):
    """A final result, frozen when the analyst signs the analysis as complete.
    Immutable (enforced by triggers)."""

    __tablename__ = "reportable_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"), index=True)
    parameter_key: Mapped[str] = mapped_column(String(40))
    parameter_name: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str | None] = mapped_column(String(20))
    replicate_values: Mapped[str | None] = mapped_column(Text)
    mean: Mapped[str | None] = mapped_column(String(64))
    rsd: Mapped[str | None] = mapped_column(String(32))
    reported_value: Mapped[str] = mapped_column(String(64))
    spec_text: Mapped[str | None] = mapped_column(String(120))
    conformance: Mapped[str] = mapped_column(String(16))
    comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")


class OOSInvestigation(Base):
    """An out-of-specification result and its investigation."""

    __tablename__ = "oos_investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    analysis_id: Mapped[int] = mapped_column(ForeignKey("analyses.id"))
    parameter_key: Mapped[str] = mapped_column(String(40))
    parameter_name: Mapped[str] = mapped_column(String(100))
    result_value: Mapped[str] = mapped_column(String(64))
    spec_text: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), index=True)
    phase: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    investigation_notes: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    conclusion: Mapped[str | None] = mapped_column(String(64))
    opened_at: Mapped[datetime] = mapped_column(default=utcnow)
    opened_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None]
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    signature_id: Mapped[int | None] = mapped_column(ForeignKey("electronic_signatures.id"))


# ---------------------------------------------------------------------------
# Phase 4: sample decisions and certificates
# ---------------------------------------------------------------------------


class SampleDecision(Base):
    """QA's final decision on a sample (Approved / Rejected). Immutable."""

    __tablename__ = "sample_decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    decision: Mapped[str] = mapped_column(String(16))
    conforms: Mapped[bool] = mapped_column(Boolean)
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    signature_id: Mapped[int] = mapped_column(ForeignKey("electronic_signatures.id"))


class Certificate(Base):
    """An issued Certificate of Analysis or Test Report, stored as the exact PDF
    that was issued, with its SHA-256 fingerprint. Immutable."""

    __tablename__ = "certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(32), unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    sample_id: Mapped[int] = mapped_column(ForeignKey("samples.id"), index=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("sample_decisions.id"))
    issued_at: Mapped[datetime] = mapped_column(default=utcnow)
    issued_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    pdf: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
