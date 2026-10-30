"""Master Administrator and IT Support roles."""

import pytest
from sqlalchemy import select

from aurum_lims.db.models import AuditEntry, User
from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.security.roles import MASTER_ADMIN_HAS_ALL_PERMISSIONS, Permission, Role, has_permission
from aurum_lims.services import users
from aurum_lims.services.audit import AuditAction


def test_role_permissions():
    master, admin, it = Role.MASTER_ADMINISTRATOR, Role.ADMINISTRATOR, Role.IT_SUPPORT
    assert has_permission(master, Permission.RELOAD_APP) and has_permission(it, Permission.RELOAD_APP)
    assert not has_permission(admin, Permission.RELOAD_APP)
    # A Master Administrator can do everything an Administrator can. In the current testing set-up it has every
    # permission; an ordinary Administrator still cannot touch lab data (segregation of duties).
    for permission in Permission:
        if has_permission(admin, permission):
            assert has_permission(master, permission)
    assert all(has_permission(master, p) for p in Permission) == MASTER_ADMIN_HAS_ALL_PERMISSIONS
    for permission in (Permission.CREATE_RECORDS, Permission.EDIT_RECORDS, Permission.REVIEW_RECORDS,
                       Permission.APPROVE_RECORDS):
        assert not has_permission(admin, permission)
    # IT Support maintains the app; it does not manage users or touch lab data.
    assert has_permission(it, Permission.MANAGE_BUG_REPORTS) and has_permission(it, Permission.VIEW_AUDIT_TRAIL)
    for permission in (Permission.MANAGE_USERS, Permission.CREATE_RECORDS, Permission.EDIT_RECORDS,
                       Permission.APPROVE_RECORDS, Permission.MANAGE_SETTINGS):
        assert not has_permission(it, permission)


def test_first_run_account_is_master_administrator(db, admin):
    assert admin.role is Role.MASTER_ADMINISTRATOR


def test_only_master_admin_grants_privileged_roles(db, admin, make_user):
    plain_admin = make_user("plain.admin", Role.ADMINISTRATOR)
    with db.session() as s:
        for role in (Role.MASTER_ADMINISTRATOR, Role.IT_SUPPORT):
            with pytest.raises(PermissionDenied, match="Master Administrator"):
                users.create_user(s, plain_admin, username=f"x.{role.value}", full_name="X", role=role,
                                  temporary_password="Temporary2026x")
        # An ordinary Administrator can still create ordinary accounts.
        users.create_user(s, plain_admin, username="new.analyst", full_name="New Analyst", role=Role.ANALYST,
                          temporary_password="Temporary2026x")
    with db.session() as s:
        it_user = users.create_user(s, admin, username="it.person", full_name="IT Person", role=Role.IT_SUPPORT,
                                    temporary_password="Temporary2026x")
        it_id = it_user.id
    analyst = make_user("someone")
    with db.session() as s:
        # A plain Administrator cannot change, reset or deactivate an IT Support / Master account...
        with pytest.raises(PermissionDenied):
            users.update_user(s, plain_admin, it_id, role=Role.ANALYST, reason="x")
        with pytest.raises(PermissionDenied):
            users.reset_password(s, plain_admin, admin.id, "Takeover2026xx", reason="x")
        with pytest.raises(PermissionDenied):
            users.deactivate_user(s, plain_admin, it_id, reason="x")
        # ...nor promote an ordinary account to one.
        with pytest.raises(PermissionDenied):
            users.update_user(s, plain_admin, analyst.id, role=Role.MASTER_ADMINISTRATOR, reason="x")


def test_last_master_administrator_is_protected(db, admin, make_user):
    second_admin = make_user("second.admin", Role.ADMINISTRATOR)  # an Administrator still remains
    with db.session() as s:
        with pytest.raises(ValidationError, match="Master Administrator must remain"):
            users.update_user(s, admin, admin.id, role=Role.ADMINISTRATOR, reason="x")
    with db.session() as s:
        second_master = users.create_user(s, admin, username="master2", full_name="Second Master",
                                          role=Role.MASTER_ADMINISTRATOR, temporary_password="Temporary2026x")
        second_id = second_master.id
    with db.session() as s:
        users.update_user(s, admin, second_id, role=Role.ADMINISTRATOR, reason="Not needed")  # allowed: one remains
    assert second_admin.role is Role.ADMINISTRATOR


def test_existing_database_promotes_first_administrator(db, admin, make_user):
    """A database created before the role existed: the first-run Administrator becomes Master."""
    later_admin = make_user("later.admin", Role.ADMINISTRATOR)
    with db.session() as s:
        s.get(User, admin.id).role = Role.ADMINISTRATOR  # as it was before this change
    with db.session() as s:
        promoted = users.ensure_master_administrator(s)
        assert promoted.id == admin.id
    with db.session() as s:
        assert s.get(User, admin.id).role == Role.MASTER_ADMINISTRATOR
        assert s.get(User, later_admin.id).role == Role.ADMINISTRATOR
        entry = s.scalars(select(AuditEntry).where(AuditEntry.action == AuditAction.USER_UPDATED)
                          .order_by(AuditEntry.id.desc())).first()
        assert "master_administrator" in entry.new_values and "first-run" in entry.reason
        assert entry.username == "system"
    with db.session() as s:
        assert users.ensure_master_administrator(s) is None  # only once
