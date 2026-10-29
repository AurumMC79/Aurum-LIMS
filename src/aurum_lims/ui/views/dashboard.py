"""Home page after signing in."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtWidgets import QGridLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from aurum_lims.db import Database
from aurum_lims.db.models import User
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import ROLE_DESCRIPTIONS, Permission
from aurum_lims.services import auth, instruments, inventory, oos, review, samples, testing, users
from aurum_lims.services.samples import ON_HAND_STATUSES, SampleStatus
from aurum_lims.services.testing import AnalysisStatus
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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget()
        content.setObjectName("Content")
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QVBoxLayout(content)
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

        self.sample_cards = {}
        for column, (key, title, help_text) in enumerate([
            ("awaiting", "Awaiting receipt", "Registered but not yet received"),
            ("on_hand", "Samples in the lab", "Received and not yet disposed"),
            ("due", "Overdue", "Open samples past their due date"),
        ]):
            card = Card(title)
            value = QLabel("0")
            value.setObjectName("CardValue")
            detail = muted_label(help_text)
            card.body.addWidget(value)
            card.body.addWidget(detail)
            self.sample_cards[key] = (value, detail)
            self.grid.addWidget(card, 1, column)
        for column, (key, title, help_text) in enumerate([
            ("pending", "Tests not started", "Assigned and waiting for results"),
            ("in_progress", "Tests in progress", "Results being entered"),
            ("oos", "Open OOS investigations", "Results outside specification under investigation"),
        ]):
            card = Card(title)
            value = QLabel("0")
            value.setObjectName("CardValue")
            card.body.addWidget(value)
            card.body.addWidget(muted_label(help_text))
            self.sample_cards[key] = (value, None)
            self.grid.addWidget(card, 2, column)
        self.reviewer = user.can(Permission.REVIEW_RECORDS)
        if self.reviewer:
            for column, (key, title, help_text) in enumerate([
                ("to_review", "Tests awaiting review", "Signed by the analyst, waiting for second-person review"),
                ("to_decide", "Samples awaiting decision", "All tests reviewed; ready to approve or reject"),
            ]):
                card = Card(title)
                value = QLabel("0")
                value.setObjectName("CardValue")
                card.body.addWidget(value)
                card.body.addWidget(muted_label(help_text))
                self.sample_cards[key] = (value, None)
                self.grid.addWidget(card, 3, column)

        for column, (key, title, help_text) in enumerate([
            ("instruments", "Instruments needing attention", ""),
            ("lots", "Lots expired or expiring", ""),
            ("low_stock", "Items low on stock", "Usable stock below the minimum"),
        ]):
            card = Card(title)
            value = QLabel("0")
            value.setObjectName("CardValue")
            detail = muted_label(help_text)
            card.body.addWidget(value)
            card.body.addWidget(detail)
            self.sample_cards[key] = (value, detail)
            self.grid.addWidget(card, 4, column)
        layout.addStretch()

    def _attention(self, key: str, count: int, detail: str) -> None:
        value, detail_label = self.sample_cards[key]
        value.setText(str(count))
        value.setStyleSheet("color:#b42318" if count else "")
        detail_label.setText(detail)

    def refresh(self) -> None:
        self.greeting.setText(f"{_greeting()}, {self.user.full_name.split()[0]}")
        with self.db.session() as session:
            me = session.get(User, self.user.id)
            expires = auth.password_expires_in_days(session, self.user.id)
            last_login = me.last_login_at
            counts = users.count_users(session) if self.users_card else None
            statuses = samples.status_counts(session)
            overdue, due_soon = samples.due_counts(session)
            tests = testing.open_counts(session)
            open_oos = oos.open_count(session)
            late_instruments, soon_instruments = instruments.due_soon(session)
            expired_lots, expiring_lots = inventory.expiring(session)
            low = inventory.low_stock(session)
            if self.reviewer:
                self.sample_cards["to_review"][0].setText(str(len(review.tests_awaiting_review(session, self.user))))
                self.sample_cards["to_decide"][0].setText(
                    str(len(review.samples_awaiting_decision(session, self.user))))
        self._attention("instruments", len(late_instruments),
                        f"{len(late_instruments)} overdue or out of service · {len(soon_instruments)} due in 14 days"
                        + (": " + ", ".join(i.code for i in late_instruments[:4]) if late_instruments else ""))
        self._attention("lots", len(expired_lots),
                        f"{len(expired_lots)} expired · {len(expiring_lots)} expire within 30 days")
        self._attention("low_stock", len(low), ", ".join(item.name for item, _ in low[:3]) or
                        "Usable stock below the minimum")
        self.sample_cards["pending"][0].setText(str(tests.get(AnalysisStatus.PENDING, 0)))
        self.sample_cards["in_progress"][0].setText(str(tests.get(AnalysisStatus.IN_PROGRESS, 0)))
        self.sample_cards["oos"][0].setText(str(open_oos))
        self.sample_cards["oos"][0].setStyleSheet("color:#b42318" if open_oos else "")
        self.sample_cards["awaiting"][0].setText(str(statuses.get(SampleStatus.REGISTERED, 0)))
        self.sample_cards["on_hand"][0].setText(str(sum(statuses.get(s, 0) for s in ON_HAND_STATUSES)))
        self.sample_cards["due"][0].setText(str(overdue))
        self.sample_cards["due"][1].setText(f"Open samples past their due date · {due_soon} due in the next 7 days")
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
