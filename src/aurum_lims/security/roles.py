"""Roles and the permissions each role grants.

Administrators manage the system but do not review or approve laboratory data
(segregation of duties). A person who does both needs two accounts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from aurum_lims.errors import PermissionDenied

if TYPE_CHECKING:
    from aurum_lims.security.identity import SessionUser


class Role(StrEnum):
    ADMINISTRATOR = "administrator"
    QA_REVIEWER = "qa_reviewer"
    ANALYST = "analyst"
    VIEWER = "viewer"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.ADMINISTRATOR: "Administrator",
    Role.QA_REVIEWER: "QA Reviewer",
    Role.ANALYST: "Analyst",
    Role.VIEWER: "Viewer",
}

ROLE_DESCRIPTIONS = {
    Role.ADMINISTRATOR: "Manages user accounts, system settings, sample types and storage; can view the audit trail.",
    Role.QA_REVIEWER: "Reviews and approves laboratory records, manages sample types and storage; can view the audit trail.",
    Role.ANALYST: "Registers, receives and handles samples, and signs their own work.",
    Role.VIEWER: "Read-only access to laboratory records.",
}


class Permission(StrEnum):
    VIEW_RECORDS = "view_records"
    CREATE_RECORDS = "create_records"
    EDIT_RECORDS = "edit_records"
    SIGN_RECORDS = "sign_records"
    REVIEW_RECORDS = "review_records"
    APPROVE_RECORDS = "approve_records"
    MANAGE_USERS = "manage_users"
    MANAGE_SETTINGS = "manage_settings"
    # Sample types, storage locations and the sample ID format.
    MANAGE_MASTER_DATA = "manage_master_data"
    VIEW_AUDIT_TRAIL = "view_audit_trail"
    EXPORT_AUDIT_TRAIL = "export_audit_trail"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMINISTRATOR: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.MANAGE_USERS,
            Permission.MANAGE_SETTINGS,
            Permission.MANAGE_MASTER_DATA,
            Permission.VIEW_AUDIT_TRAIL,
            Permission.EXPORT_AUDIT_TRAIL,
        }
    ),
    Role.QA_REVIEWER: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.SIGN_RECORDS,
            Permission.REVIEW_RECORDS,
            Permission.APPROVE_RECORDS,
            Permission.MANAGE_MASTER_DATA,
            Permission.VIEW_AUDIT_TRAIL,
            Permission.EXPORT_AUDIT_TRAIL,
        }
    ),
    Role.ANALYST: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.CREATE_RECORDS,
            Permission.EDIT_RECORDS,
            Permission.SIGN_RECORDS,
        }
    ),
    Role.VIEWER: frozenset({Permission.VIEW_RECORDS}),
}


def has_permission(role: Role | str, permission: Permission) -> bool:
    return permission in ROLE_PERMISSIONS[Role(role)]


def require(user: SessionUser, permission: Permission) -> None:
    """Raise ``PermissionDenied`` unless ``user`` holds ``permission``."""
    if not has_permission(user.role, permission):
        raise PermissionDenied(f"Your role ({Role(user.role).label}) does not allow this action.")
