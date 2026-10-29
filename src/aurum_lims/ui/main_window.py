"""Main application window: sidebar navigation, header, and pages."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from aurum_lims import config
from aurum_lims.db import Database
from aurum_lims.security.identity import SessionUser
from aurum_lims.security.roles import Permission
from aurum_lims.services import auth
from aurum_lims.services.settings import get_security_settings
from aurum_lims.ui.common import app_icon, logo_pixmap
from aurum_lims.ui.lock_screen import IdleWatcher, LockDialog
from aurum_lims.ui.views.account import AccountView
from aurum_lims.ui.views.audit import AuditView
from aurum_lims.ui.views.certificates import CertificatesView
from aurum_lims.ui.views.dashboard import DashboardView
from aurum_lims.ui.views.instruments import InstrumentsView
from aurum_lims.ui.views.inventory import InventoryView
from aurum_lims.ui.views.review import ReviewView
from aurum_lims.ui.views.methods import MethodsView
from aurum_lims.ui.views.oos import OOSView
from aurum_lims.ui.views.specifications import SpecificationsView
from aurum_lims.ui.views.testing import TestingView
from aurum_lims.ui.views.sample_types import SampleTypesView
from aurum_lims.ui.views.samples import SamplesView
from aurum_lims.ui.views.settings import SettingsView
from aurum_lims.ui.views.storage import StorageView
from aurum_lims.ui.views.users import UsersView


class MainWindow(QMainWindow):
    # (trigger, exit_application)
    signed_out = Signal(str, bool)

    def __init__(self, db: Database, user: SessionUser):
        super().__init__()
        self.db = db
        self.user = user
        self._signing_out = False
        self.setWindowTitle(f"{config.APP_NAME} — {user.full_name}")
        self.setWindowIcon(app_icon())
        self.resize(1200, 760)
        self.setMinimumSize(QSize(960, 600))

        self.pages: list[QWidget] = []
        self.stack = QStackedWidget()
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFocusPolicy(Qt.NoFocus)

        self._add_page(DashboardView(db, user))
        self._add_page(SamplesView(db, user))
        self._add_page(TestingView(db, user))
        if user.can(Permission.REVIEW_RECORDS):
            self._add_page(ReviewView(db, user))
        self._add_page(OOSView(db, user))
        self._add_page(CertificatesView(db, user))
        self._add_page(InstrumentsView(db, user))
        self._add_page(InventoryView(db, user))
        self._add_page(StorageView(db, user))
        if user.can(Permission.MANAGE_MASTER_DATA):
            self._add_page(MethodsView(db, user))
            self._add_page(SpecificationsView(db, user))
            self._add_page(SampleTypesView(db, user))
        if user.can(Permission.MANAGE_USERS):
            self._add_page(UsersView(db, user))
        if user.can(Permission.VIEW_AUDIT_TRAIL):
            self._add_page(AuditView(db, user))
        if user.can(Permission.MANAGE_SETTINGS):
            settings_page = SettingsView(db, user)
            settings_page.settings_changed.connect(self._apply_settings)
            self._add_page(settings_page)
        self._add_page(AccountView(db, user))

        self.page_title = QLabel()
        self.page_title.setObjectName("PageTitle")

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_sidebar())
        content = QWidget()
        content.setObjectName("Content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(0)
        content_layout.addWidget(self._build_header())
        content_layout.addWidget(self.stack, 1)
        root.addWidget(content, 1)
        self.setCentralWidget(central)

        self.nav.currentRowChanged.connect(self._show_page)
        self.nav.setCurrentRow(0)

        with db.session() as session:
            timeout = get_security_settings(session).session_timeout_minutes
        self.idle = IdleWatcher(QApplication.instance(), timeout, self)
        self.idle.idle.connect(lambda: self._lock("inactivity"))

    # ----- layout -----
    def _add_page(self, page: QWidget) -> None:
        self.pages.append(page)
        self.stack.addWidget(page)
        QListWidgetItem(page.title, self.nav)

    def _build_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(220)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 18, 0, 16)
        layout.setSpacing(4)

        brand = QHBoxLayout()
        brand.setContentsMargins(16, 0, 16, 0)
        logo = QLabel()
        logo.setPixmap(logo_pixmap(34))
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel(config.APP_NAME)
        title.setObjectName("SidebarTitle")
        subtitle = QLabel(f"v{config.APP_VERSION}")
        subtitle.setObjectName("SidebarSubtitle")
        names.addWidget(title)
        names.addWidget(subtitle)
        brand.addWidget(logo)
        brand.addLayout(names)
        brand.addStretch()
        layout.addLayout(brand)
        layout.addSpacing(18)
        layout.addWidget(self.nav, 1)

        who = QVBoxLayout()
        who.setContentsMargins(16, 0, 16, 0)
        who.setSpacing(0)
        name = QLabel(self.user.full_name)
        name.setObjectName("SidebarUser")
        role = QLabel(self.user.role.label)
        role.setObjectName("SidebarRole")
        who.addWidget(name)
        who.addWidget(role)
        layout.addLayout(who)
        return sidebar

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("Header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(24, 12, 16, 12)
        layout.addWidget(self.page_title)
        layout.addStretch()
        lock = QPushButton("Lock")
        lock.setToolTip("Lock the screen now (e.g. when stepping away from the bench)")
        lock.clicked.connect(lambda *_: self._lock("locked by user"))
        sign_out = QPushButton("Sign out")
        sign_out.clicked.connect(lambda *_: self.sign_out("user"))
        layout.addWidget(lock)
        layout.addWidget(sign_out)
        return header

    def _show_page(self, index: int) -> None:
        if index < 0:
            return
        page = self.pages[index]
        self.stack.setCurrentWidget(page)
        self.page_title.setText(page.title)
        page.refresh()

    def show_page(self, title: str) -> bool:
        for i, page in enumerate(self.pages):
            if page.title == title:
                self.nav.setCurrentRow(i)
                return True
        return False

    # ----- session -----
    def _apply_settings(self) -> None:
        with self.db.session() as session:
            self.idle.set_timeout(get_security_settings(session).session_timeout_minutes)

    def _lock(self, trigger: str) -> None:
        self.idle.pause()
        with self.db.session() as session:
            auth.record_session_locked(session, self.user, trigger)
        dialog = LockDialog(self.db, self.user, self)
        if dialog.exec() == QDialog.Accepted:
            self.idle.resume()
            current = self.stack.currentWidget()
            if current is not None:
                current.refresh()
        else:
            self.sign_out("account locked" if dialog.locked_out else "signed out from lock screen")

    def sign_out(self, trigger: str, exit_app: bool = False) -> None:
        if self._signing_out:
            return
        self._signing_out = True
        self.idle.stop()
        self.signed_out.emit(trigger, exit_app)

    def closeEvent(self, event) -> None:
        if self._signing_out:
            event.accept()
            return
        answer = QMessageBox.question(self, config.APP_NAME, "Sign out and close Aurum LIMS?")
        if answer == QMessageBox.Yes:
            event.accept()
            self.sign_out("application closed", exit_app=True)
        else:
            event.ignore()
