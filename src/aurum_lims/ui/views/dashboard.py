"""Home page after signing in."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QGridLayout, QLabel, QVBoxLayout, QWidget

from aurum_lims.db import Database
from aurum_lims.db.models import User
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import ROLE_DESCRIPTIONS, Permission
from aurum_lims.services import auth, users
from aurum_lims.ui.common import Card, format_local, muted_label


def _greeting() -> str:
    hour = datetime.now().hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


class DashboardView(QWidget):
    title = "Dashboard"

    def __init__(self, db: Database, user: SessionUser, parent=None):
        super().__init__(parent)
        self.db = db
        self.user = user
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        self.greeting = QLabel()
        self.greeting.setObjectName("PageTitle")
        layout.addWidget(self.greeting)

        self.grid = QGridLayout()
        self.grid.setSpacing(16)
        layout.addLayout(self.grid)

        self.role_card = Card("Your role")
        self.role_value = QLabel(user.role.label)
        self.role_value.setObjectName("CardValue")
        self.role_card.body.addWidget(self.role_value)
        self.role_card.body.addWidget(muted_label(ROLE_DESCRIPTIONS[user.role]))
        self.grid.addWidget(self.role_card, 0, 0)

        self.account_card = Card("Your account")
        self.account_info = muted_label("")
        self.account_card.body.addWidget(self.account_info)
        self.grid.addWidget(self.account_card, 0, 1)

        self.users_card = None
        if user.can(Permission.MANAGE_USERS):
            self.users_card = Card("User accounts")
            self.users_value = QLabel()
            self.users_value.setObjectName("CardValue")
            self.users_detail = muted_label("")
            self.users_card.body.addWidget(self.users_value)
            self.users_card.body.addWidget(self.users_detail)
            self.grid.addWidget(self.users_card, 0, 2)

        roadmap = Card("Coming next")
        roadmap.body.addWidget(
            muted_label(
                "Phase 2 adds sample registration and chain of custody. Later phases add test methods "
                "and specifications, result entry with out-of-specification flags, review and approval "
                "with electronic signatures, Certificates of Analysis, and instrument and reagent tracking."
            )
        )
        layout.addWidget(roadmap)
        layout.addStretch()

    def refresh(self) -> None:
        self.greeting.setText(f"{_greeting()}, {self.user.full_name.split()[0]}")
        with self.db.session() as session:
            me = session.get(User, self.user.id)
            expires = auth.password_expires_in_days(session, self.user.id)
            last_login = me.last_login_at
            counts = users.count_users(session) if self.users_card else None
        expiry = "Your password does not expire." if expires is None else f"Password expires in {expires} day(s)."
        self.account_info.setText(f"Username: {self.user.username}\nSigned in: {format_local(last_login)}\n{expiry}")
        if counts is not None:
            self.users_value.setText(str(counts["active"]))
            self.users_detail.setText(f"active · {counts['locked']} locked · {counts['inactive']} inactive")


class PlaceholderView(QWidget):
    """Stand-in page for modules that arrive in later phases."""

    def __init__(self, title: str, description: str, parent=None):
        super().__init__(parent)
        self.title = title
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        card = Card(title)
        card.body.addWidget(muted_label(description))
        layout.addWidget(card)
        layout.addStretch()

    def refresh(self) -> None:
        pass
