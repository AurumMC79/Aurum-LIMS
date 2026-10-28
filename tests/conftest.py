import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from aurum_lims.db import Database
from aurum_lims.security.roles import Role
from aurum_lims.services import auth, users

ADMIN_PASSWORD = "Spectroscopy2026"
USER_PASSWORD = "Chemistry2026"


@pytest.fixture
def db(tmp_path):
    database = Database(f"sqlite:///{(tmp_path / 'test.db').as_posix()}")
    database.upgrade()
    yield database
    database.dispose()


@pytest.fixture
def admin(db):
    with db.session() as s:
        return users.create_initial_admin(s, "admin", "Ada Admin", ADMIN_PASSWORD)


@pytest.fixture
def make_user(db, admin):
    """Create a user who has already chosen their own password."""

    def _make(username: str, role: Role = Role.ANALYST, password: str = USER_PASSWORD):
        temp = "Temporary2026x"
        with db.session() as s:
            user = users.create_user(
                s, admin, username=username, full_name=username.title(), role=role, temporary_password=temp
            )
            user_id = user.id
        with db.session() as s:
            auth.change_password(s, user_id, temp, password)
        with db.session() as s:
            result = auth.authenticate(s, username, password)
        assert result.ok
        return result.user

    return _make
