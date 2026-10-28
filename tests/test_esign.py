import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from aurum_lims.errors import PermissionDenied
from aurum_lims.security.roles import Role
from aurum_lims.services import esign
from aurum_lims.services.esign import SignatureMeaning
from tests.conftest import USER_PASSWORD


def test_sign_with_correct_password(db, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        result = esign.sign(s, analyst, USER_PASSWORD, SignatureMeaning.PERFORMED, "sample", "S-0001")
    assert result.ok
    with db.session() as s:
        sigs = esign.signatures_for(s, "sample", "S-0001")
    assert len(sigs) == 1
    assert sigs[0].full_name == "Jsmith"
    assert sigs[0].meaning == "Performed by"


def test_sign_with_wrong_password_fails(db, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        result = esign.sign(s, analyst, "wrong-password-1", SignatureMeaning.PERFORMED, "sample", "S-0001")
    assert not result.ok
    with db.session() as s:
        assert esign.signatures_for(s, "sample", "S-0001") == []


def test_role_controls_signature_meaning(db, make_user):
    analyst = make_user("jsmith")
    viewer = make_user("viewer1", Role.VIEWER)
    reviewer = make_user("qa1", Role.QA_REVIEWER)
    with db.session() as s:
        with pytest.raises(PermissionDenied):
            esign.sign(s, analyst, USER_PASSWORD, SignatureMeaning.APPROVED, "sample", "S-1")
        with pytest.raises(PermissionDenied):
            esign.sign(s, viewer, USER_PASSWORD, SignatureMeaning.PERFORMED, "sample", "S-1")
    with db.session() as s:
        assert esign.sign(s, reviewer, USER_PASSWORD, SignatureMeaning.APPROVED, "sample", "S-1").ok


def test_signatures_are_immutable(db, make_user):
    analyst = make_user("jsmith")
    with db.session() as s:
        esign.sign(s, analyst, USER_PASSWORD, SignatureMeaning.PERFORMED, "sample", "S-0001")
    with pytest.raises(DBAPIError, match="immutable"):
        with db.engine.begin() as conn:
            conn.execute(text("UPDATE electronic_signatures SET meaning = 'Approved by'"))
