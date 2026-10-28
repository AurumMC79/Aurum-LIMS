import pytest

from aurum_lims.errors import PermissionDenied, ValidationError
from aurum_lims.services import audit
from aurum_lims.services.audit import AuditAction
from aurum_lims.services.settings import get_security_settings, update_security_settings


def test_defaults(db):
    with db.session() as s:
        settings = get_security_settings(s)
    assert settings.max_failed_logins == 5
    assert settings.session_timeout_minutes == 15


def test_update_is_audited(db, admin):
    with db.session() as s:
        update_security_settings(s, admin, {"session_timeout_minutes": 10, "max_failed_logins": 5}, "SOP-IT-004 rev 2")
    with db.session() as s:
        assert get_security_settings(s).session_timeout_minutes == 10
        entries = audit.query_entries(s, admin, action=AuditAction.SETTING_CHANGED)
    assert len(entries) == 1  # unchanged values are not logged
    assert entries[0].reason == "SOP-IT-004 rev 2"


def test_update_validation(db, admin, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        with pytest.raises(ValidationError, match="reason"):
            update_security_settings(s, admin, {"session_timeout_minutes": 10}, "")
        with pytest.raises(ValidationError, match="between"):
            update_security_settings(s, admin, {"max_failed_logins": 100}, "test")
        with pytest.raises(PermissionDenied):
            update_security_settings(s, analyst, {"max_failed_logins": 4}, "test")
