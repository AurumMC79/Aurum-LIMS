"""Sidebar navigation: pages grouped under a few headings, each opening a flyout menu.

Hovering over a heading opens its menu to the right of the sidebar; moving
away closes it again. Clicking a heading keeps the menu open until a page is
chosen, the user clicks elsewhere or presses Esc. A heading with a single page
(Dashboard) opens that page directly. The heading of the current page is
highlighted and shows the page's name underneath.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

# Heading → page titles, in order. Pages the user's role does not have are simply left out.
NAV_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("Dashboard", ("Dashboard",)),
    ("Lab work", ("Sample Intaking", "Samples", "Testing", "Review", "OOS", "Certificates")),
    ("Equipment & materials", ("Instruments", "Inventory", "Storage")),
    ("Quality & reports", ("Reports", "SOP Index", "Audit Trail")),
    ("Setup", ("Methods", "Specifications", "Sample Types", "Users")),
    ("Settings & account", ("Settings", "My Account")),
]
OTHER = "More"
OPEN_DELAY_MS, CLOSE_DELAY_MS = 120, 350


class _HeadingButton(QPushButton):
    """A sidebar heading; shows › when it opens a menu."""

    hovered = Signal()
    left = Signal()

    def __init__(self, text: str, has_menu: bool, parent=None):
        super().__init__(text.replace("&", "&&"), parent)  # a single & would be read as a keyboard shortcut
        self.setObjectName("NavGroup")
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.NoFocus)
        self.arrow = QLabel("›", self) if has_menu else None
        if self.arrow is not None:
            self.arrow.setObjectName("NavArrow")
            self.arrow.setAttribute(Qt.WA_TransparentForMouseEvents)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.arrow is not None:
            self.arrow.adjustSize()
            self.arrow.move(self.width() - self.arrow.width() - 14, (self.height() - self.arrow.height()) // 2)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.hovered.emit()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.left.emit()


class FlyoutMenu(QFrame):
    """The pages of one heading, shown beside the sidebar (a child of the window, above the content)."""

    chosen = Signal(int)
    entered = Signal()
    left = Signal()

    def __init__(self, host: QWidget):
        super().__init__(host)
        self.setObjectName("Flyout")
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(6, 8, 6, 8)
        self.layout_.setSpacing(2)
        self.group: str | None = None
        self.buttons: list[QPushButton] = []
        self.hide()

    def show_items(self, group: str, items: list[tuple[int, str]], current: int, anchor: QWidget) -> None:
        # Hide first: widgets added to an already visible frame stay hidden (BUG-0005, empty menu when moving
        # from one heading to another); showing the frame again below shows the new entries.
        self.hide()
        while self.layout_.count():
            widget = self.layout_.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        title = QLabel(group)
        title.setObjectName("FlyoutTitle")
        self.layout_.addWidget(title)
        self.buttons = []
        for index, text in items:
            button = QPushButton(text.replace("&", "&&"))
            button.setObjectName("FlyoutItem")
            button.setCheckable(True)
            button.setChecked(index == current)
            button.setCursor(Qt.PointingHandCursor)
            button.setFocusPolicy(Qt.NoFocus)
            button.clicked.connect(lambda *_, i=index: self.chosen.emit(i))
            self.layout_.addWidget(button)
            self.buttons.append(button)
        self.group = group
        self.setMinimumWidth(210)
        self.show()
        self.raise_()
        # Size and place it once shown, so the entries' styling (padding, fonts) is counted.
        for button in self.buttons:
            button.ensurePolished()
        self.adjustSize()
        host = self.parentWidget()
        top_left = anchor.mapTo(host, QPoint(anchor.width(), 0))
        y = max(4, min(top_left.y() - 8, host.height() - self.height() - 4))
        self.move(top_left.x(), y)

    def enterEvent(self, event) -> None:
        super().enterEvent(event)
        self.entered.emit()

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        self.left.emit()


class NavigationPanel(QWidget):
    """The headings in the sidebar plus their flyout menu."""

    page_chosen = Signal(int)

    def __init__(self, titles: list[str], host: QWidget, parent=None):
        super().__init__(parent)
        self.titles = titles
        self.current = 0
        self.groups: list[tuple[str, list[tuple[int, str]]]] = []
        placed = set()
        for name, members in NAV_GROUPS:
            items = [(i, t) for t in members for i, title in enumerate(titles) if title == t]
            placed.update(i for i, _ in items)
            if items:
                self.groups.append((name, items))
        leftover = [(i, t) for i, t in enumerate(titles) if i not in placed]
        if leftover:
            self.groups.append((OTHER, leftover))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        self.headings: dict[str, _HeadingButton] = {}
        self.current_labels: dict[str, QLabel] = {}
        for name, items in self.groups:
            direct = len(items) == 1
            button = _HeadingButton(items[0][1] if direct else name, not direct)
            button.clicked.connect(lambda *_, g=name: self._clicked(g))
            button.hovered.connect(lambda g=name: self._hovered(g))
            button.left.connect(self._schedule_close)
            label = QLabel("")
            label.setObjectName("NavCurrent")
            label.hide()
            layout.addWidget(button)
            layout.addWidget(label)
            self.headings[name] = button
            self.current_labels[name] = label
        layout.addStretch()

        self.flyout = FlyoutMenu(host)
        self.flyout.chosen.connect(self._choose)
        self.flyout.entered.connect(self._cancel_close)
        self.flyout.left.connect(self._schedule_close)
        self.pinned = False
        self.pending_group: str | None = None
        self.open_timer = QTimer(self, singleShot=True, interval=OPEN_DELAY_MS)
        self.open_timer.timeout.connect(lambda: self.pending_group and self.open_group(self.pending_group))
        self.close_timer = QTimer(self, singleShot=True, interval=CLOSE_DELAY_MS)
        self.close_timer.timeout.connect(self._close_if_away)
        self.watching = False  # clicks / Esc are watched only while a menu is open
        self.flyout.destroyed.connect(lambda *_: self._stop_watching())

    # ----- groups -----
    def group_of(self, index: int) -> str | None:
        return next((name for name, items in self.groups if any(i == index for i, _ in items)), None)

    def items_of(self, group: str) -> list[tuple[int, str]]:
        return next(items for name, items in self.groups if name == group)

    def set_current(self, index: int) -> None:
        """Highlight the heading of the page now shown."""
        self.current = index
        active = self.group_of(index)
        for name, button in self.headings.items():
            button.setChecked(name == active)
            label = self.current_labels[name]
            show = name == active and len(self.items_of(name)) > 1
            label.setText(self.titles[index] if show else "")
            label.setVisible(show)

    def open_group(self, group: str) -> None:
        items = self.items_of(group)
        if len(items) == 1:
            return
        self.flyout.show_items(group, items, self.current, self.headings[group])
        if not self.watching:
            QApplication.instance().installEventFilter(self)
            self.watching = True

    def close_flyout(self) -> None:
        self.open_timer.stop()
        self.close_timer.stop()
        self.pinned = False
        self._stop_watching()
        self.flyout.hide()
        self.flyout.group = None

    def _stop_watching(self) -> None:
        # The window may be closing: either object can already be gone.
        if self.watching and isValid(self) and QApplication.instance() is not None:
            QApplication.instance().removeEventFilter(self)
        self.watching = False

    # ----- mouse -----
    def _clicked(self, group: str) -> None:
        items = self.items_of(group)
        if len(items) == 1:
            self._choose(items[0][0])
            return
        self.open_timer.stop()
        if self.flyout.isVisible() and self.flyout.group == group and self.pinned:
            self.close_flyout()
            self.set_current(self.current)
            return
        self.open_group(group)
        self.pinned = True
        self.set_current(self.current)  # clicking a checkable button toggles it; restore the highlight

    def _hovered(self, group: str) -> None:
        self._cancel_close()
        if len(self.items_of(group)) == 1:
            if not self.pinned:
                self._schedule_close()
            return
        if self.flyout.isVisible() and self.flyout.group != group:
            self.pinned = False
            self.open_group(group)  # moving between headings switches straight away
            return
        self.pending_group = group
        self.open_timer.start()

    def _schedule_close(self) -> None:
        self.open_timer.stop()
        if self.flyout.isVisible() and not self.pinned:
            self.close_timer.start()

    def _cancel_close(self) -> None:
        self.close_timer.stop()

    def _close_if_away(self) -> None:
        if self.pinned or not self.flyout.isVisible():
            return
        position = QCursor.pos()
        over_flyout = QRect(self.flyout.mapToGlobal(QPoint(0, 0)), self.flyout.size()).contains(position)
        heading = self.headings.get(self.flyout.group or "")
        over_heading = heading is not None and QRect(heading.mapToGlobal(QPoint(0, 0)),
                                                     heading.size()).contains(position)
        if not over_flyout and not over_heading:
            self.close_flyout()

    def _choose(self, index: int) -> None:
        self.close_flyout()
        self.set_current(index)
        self.page_chosen.emit(index)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if not isValid(self.flyout):
            self._stop_watching()
            return False
        if self.watching and self.flyout.isVisible():
            # A real click reaches the window (a QWindow) first and then the widget under the pointer. Only the
            # widget tells us where the click is; closing on the window's copy would hide the menu before its
            # button could be clicked.
            if event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
                inside = (watched is self.flyout or self.flyout.isAncestorOf(watched) or
                          any(b is watched or b.isAncestorOf(watched) for b in self.headings.values()))
                if not inside:
                    self.close_flyout()
            elif event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
                self.close_flyout()
                return True
        return False
