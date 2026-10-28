"""Small UI building blocks shared by the screens."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QDateTime, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDateTimeEdit,
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from aurum_lims.errors import AurumError

THEME_PATH = Path(__file__).resolve().parent / "theme.qss"
GOLD = QColor("#9a7b1c")


def load_stylesheet() -> str:
    try:
        return THEME_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""


def logo_pixmap(size: int = 64) -> QPixmap:
    """The 'Au' tile used as the app icon and on the sign-in screen."""
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(GOLD)
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.22, size * 0.22)
    painter.setPen(QColor("#ffffff"))
    font = QFont("Segoe UI", 1, QFont.DemiBold)
    font.setPixelSize(int(size * 0.46))
    painter.setFont(font)
    painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, "Au")
    painter.end()
    return pixmap


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(logo_pixmap(size))
    return icon


def primary_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("primary", True)
    button.setDefault(True)
    return button


def danger_button(text: str) -> QPushButton:
    button = QPushButton(text)
    button.setProperty("danger", True)
    return button


def password_field(placeholder: str = "") -> QLineEdit:
    field = QLineEdit()
    field.setEchoMode(QLineEdit.Password)
    field.setPlaceholderText(placeholder)
    return field


def error_label() -> QLabel:
    label = QLabel()
    label.setObjectName("ErrorLabel")
    label.setWordWrap(True)
    label.hide()
    return label


def show_label_message(label: QLabel, message: str) -> None:
    label.setText(message)
    label.setVisible(bool(message))


def muted_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


class Card(QFrame):
    """White rounded panel with an optional title."""

    def __init__(self, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(8)
        if title:
            heading = QLabel(title)
            heading.setObjectName("CardTitle")
            self.body.addWidget(heading)


def ask_reason(parent: QWidget, title: str, prompt: str) -> str | None:
    """Ask for the reason for a change (required for regulated records).
    Returns ``None`` if the user cancels."""
    while True:
        text, ok = QInputDialog.getMultiLineText(parent, title, prompt + "\n\nReason for change (required):")
        if not ok:
            return None
        if text.strip():
            return text.strip()
        QMessageBox.warning(parent, title, "A reason is required.")


def show_error(parent: QWidget, error: Exception | str, title: str = "Not allowed") -> None:
    message = str(error) if isinstance(error, (AurumError, str)) else f"Unexpected error: {error}"
    QMessageBox.warning(parent, title, message)


def now_edit() -> QDateTimeEdit:
    """Date/time picker defaulting to now, in local time."""
    edit = QDateTimeEdit(QDateTime.currentDateTime())
    edit.setCalendarPopup(True)
    edit.setDisplayFormat("yyyy-MM-dd HH:mm")
    edit.setMaximumDateTime(QDateTime.currentDateTime().addDays(1))
    return edit


def edit_to_utc(edit: QDateTimeEdit) -> datetime:
    """The picked local date/time as a timezone-aware value."""
    return edit.dateTime().toPython().astimezone()


def fill_location_combo(combo: QComboBox, location_paths: list[tuple[int, str]], selected: int | None = None) -> None:
    combo.clear()
    combo.addItem("— choose a location —", None)
    for location_id, path in location_paths:
        combo.addItem(path, location_id)
        if location_id == selected:
            combo.setCurrentIndex(combo.count() - 1)


def open_pdf(name: str, data: bytes) -> Path:
    """Write a PDF to a temporary folder and open it in the default PDF viewer."""
    import tempfile

    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices

    folder = Path(tempfile.gettempdir()) / "AurumLIMS"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.pdf"
    path.write_bytes(data)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    return path


def format_local(moment: datetime | str | None, with_seconds: bool = False) -> str:
    """Show a stored UTC time in the computer's local time zone."""
    if moment is None:
        return "—"
    if isinstance(moment, str):
        moment = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    return moment.astimezone().strftime(fmt)
