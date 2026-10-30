"""Roles and the permissions each role grants.

Administrators manage the system but do not review or approve laboratory data
(segregation of duties). A person who does both needs two accounts.

The Master Administrator is an Administrator who can also reload the app and
is the only one who can hand out (or take away) the Master Administrator and
IT Support roles. IT Support maintains the app: reload, bug reports, and
read-only access for troubleshooting.

Second-person review of tests: QA Reviewers, Supervisors and Lead Analysts
(never on a test they worked on). Final sample decisions, OOS closure and
method / specification approval: QA Reviewers only. Managers oversee without
acting on lab data; the Chemical Hygiene Officer looks after the chemical
inventory and safety documents.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from aurum_lims.errors import PermissionDenied

if TYPE_CHECKING:
    from aurum_lims.security.identity import SessionUser


class Role(StrEnum):
    MASTER_ADMINISTRATOR = "master_administrator"
    ADMINISTRATOR = "administrator"
    IT_SUPPORT = "it_support"
    MANAGER = "manager"
    QA_REVIEWER = "qa_reviewer"
    SUPERVISOR = "supervisor"
    LEAD_ANALYST = "lead_analyst"
    ANALYST = "analyst"
    CHEMICAL_HYGIENE_OFFICER = "chemical_hygiene_officer"
    VIEWER = "viewer"

    @property
    def label(self) -> str:
        return ROLE_LABELS[self]


ROLE_LABELS = {
    Role.MASTER_ADMINISTRATOR: "Master Administrator",
    Role.ADMINISTRATOR: "Administrator",
    Role.IT_SUPPORT: "IT Support",
    Role.MANAGER: "Manager",
    Role.QA_REVIEWER: "QA Reviewer",
    Role.SUPERVISOR: "Supervisor",
    Role.LEAD_ANALYST: "Lead Analyst",
    Role.ANALYST: "Analyst",
    Role.CHEMICAL_HYGIENE_OFFICER: "Chemical Hygiene Officer",
    Role.VIEWER: "Viewer",
}

ROLE_DESCRIPTIONS = {
    Role.MASTER_ADMINISTRATOR: "TESTING SET-UP: has every permission of every role (lab work, review, approval and "
                               "administration). The only role that can give or remove the Master Administrator and "
                               "IT Support roles. Restrict before the system is used for real (GxP) work.",
    Role.ADMINISTRATOR: "Manages user accounts, system settings, sample types and storage; can view the audit trail.",
    Role.IT_SUPPORT: "Maintains the app: reloads it, handles bug reports, and has read-only access to records and "
                     "the audit trail for troubleshooting.",
    Role.MANAGER: "Oversees the laboratory: sees everything including the review queues, exports the audit trail, "
                  "and manages sample types, storage, methods, specifications and SOPs; does not enter, review or "
                  "approve lab data.",
    Role.QA_REVIEWER: "Reviews and approves laboratory records, manages sample types and storage; can view the audit trail.",
    Role.SUPERVISOR: "Does everything an Analyst does, peer-reviews colleagues' tests, and can view the audit trail.",
    Role.LEAD_ANALYST: "Does everything an Analyst does and peer-reviews colleagues' tests.",
    Role.ANALYST: "Registers, receives and handles samples, and signs their own work.",
    Role.CHEMICAL_HYGIENE_OFFICER: "Manages the chemical inventory (receive, open, count, discard) and safety documents "
                                   "in the SOP index; read-only access to everything else.",
    Role.VIEWER: "Read-only access to laboratory records.",
}

# Roles only a Master Administrator may give or take away.
PRIVILEGED_ROLES = frozenset({Role.MASTER_ADMINISTRATOR, Role.IT_SUPPORT})


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
    # Upload SOPs / forms to the SOP index; see superseded revisions; handle bug reports.
    MANAGE_DOCUMENTS = "manage_documents"
    MANAGE_BUG_REPORTS = "manage_bug_reports"
    VIEW_AUDIT_TRAIL = "view_audit_trail"
    EXPORT_AUDIT_TRAIL = "export_audit_trail"
    # Restart the app in place to load a new version.
    RELOAD_APP = "reload_app"
    # Give or remove the Master Administrator and IT Support roles.
    MANAGE_PRIVILEGED_ROLES = "manage_privileged_roles"
    # Reagent / standard / consumable catalogue and lots (receive, open, count, discard).
    MANAGE_INVENTORY = "manage_inventory"
    # See what is waiting for review and decision without being able to act on it.
    VIEW_REVIEW_QUEUE = "view_review_queue"


_ADMINISTRATOR = frozenset(
    {
        Permission.VIEW_RECORDS,
        Permission.MANAGE_USERS,
        Permission.MANAGE_SETTINGS,
        Permission.MANAGE_MASTER_DATA,
        Permission.MANAGE_DOCUMENTS,
        Permission.MANAGE_BUG_REPORTS,
        Permission.VIEW_AUDIT_TRAIL,
        Permission.EXPORT_AUDIT_TRAIL,
    }
)

_ANALYST = frozenset(
    {
        Permission.VIEW_RECORDS,
        Permission.CREATE_RECORDS,
        Permission.EDIT_RECORDS,
        Permission.SIGN_RECORDS,
    }
)

# Testing set-up requested by the laboratory: the Master Administrator can do everything, so one person can try
# every function. Set to False before validated use; the Master Administrator then has the administrator
# permissions plus reload and privileged-role management only (segregation of duties). The IQ flags it while True.
MASTER_ADMIN_HAS_ALL_PERMISSIONS = True

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.MASTER_ADMINISTRATOR: frozenset(Permission) if MASTER_ADMIN_HAS_ALL_PERMISSIONS else
    _ADMINISTRATOR | {Permission.RELOAD_APP, Permission.MANAGE_PRIVILEGED_ROLES},
    Role.ADMINISTRATOR: _ADMINISTRATOR,
    Role.IT_SUPPORT: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.MANAGE_BUG_REPORTS,
            Permission.VIEW_AUDIT_TRAIL,
            Permission.RELOAD_APP,
        }
    ),
    Role.QA_REVIEWER: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.SIGN_RECORDS,
            Permission.REVIEW_RECORDS,
            Permission.APPROVE_RECORDS,
            Permission.MANAGE_MASTER_DATA,
            Permission.MANAGE_DOCUMENTS,
            Permission.VIEW_AUDIT_TRAIL,
            Permission.EXPORT_AUDIT_TRAIL,
        }
    ),
    Role.MANAGER: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.VIEW_REVIEW_QUEUE,
            Permission.MANAGE_MASTER_DATA,
            Permission.MANAGE_DOCUMENTS,
            Permission.MANAGE_BUG_REPORTS,
            Permission.VIEW_AUDIT_TRAIL,
            Permission.EXPORT_AUDIT_TRAIL,
        }
    ),
    Role.SUPERVISOR: _ANALYST | {Permission.REVIEW_RECORDS, Permission.VIEW_AUDIT_TRAIL},
    Role.LEAD_ANALYST: _ANALYST | {Permission.REVIEW_RECORDS},
    Role.ANALYST: _ANALYST,
    Role.CHEMICAL_HYGIENE_OFFICER: frozenset(
        {
            Permission.VIEW_RECORDS,
            Permission.MANAGE_INVENTORY,
            Permission.MANAGE_DOCUMENTS,
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


def require_any(user: SessionUser, *permissions: Permission) -> None:
    """Raise ``PermissionDenied`` unless ``user`` holds at least one of ``permissions``."""
    if not any(has_permission(user.role, p) for p in permissions):
        raise PermissionDenied(f"Your role ({Role(user.role).label}) does not allow this action.")
