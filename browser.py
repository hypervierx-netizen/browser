#!/usr/bin/env python3
"""A minimal, island-styled web browser. Tabs, search bar, start page."""
import json
import os
import re
import sys
import uuid
import time
from pathlib import Path

CONFIG_FILE = Path.home() / ".local/share/browser/config.json"

# sites see prefers-color-scheme: dark and serve their native dark theme
# (0 = dark); must be set before Qt WebEngine starts
os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = (
    os.environ.get("QTWEBENGINE_CHROMIUM_FLAGS", "")
    + " --blink-settings=preferredColorScheme=0")

from PyQt6.QtCore import (
    QSize, Qt, QElapsedTimer, QEvent, QObject, QProcess, QStringListModel,
    QTimer, QUrl, QUrlQuery, pyqtSignal, pyqtSlot,
)
from PyQt6.QtWebChannel import QWebChannel
from PyQt6.QtGui import (
    QColor, QDesktopServices, QIcon, QKeySequence, QPainter, QPixmap,
    QShortcut, QGuiApplication,
)
from PyQt6.QtWidgets import (
    QApplication, QCompleter, QInputDialog, QLabel, QMainWindow, QMenu,
    QProgressBar, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLineEdit,
    QTabWidget, QTabBar, QToolButton,
)
from PyQt6.QtWebEngineCore import (
    QWebEnginePermission, QWebEngineProfile, QWebEnginePage, QWebEngineScript,
    QWebEngineSettings,
)
from PyQt6.QtNetwork import (
    QLocalServer, QLocalSocket, QNetworkAccessManager, QNetworkRequest,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

APP_DIR = Path(__file__).resolve().parent
# version query defeats the renderer's cache of local pages, so a new
# tab always shows the current start.html, not a stale cached copy
START_PAGE = QUrl.fromLocalFile(str(APP_DIR / "start.html"))
START_PAGE.setQuery("v=%d" % (APP_DIR / "start.html").stat().st_mtime)
SEARCH_URL = "https://www.google.com/search?q={}"
SUGGEST_URL = "https://suggestqueries.google.com/complete/search"
DOWNLOAD_DIR = Path.home() / "Downloads"
HOSTS_FILE = Path.home() / ".local/share/browser/hosts.json"
HISTORY_FILE = Path.home() / ".local/share/browser/history.json"
HISTORY_PAGE = QUrl.fromLocalFile(str(APP_DIR / "history.html"))
HISTORY_PAGE.setQuery("v=%d" % (APP_DIR / "history.html").stat().st_mtime)
HISTORY_MAX = 3000

# sites that ship their own dark theme (served via preferredColorScheme):
# force-dark would only slow them down repainting an already-dark page
NATIVE_DARK_SITES = {
    "github.com", "youtube.com", "reddit.com", "twitch.tv", "discord.com",
    "netflix.com", "spotify.com", "tiktok.com", "instagram.com",
    "modrinth.com", "duckduckgo.com",
}

# Google's native dark theme is gray (#202124); repaint it true black
# so search pages match the rest of the UI
GOOGLE_BLACK_JS = r"""
(function () {
  if (!/(^|\.)google\.[a-z.]+$/.test(location.hostname)) return;
  var s = document.createElement("style");
  s.textContent =
    "html, body { background: #000 !important; }" +
    ".sfbg, .minidiv, #searchform, #appbar, #sfcnt, #footcnt, #fbar," +
    " #footer, .appbar { background: #000 !important; }";
  (document.head || document.documentElement).appendChild(s);
})();
"""

# what a site may ask for, in words the permission bar can show
PERMISSION_LABELS = {
    QWebEnginePermission.PermissionType.MediaAudioCapture:
        "use your microphone",
    QWebEnginePermission.PermissionType.MediaVideoCapture:
        "use your camera",
    QWebEnginePermission.PermissionType.MediaAudioVideoCapture:
        "use your microphone and camera",
    QWebEnginePermission.PermissionType.DesktopVideoCapture:
        "share your screen",
    QWebEnginePermission.PermissionType.DesktopAudioVideoCapture:
        "share your screen with audio",
    QWebEnginePermission.PermissionType.Notifications:
        "show notifications",
}

# sentinel: "new tab inherits the current tab's group"
INHERIT_GROUP = object()

# palette offered when creating a tab group
GROUP_COLORS = [
    ("Blue", "#89b4fa"), ("Pink", "#f38ba8"), ("Green", "#a6e3a1"),
    ("Yellow", "#f9e2af"), ("Purple", "#cba6f7"), ("Teal", "#94e2d5"),
    ("Orange", "#fab387"), ("Gray", "#6c7086"),
]

# domain guesses for the address bar ("wiki" -> wikipedia.org);
# visited sites are remembered and suggested too
COMMON_SITES = [
    "wikipedia.org", "youtube.com", "github.com", "google.com",
    "reddit.com", "amazon.de", "ebay.de", "netflix.com", "spotify.com",
    "twitch.tv", "instagram.com", "tiktok.com", "discord.com",
    "translate.google.com", "maps.google.com", "web.de", "gmx.net",
]

STYLE = """
* { font-family: "JetBrainsMono Nerd Font", "Inter", sans-serif; font-size: 13px; }
QMainWindow, #chrome { background: #000000; }

QLineEdit#urlbar {
    background: rgba(13, 13, 18, 230);
    color: #cdd6f4;
    border: 1px solid rgba(108, 112, 134, 70);
    border-radius: 0px;
    padding: 7px 16px;
    selection-background-color: #45475a;
    selection-color: #ffffff;
}
QLineEdit#urlbar:focus { border: 1px solid #a6adc8; }

QToolButton {
    background: rgba(13, 13, 18, 230);
    color: #cdd6f4;
    border: none;
    border-radius: 12px;
    padding: 5px 11px;
    font-weight: bold;
}
QToolButton:hover { background: #16161d; color: #ffffff; }

QTabWidget::pane { border: none; }
QTabBar { background: transparent; }
QTabBar::tab {
    background: rgba(13, 13, 18, 200);
    color: #a6adc8;
    border-radius: 0px;
    padding: 7px 6px 7px 14px;
    margin: 4px 3px 6px 3px;
}
QTabBar::tab:selected {
    background: #16161d;
    color: #cdd6f4;
    border: 1px solid rgba(108, 112, 134, 90);
}
QTabBar::tab:hover { color: #cdd6f4; }

#dlbar { background: #000000; border-top: 1px solid rgba(108, 112, 134, 50); }
#dlitem { background: rgba(13, 13, 18, 230); border-radius: 12px; }
QLabel#dlname { color: #cdd6f4; }
QLabel#dlinfo { color: #6c7086; font-size: 11px; }
QProgressBar {
    background: #16161d;
    border: none;
    border-radius: 3px;
    max-height: 6px;
}
QProgressBar::chunk { background: #89b4fa; border-radius: 3px; }

QToolButton#tabclose {
    background: rgba(108, 112, 134, 60);
    color: #cdd6f4;
    min-width: 18px; max-width: 18px;
    min-height: 18px; max-height: 18px;
    border-radius: 9px;
    padding: 0px;
    font-size: 12px;
    font-weight: normal;
}
QToolButton#tabclose:hover { background: rgba(243, 139, 168, 70); color: #f38ba8; }

#toast { background: #0d0d12; border: 1px solid rgba(108, 112, 134, 110); }
#toast QLabel { color: #cdd6f4; }

QMenu {
    background: #0d0d12;
    color: #cdd6f4;
    border: 1px solid rgba(108, 112, 134, 110);
    padding: 4px;
}
QMenu::item { padding: 6px 18px; }
QMenu::item:selected { background: #16161d; color: #ffffff; }
QMenu::separator { height: 1px; background: rgba(108, 112, 134, 70); margin: 4px 8px; }

QToolButton#groupbtn {
    font-size: 15px;
    padding: 5px 12px;
    margin: 4px 0 6px 6px;
    border-radius: 0px;
}
QToolButton#newtabbtn {
    padding: 0px;
    margin: 0px;
    border-radius: 0px;
    font-size: 15px;
}
"""


class GroupMenu(QMenu):
    """The book-button menu; right-clicking a group offers to delete it."""

    def __init__(self, browser):
        super().__init__(browser)
        self.browser = browser

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            action = self.actionAt(event.position().toPoint())
            group = action.data() if action else None
            if group:
                sub = QMenu(self)
                delete = sub.addAction("Delete \u201c%s\u201d" % group)
                chosen = sub.exec(event.globalPosition().toPoint())
                if chosen is delete:
                    self.browser.delete_group(group)
                self.close()
                return
        super().mouseReleaseEvent(event)


class GroupTabBar(QTabBar):
    """Chrome-style painting: group headers as colored pills, group
    members with a colored underline."""

    def __init__(self, browser):
        super().__init__()
        self.browser = browser

    def _tabs(self):
        return getattr(self.browser, "tabs", None)

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        tabs = self._tabs()
        w = tabs.widget(index) if tabs else None
        if w is not None and getattr(w, "group_header", None) is not None:
            width = self.fontMetrics().horizontalAdvance(w.group_header) + 30
            return QSize(max(width, 44), size.height())
        if tabs is None:
            return QSize(min(max(size.width(), 160), 240), size.height())
        # tabs share the bar width and shrink as more open, like Chrome
        members = 0
        pills = 0
        for i in range(self.count()):
            if not self.isTabVisible(i):
                continue
            wi = tabs.widget(i)
            if wi is None:
                continue
            if getattr(wi, "group_header", None) is not None:
                pills += (self.fontMetrics().horizontalAdvance(wi.group_header)
                          + 30 + 6)
            else:
                members += 1
        available = self.width() - pills - 46  # room for the + button
        share = available // max(1, members) - 6  # per-tab margins
        return QSize(max(34, min(240, share)), size.height())

    def tabLayoutChange(self):
        super().tabLayoutChange()
        if getattr(self.browser, "tabs", None):
            update = getattr(self.browser, "_update_close_buttons", None)
            if update is not None:
                update()
            place = getattr(self.browser, "_place_newtab", None)
            if place is not None:
                place()

    def paintEvent(self, event):
        super().paintEvent(event)
        tabs = self._tabs()
        if tabs is None:
            return
        painter = QPainter(self)
        for i in range(self.count()):
            if not self.isTabVisible(i):
                continue
            w = tabs.widget(i)
            if w is None:
                continue
            rect = self.tabRect(i)
            header = getattr(w, "group_header", None)
            if header is not None:
                color = QColor(self.browser.group_colors.get(header, "#6c7086"))
                pill = rect.adjusted(3, 8, -3, -10)
                painter.fillRect(pill, color)
                painter.setPen(QColor("#000000"))
                painter.drawText(pill, Qt.AlignmentFlag.AlignCenter, header)
            else:
                group = getattr(w, "group", None)
                if group is not None:
                    color = QColor(self.browser.group_colors.get(group, "#6c7086"))
                    painter.fillRect(rect.x() + 2, rect.bottom() - 2,
                                     rect.width() - 4, 3, color)
        painter.end()


class TabWidget(QTabWidget):
    def __init__(self, browser):
        super().__init__()
        self.setTabBar(GroupTabBar(browser))


class Bridge(QObject):
    """Exposed to the start/history pages via QWebChannel."""

    updateFinished = pyqtSignal(str)

    def __init__(self, browser):
        super().__init__()
        self.browser = browser
        self._updating = None

    @pyqtSlot()
    def runUpdate(self):
        """Pull the newest version from GitHub (async; result via signal)."""
        if self._updating is not None:
            return
        proc = QProcess(self)
        self._updating = proc
        proc.setWorkingDirectory(str(APP_DIR))
        proc.finished.connect(lambda *_: self._update_done(proc))
        proc.errorOccurred.connect(lambda *_: self._update_done(proc))
        proc.start("git", ["pull", "--ff-only"])

    def _update_done(self, proc):
        if self._updating is not proc:
            return
        self._updating = None
        out = bytes(proc.readAllStandardOutput()).decode(errors="replace")
        err = bytes(proc.readAllStandardError()).decode(errors="replace")
        proc.deleteLater()
        if proc.exitStatus() != QProcess.ExitStatus.NormalExit or proc.error() == QProcess.ProcessError.FailedToStart:
            msg = "Update needs git and a cloned copy of the repo."
        elif proc.exitCode() != 0:
            msg = "Update failed: " + (err.strip().splitlines() or ["unknown error"])[-1]
        elif "Already up to date" in out:
            msg = "You have the newest version \u2713"
        else:
            msg = "Updated! Restart the browser to finish."
        self.updateFinished.emit(msg)

    @pyqtSlot(result=bool)
    def historyEnabled(self):
        return self.browser.config.get("history", True)

    @pyqtSlot(bool)
    def setHistoryEnabled(self, enabled):
        self.browser.config["history"] = enabled
        self.browser.save_config()

    @pyqtSlot(result=str)
    def getStartData(self):
        """Start-page setup shared across all cookie jars."""
        return json.dumps(self.browser.config.get("startPage", {}))

    @pyqtSlot(str)
    def setStartData(self, data):
        try:
            self.browser.config["startPage"] = json.loads(data)
        except ValueError:
            return
        self.browser.save_config()

    @pyqtSlot(result=str)
    def getHistory(self):
        return json.dumps(self.browser.history)

    @pyqtSlot()
    def clearHistory(self):
        self.browser.history = []
        self.browser.save_history()


class WebView(QWebEngineView):
    def __init__(self, browser, profile):
        super().__init__()
        self.browser = browser
        self.attach_profile(profile)

    def attach_profile(self, profile):
        old = self.page()
        page = QWebEnginePage(profile, self)
        channel = QWebChannel(page)
        channel.registerObject("bridge", self.browser.bridge)
        page.setWebChannel(channel)
        page.fullScreenRequested.connect(self._fullscreen)
        page.permissionRequested.connect(self.browser._permission_requested)
        self.setPage(page)
        if old is not None and old is not page:
            try:
                old.deleteLater()
            except RuntimeError:
                pass  # Qt already disposed of the replaced page

    def createWindow(self, wtype):
        # tab for a link opened by a page (ctrl+click, middle-click,
        # target=_blank); the engine loads the URL itself, so don't load
        # the start page. Ctrl/middle-click = background tab, like Chrome.
        background = (wtype ==
                      QWebEnginePage.WebWindowType.WebBrowserBackgroundTab)
        return self.browser.new_tab(switch=not background, blank=True)

    def _fullscreen(self, request):
        request.accept()
        self.browser.set_fullscreen(request.toggleOn())


def fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024


def fmt_time(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


class DownloadWidget(QWidget):
    """One entry in the download bar: name, progress, speed, time left."""

    def __init__(self, request, on_dismiss):
        super().__init__(objectName="dlitem")
        self.req = request
        self.on_dismiss = on_dismiss
        self.clock = QElapsedTimer()
        self.clock.start()
        self.last_bytes = 0
        self.last_ms = 0
        self.speed = 0.0

        self.setFixedWidth(360)
        name = request.downloadFileName()
        self.name = QLabel(objectName="dlname")
        self.name.setText(self.fontMetrics().elidedText(
            name, Qt.TextElideMode.ElideMiddle, 230))
        self.name.setToolTip(name)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.info = QLabel("Starting…", objectName="dlinfo")

        self.open_btn = QToolButton(text="Open")
        self.open_btn.hide()
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(
            QUrl.fromLocalFile(self.req.downloadDirectory())))
        self.close_btn = QToolButton(text="✕")
        self.close_btn.clicked.connect(self._cancel_or_dismiss)

        grid = QGridLayout(self)
        grid.setContentsMargins(12, 8, 8, 8)
        grid.setVerticalSpacing(4)
        grid.addWidget(self.name, 0, 0)
        grid.addWidget(self.open_btn, 0, 1)
        grid.addWidget(self.close_btn, 0, 2)
        grid.addWidget(self.bar, 1, 0, 1, 3)
        grid.addWidget(self.info, 2, 0, 1, 3)

        request.receivedBytesChanged.connect(self._progress)
        request.totalBytesChanged.connect(self._progress)
        request.stateChanged.connect(self._state_changed)

    def _progress(self):
        if self.req.state() != self.req.DownloadState.DownloadInProgress:
            return
        received, total = self.req.receivedBytes(), self.req.totalBytes()
        ms = self.clock.elapsed()
        if ms - self.last_ms >= 300:
            instant = (received - self.last_bytes) / ((ms - self.last_ms) / 1000)
            self.speed = instant if not self.speed else 0.7 * self.speed + 0.3 * instant
            self.last_bytes, self.last_ms = received, ms
        parts = []
        if total > 0:
            self.bar.setRange(0, 1000)
            self.bar.setValue(round(received / total * 1000))
            parts.append(f"{fmt_size(received)} / {fmt_size(total)}")
        else:
            self.bar.setRange(0, 0)  # size unknown: busy animation
            parts.append(fmt_size(received))
        if self.speed > 0:
            parts.append(f"{fmt_size(self.speed)}/s")
            if total > 0:
                parts.append(f"{fmt_time((total - received) / self.speed)} left")
        self.info.setText(" · ".join(parts))

    def _state_changed(self, state):
        St = self.req.DownloadState
        if state == St.DownloadCompleted:
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000)
            self.info.setText(f"Done · {fmt_size(self.req.receivedBytes())}")
            self.open_btn.show()
        elif state == St.DownloadCancelled:
            self.bar.setRange(0, 1000)
            self.info.setText("Cancelled")
        elif state == St.DownloadInterrupted:
            self.bar.setRange(0, 1000)
            self.info.setText("Failed: " + self.req.interruptReasonString())

    def _cancel_or_dismiss(self):
        if self.req.state() == self.req.DownloadState.DownloadInProgress:
            self.req.cancel()
        else:
            self.on_dismiss(self)


class Browser(QMainWindow):
    def __init__(self, initial_url=None):
        super().__init__()
        self._initial_url = initial_url
        self.setWindowTitle("browser")
        self.resize(1280, 820)

        try:
            self.config = json.loads(CONFIG_FILE.read_text())
        except Exception:
            self.config = {}
        try:
            self.history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            self.history = []
        self.bridge = Bridge(self)

        self.profile = self._make_profile("browser")
        self._perm_queue = []
        self._perm_widget = None
        self._session_perms = {}

        # top island bar: nav buttons + url bar
        self.urlbar = QLineEdit(objectName="urlbar")
        self.urlbar.setPlaceholderText("Search or enter address")
        self.urlbar.returnPressed.connect(self._navigate)

        # suggestions dropdown: domain guesses + Google search suggestions
        try:
            self.known_hosts = set(json.loads(HOSTS_FILE.read_text()))
        except Exception:
            self.known_hosts = set()
        self.suggest_model = QStringListModel(self)
        self.completer = QCompleter(self.suggest_model, self)
        self.completer.setCompletionMode(
            QCompleter.CompletionMode.UnfilteredPopupCompletion)
        self.urlbar.setCompleter(self.completer)
        self.completer.activated.connect(
            lambda _: QTimer.singleShot(0, self._navigate))
        self.completer.popup().setStyleSheet("""
            QListView {
                background: #0d0d12; color: #cdd6f4;
                border: 1px solid rgba(108, 112, 134, 110);
                border-radius: 10px; padding: 4px; outline: 0;
            }
            QListView::item { padding: 6px 10px; border-radius: 7px; }
            QListView::item:selected { background: #16161d; color: #ffffff; }
        """)
        self._nam = QNetworkAccessManager(self)
        self._suggest_reply = None
        self._suggest_timer = QTimer(self)
        self._suggest_timer.setSingleShot(True)
        self._suggest_timer.setInterval(150)
        self._suggest_timer.timeout.connect(self._fetch_suggestions)
        self.urlbar.textEdited.connect(lambda _t: self._suggest_timer.start())

        back = QToolButton(text="‹")
        fwd = QToolButton(text="›")
        reload_ = QToolButton(text="⟳")
        back.clicked.connect(lambda: self.current().back())
        fwd.clicked.connect(lambda: self.current().forward())
        reload_.clicked.connect(lambda: self.current().reload())

        bar = QHBoxLayout()
        bar.setContentsMargins(10, 8, 10, 2)
        bar.setSpacing(6)
        for w in (back, fwd, reload_):
            bar.addWidget(w)
        bar.addWidget(self.urlbar, 1)

        self.tabs = TabWidget(self)
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        self.tabs.tabBar().tabMoved.connect(self._tab_moved)
        self.tabs.setElideMode(Qt.TextElideMode.ElideRight)
        self.tabs.currentChanged.connect(self._tab_changed)

        # Chrome-style tab groups: a colored name label sits in the tab
        # strip before its tabs; clicking it collapses/expands the group
        self.groups = []
        self.group_colors = {}
        self.collapsed = {}
        self.group_ids = {}
        self.group_profiles = {}
        self.group_sessions = {}
        self.sessions = [{"name": "Browser 1", "sid": "main"}]
        self.active_session = "main"
        self.session_profiles = {}
        self._book = QToolButton(text="\uf02d", objectName="groupbtn")
        self._book.setToolTip("Tab groups")
        self._book.clicked.connect(self._group_menu)
        self.tabs.setCornerWidget(self._book, Qt.Corner.TopLeftCorner)
        # the + rides along right after the last tab, like Chrome
        self._newtab_btn = QToolButton(self.tabs.tabBar(), text="+",
                                       objectName="newtabbtn")
        self._newtab_btn.setToolTip("New tab")
        self._newtab_btn.setFixedSize(28, 26)
        self._newtab_btn.clicked.connect(lambda: self.new_tab())
        self._newtab_btn.show()
        self.tabs.tabBar().installEventFilter(self)

        self.chrome = QWidget(objectName="chrome")
        lay = QVBoxLayout(self.chrome)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # virtual browsers: each entry up here is a full browser with
        # its own cookies and its own tabs
        self.sessrow = QWidget(objectName="sessrow")
        self.sesslay = QHBoxLayout(self.sessrow)
        self.sesslay.setContentsMargins(8, 4, 8, 0)
        self.sesslay.setSpacing(6)
        lay.addWidget(self.sessrow)
        lay.addLayout(bar)

        # download bar (hidden until a download starts)
        self.dlbar = QWidget(objectName="dlbar")
        self.dllay = QHBoxLayout(self.dlbar)
        self.dllay.setContentsMargins(10, 8, 10, 8)
        self.dllay.setSpacing(8)
        self.dllay.addStretch()
        self.dlbar.hide()

        root = QWidget()
        rlay = QVBoxLayout(root)
        rlay.setContentsMargins(0, 0, 0, 0)
        rlay.setSpacing(0)
        rlay.addWidget(self.chrome)
        rlay.addWidget(self.tabs, 1)
        rlay.addWidget(self.dlbar)
        self.setCentralWidget(root)

        for key, fn in {
            "Ctrl+T": self.new_tab,
            "Ctrl+W": lambda: self.close_tab(self.tabs.currentIndex()),
            "Ctrl+L": self._focus_url,
            "Ctrl+R": lambda: self.current().reload(),
            "F5": lambda: self.current().reload(),
            "Ctrl+Q": self.close,
            "Ctrl+Tab": lambda: self._cycle(1),
            "Ctrl+Shift+Tab": lambda: self._cycle(-1),
            "F11": lambda: self.set_fullscreen(not self.isFullScreen()),
            "Ctrl+H": lambda: self.new_tab(url=HISTORY_PAGE.toString()),
        }.items():
            QShortcut(QKeySequence(key), self).activated.connect(fn)

        self.bridge.updateFinished.connect(self._toast_result)
        QTimer.singleShot(3000, self._check_updates)
        self._toast = None

        # virtual browsers and groups survive restarts
        QApplication.instance().aboutToQuit.connect(self._save_groups)
        saved_sessions = [e for e in self.config.get("sessions", [])
                          if e.get("sid") and e.get("name")]
        if saved_sessions:
            self.sessions = saved_sessions
            if not any(e["sid"] == "main" for e in self.sessions):
                self.sessions.insert(0, {"name": "Browser 1", "sid": "main"})
        self.active_session = self.sessions[0]["sid"]
        self._restore_groups()
        self._restore_session_tabs()
        self.new_tab(url=initial_url, group=None,
                     session=self.active_session)
        self.switch_session(self.active_session)

    def _restore_session_tabs(self):
        valid = {e["sid"] for e in self.sessions}
        for sid, items in (self.config.get("sessionTabs") or {}).items():
            if sid not in valid:
                continue
            for item in items:
                if isinstance(item, dict):
                    u, t = item.get("u", ""), item.get("t") or None
                else:
                    u, t = item, None
                if u:
                    self.new_tab(url=u, group=None, session=sid,
                                 switch=False, lazy=True, title=t)

    def _save_groups(self):
        data = []
        for g in self.groups:
            urls = []
            for i in self._group_indices(g):
                view = self.tabs.widget(i)
                url = view.url()
                if url == START_PAGE:
                    urls.append({"u": "", "t": ""})
                else:
                    urls.append({"u": url.toString()
                                 or getattr(view, "_pending", "")
                                 or getattr(view, "_requested", ""),
                                 "t": self.tabs.tabText(i)})
            data.append({"name": g,
                         "color": self.group_colors.get(g, "#6c7086"),
                         "collapsed": bool(self.collapsed.get(g)),
                         "gid": self.group_ids.get(g),
                         "session": self.group_sessions.get(g, "main"),
                         "urls": urls})
        self.config["tabGroups"] = data
        # loose tabs are saved per virtual browser too (start pages
        # excluded — every start spawns a fresh one anyway)
        session_tabs = {}
        for i in range(self.tabs.count()):
            view = self.tabs.widget(i)
            if self._is_header(view) or self._group_of(view) is not None:
                continue
            url = view.url()
            if url == START_PAGE:
                continue
            u = (url.toString() or getattr(view, "_pending", "")
                 or getattr(view, "_requested", ""))
            if not u:
                continue
            sid = getattr(view, "session", "main")
            session_tabs.setdefault(sid, []).append(
                {"u": u, "t": self.tabs.tabText(i)})
        self.config["sessionTabs"] = session_tabs
        self.config["sessions"] = self.sessions
        self.save_config()

    def _restore_groups(self):
        for entry in self.config.get("tabGroups", []):
            name = entry.get("name")
            urls = entry.get("urls", [])
            if not name or name in self.groups or not urls:
                continue
            if entry.get("gid"):
                self.group_ids[name] = entry["gid"]
            session = entry.get("session", "main")
            if not any(e["sid"] == session for e in self.sessions):
                session = "main"
            self._register_group(name, entry.get("color", "#6c7086"),
                                 session=session)
            for item in urls:
                if isinstance(item, dict):
                    u, t = item.get("u", ""), item.get("t") or None
                else:
                    u, t = item, None
                self.new_tab(url=u or None, group=name, switch=False,
                             lazy=bool(u), title=t)
            if entry.get("collapsed"):
                self._toggle_collapse(name)

    # ---- updates ----
    def _check_updates(self):
        """Quietly look for a newer version on GitHub at startup."""
        if not (APP_DIR / ".git").exists():
            return
        fetch = QProcess(self)
        fetch.setWorkingDirectory(str(APP_DIR))

        def fetched(*_):
            try:
                fetch.deleteLater()
            except RuntimeError:
                return  # quitting while the check was in flight
            self._count_behind()
        fetch.finished.connect(fetched)
        fetch.start("git", ["fetch", "--quiet"])

    def _count_behind(self):
        proc = QProcess(self)
        proc.setWorkingDirectory(str(APP_DIR))

        def done(*_):
            try:
                out = bytes(proc.readAllStandardOutput()).decode().strip()
                code = proc.exitCode()
                proc.deleteLater()
            except RuntimeError:
                return  # quitting while the check was in flight
            if code == 0 and out.isdigit() and int(out) > 0:
                self._show_toast()
        proc.finished.connect(done)
        proc.start("git", ["rev-list", "--count", "HEAD..@{u}"])

    def _show_toast(self):
        if self._toast:
            return
        toast = QWidget(self, objectName="toast")
        toast.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(toast)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        self._toast_label = QLabel("Update available")
        update = QToolButton(text="Update now")
        close = QToolButton(text="\u2715", objectName="tabclose")
        lay.addWidget(self._toast_label)
        lay.addWidget(update)
        lay.addWidget(close)

        self._toast = toast
        self._toast_timer = QTimer(self)
        self._toast_timer.setSingleShot(True)
        self._toast_timer.setInterval(5000)
        self._toast_timer.timeout.connect(self._hide_toast)

        close.clicked.connect(self._hide_toast)
        update.clicked.connect(lambda: (
            self._toast_timer.stop(),
            update.hide(),
            self._toast_label.setText("Updating\u2026"),
            self.bridge.runUpdate(),
        ))

        self._place_toast()
        toast.show()
        toast.raise_()
        self._toast_timer.start()

    def _place_toast(self):
        if self._toast:
            self._toast.adjustSize()
            self._toast.move(self.width() - self._toast.width() - 16, 54)

    def _hide_toast(self):
        if self._toast:
            self._toast_timer.stop()
            self._toast.deleteLater()
            self._toast = None

    def _toast_result(self, msg):
        if not self._toast:
            return
        self._toast_label.setText(msg)
        if msg.startswith("Updated"):
            restart = QToolButton(text="Restart now")
            restart.clicked.connect(self.restart)
            self._toast.layout().insertWidget(1, restart)
            self._toast_timer.stop()  # stays until acted on or dismissed
        else:
            self._toast_timer.setInterval(8000)
            self._toast_timer.start()
        self._place_toast()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._place_toast()

    # ---- tabs ----
    def current(self):
        return self.tabs.currentWidget()

    def new_tab(self, url=None, switch=True, blank=False,
                group=INHERIT_GROUP, session=None, lazy=False, title=None):
        if group is INHERIT_GROUP:
            group = self._group_of(self.current())
        if session is None:
            session = (self.group_sessions.get(group)
                       if group is not None else self.active_session)
        view = WebView(self, self._profile_for(group, session))
        view.group = group
        view.session = session or "main"
        view.urlChanged.connect(lambda u, v=view: self._url_changed(v, u))
        view.titleChanged.connect(lambda t, v=view: self._title_changed(v, t))
        view.iconChanged.connect(lambda ic, v=view: self._icon_changed(v, ic))
        if group is not None:
            if self.collapsed.get(group):
                self._toggle_collapse(group)
            block = [self._header_index(group)] + self._group_indices(group)
            i = self.tabs.insertTab(max(block) + 1, view, "New tab")
        else:
            i = self.tabs.addTab(view, "New tab")

        self._add_close_button(i, view)

        if switch:
            self.tabs.setCurrentIndex(i)
        if not blank:
            if url is None:
                view.load(START_PAGE)
                self._focus_url()
            elif lazy:
                # the page loads only when the tab is first opened,
                # so restored sessions cost no memory until used
                view._pending = url
                view._requested = url
            else:
                view._requested = url  # fallback for saving before commit
                view.load(QUrl(url))
        if title:
            self.tabs.setTabText(i, title)
        elif lazy and url:
            self.tabs.setTabText(i, QUrl(url).host() or "Tab")
        return view

    def _add_close_button(self, index, view):
        close = QToolButton(text="✕", objectName="tabclose")
        close.clicked.connect(lambda _, v=view: self.close_tab(self.tabs.indexOf(v)))
        # wrapper centers the circle between the tab text and the tab's right wall
        holder = QWidget()
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 6, 0)
        hl.addWidget(close)
        self.tabs.tabBar().setTabButton(index, QTabBar.ButtonPosition.RightSide, holder)

    def close_tab(self, index):
        w = self.tabs.widget(index)
        if w is None or self._is_header(w):
            return
        group = self._group_of(w)
        self.tabs.removeTab(index)
        w.deleteLater()
        # a group whose last tab closes disappears, like in Chrome
        if group is not None and not self._group_indices(group):
            h = self._header_index(group)
            if h is not None:
                hw = self.tabs.widget(h)
                self.tabs.removeTab(h)
                hw.deleteLater()
            self.groups.remove(group)
            self.group_colors.pop(group, None)
            self.collapsed.pop(group, None)
        self._ensure_tab_or_quit()

    def _cycle(self, step):
        # skip group headers and collapsed (hidden) tabs
        bar = self.tabs.tabBar()
        n = self.tabs.count()
        i = self.tabs.currentIndex()
        for _ in range(n):
            i = (i + step) % n
            if bar.isTabVisible(i) and not self._is_header(self.tabs.widget(i)):
                self.tabs.setCurrentIndex(i)
                return

    # ---- drag & drop between groups ----
    def _tab_moved(self, _frm, _to):
        """While a tab is dragged, its group follows its position:
        inside a group's block (or onto its pill) joins it, outside
        leaves. Qt reports every displaced tab here, so only the tab
        actually held by the user is ever reassigned."""
        if getattr(self, "_fixing", False):
            return
        w = getattr(self, "_drag_view", None)
        if w is None:
            return
        to = self.tabs.indexOf(w)
        if to < 0:
            return
        left = self.tabs.widget(to - 1) if to > 0 else None
        right = (self.tabs.widget(to + 1)
                 if to + 1 < self.tabs.count() else None)
        if left is None:
            lg = None
        elif self._is_header(left):
            lg = left.group_header
        else:
            lg = getattr(left, "group", None)
        if right is not None and self._is_header(right):
            # dropped onto the pill: merge into that group
            target = right.group_header
        else:
            rg = None if right is None else getattr(right, "group", None)
            target = lg if lg is not None and lg == rg else None
        if target is not None and self.collapsed.get(target):
            target = None  # no dropping into a folded group
        w.group = target
        self.tabs.tabBar().update()

    def _fix_group_layout(self, group):
        """Ensure the group's tabs sit contiguously after its pill."""
        bar = self.tabs.tabBar()
        for _ in range(self.tabs.count()):
            h = self._header_index(group)
            members = self._group_indices(group)
            if h is None or not members:
                return
            want = set(range(h + 1, h + 1 + len(members)))
            misplaced = [m for m in members if m not in want]
            if not misplaced:
                return
            m = misplaced[0]
            bar.moveTab(m, h + len(members) if m > h else h)

    def _finalize_drag(self):
        held = self.current()  # the tab the user was dragging stays active
        self._fixing = True
        try:
            for g in list(self.groups):
                self._fix_group_layout(g)
        finally:
            self._fixing = False
        for g in list(self.groups):
            self._cleanup_group_if_empty(g)
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            if not self._is_header(w):
                self._sync_profile(w)
        if held is not None:
            i = self.tabs.indexOf(held)
            if i >= 0 and not self._is_header(held):
                self.tabs.setCurrentIndex(i)
        self.tabs.tabBar().update()

    # ---- virtual browsers ----
    def _update_session_bar(self):
        lay = self.sesslay
        while lay.count():
            item = lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # also sweep strays a drag left floating outside the layout
        for child in self.sessrow.children():
            if isinstance(child, QWidget):
                child.hide()
                child.deleteLater()
        for entry in self.sessions:
            active = entry["sid"] == self.active_session
            b = QToolButton(text=entry["name"])
            b.setStyleSheet(
                "QToolButton { background: %s; color: %s; border: 1px solid %s;"
                " border-radius: 0px; padding: 4px 14px; font-weight: %s; }"
                % (("#16161d", "#ffffff", "#a6adc8", "bold") if active
                   else ("#0d0d12", "#6c7086", "rgba(108, 112, 134, 60)", "normal")))
            b.clicked.connect(lambda _, sid=entry["sid"]: self.switch_session(sid))
            b.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            b.customContextMenuRequested.connect(
                lambda _p, sid=entry["sid"], b=b: self._session_menu(b, sid))
            b._session_sid = entry["sid"]
            b.installEventFilter(self)
            lay.addWidget(b)
        plus = QToolButton(text="+")
        plus.setToolTip("New virtual browser (own cookies and tabs)")
        plus.setStyleSheet("QToolButton { background: #0d0d12; color: #6c7086;"
                           " border: 1px solid rgba(108, 112, 134, 60);"
                           " border-radius: 0px; padding: 4px 10px; }")
        plus.clicked.connect(self._add_session)
        lay.addWidget(plus)
        lay.addStretch()

    def switch_session(self, sid):
        self.active_session = sid
        bar = self.tabs.tabBar()
        first = None
        for i in range(self.tabs.count()):
            w = self.tabs.widget(i)
            in_session = getattr(w, "session", "main") == sid
            if self._is_header(w):
                visible = in_session
            else:
                g = getattr(w, "group", None)
                visible = in_session and not (g and self.collapsed.get(g))
            bar.setTabVisible(i, visible)
            if visible and not self._is_header(w) and first is None:
                first = i
        self._update_session_bar()
        current = self.current()
        if (current is None or self._is_header(current)
                or getattr(current, "session", "main") != sid):
            if first is not None:
                self.tabs.setCurrentIndex(first)
            else:
                self.new_tab(group=None)  # fresh, ungrouped, this browser
        bar.update()

    def _add_session(self):
        names = {e["name"] for e in self.sessions}
        n = 2
        while "Browser %d" % n in names:
            n += 1
        name, ok = QInputDialog.getText(
            self, "New browser", "Name:", text="Browser %d" % n)
        name = name.strip()
        if not ok or not name:
            return
        while name in names:
            name += " 2"
        self.sessions.append({"name": name, "sid": uuid.uuid4().hex[:8]})
        self.switch_session(self.sessions[-1]["sid"])

    def _session_buttons_in_layout(self):
        out = []
        for k in range(self.sesslay.count()):
            w = self.sesslay.itemAt(k).widget()
            if w is not None and hasattr(w, "_session_sid"):
                out.append(w)
        return out

    def _drag_session_move(self, local_x):
        drag = self._sess_drag
        btn = drag["btn"]
        if not drag["moved"]:
            if abs(local_x - drag["x"]) <= 12:
                return
            # lift the button out of the row; a spacer keeps its slot
            drag["moved"] = True
            drag["index"] = self._session_buttons_in_layout().index(btn)
            spacer = QWidget(self.sessrow)
            spacer.setFixedSize(btn.size())
            drag["spacer"] = spacer
            self.sesslay.removeWidget(btn)
            self.sesslay.insertWidget(drag["index"], spacer)
            spacer.show()
        # the button follows the cursor (all math in strip coordinates)
        x = int(local_x - drag["grip"])
        x = max(0, min(x, self.sessrow.width() - btn.width()))
        btn.move(x, btn.y())
        btn.raise_()
        # the gap travels as the cursor crosses neighbors
        others = self._session_buttons_in_layout()
        target = sum(1 for b in others
                     if local_x > b.geometry().center().x())
        if target != drag["index"]:
            drag["index"] = target
            self.sesslay.removeWidget(drag["spacer"])
            self.sesslay.insertWidget(target, drag["spacer"])
            self.sesslay.activate()

    def _drag_session_drop(self, drag):
        drag["btn"].hide()
        drag["btn"].deleteLater()  # the rebuild recreates it in place
        spacer = drag["spacer"]
        if spacer is not None:
            self.sesslay.removeWidget(spacer)
            spacer.deleteLater()
        sid = drag["btn"]._session_sid
        entry = next((e for e in self.sessions if e["sid"] == sid), None)
        if entry is not None:
            rest = [e for e in self.sessions if e["sid"] != sid]
            i = max(0, min(drag["index"], len(rest)))
            self.sessions = rest[:i] + [entry] + rest[i:]
        self._update_session_bar()

    def _session_menu(self, button, sid):
        menu = QMenu(self)
        name = next((e["name"] for e in self.sessions if e["sid"] == sid), sid)
        rename = menu.addAction("Rename\u2026")
        close = menu.addAction("Close \u201c%s\u201d" % name)
        close.setEnabled(len(self.sessions) > 1)
        chosen = menu.exec(button.mapToGlobal(button.rect().bottomLeft()))
        if chosen is close:
            self._close_session(sid)
        elif chosen is rename:
            new, ok = QInputDialog.getText(
                self, "Rename browser", "Name:", text=name)
            new = new.strip()
            if ok and new and all(e["name"] != new for e in self.sessions):
                for entry in self.sessions:
                    if entry["sid"] == sid:
                        entry["name"] = new
                self._update_session_bar()

    def _close_session(self, sid):
        if len(self.sessions) <= 1:
            return
        for i in reversed(range(self.tabs.count())):
            w = self.tabs.widget(i)
            if getattr(w, "session", "main") == sid:
                self.tabs.removeTab(i)
                w.deleteLater()
        for g in [g for g, s in list(self.group_sessions.items()) if s == sid]:
            if g in self.groups:
                self.groups.remove(g)
            self.group_colors.pop(g, None)
            self.collapsed.pop(g, None)
            self.group_ids.pop(g, None)
            self.group_sessions.pop(g, None)
        self.sessions = [e for e in self.sessions if e["sid"] != sid]
        self.session_profiles.pop(sid, None)
        if self.active_session == sid:
            self.switch_session(self.sessions[0]["sid"])
        else:
            self._update_session_bar()

    # ---- site permissions (microphone, camera, screen share) ----
    def _permission_requested(self, permission):
        label = PERMISSION_LABELS.get(permission.permissionType())
        if label is None:
            return  # let the engine keep its default for exotic requests
        origin = permission.origin().host() or permission.origin().toString()
        key = "%s|%s" % (origin, permission.permissionType().name)
        if self.config.get("permissions", {}).get(key):
            permission.grant()
            return
        if key in self._session_perms:
            permission.grant() if self._session_perms[key] else permission.deny()
            return
        self._perm_queue.append((permission, origin, label, key))
        self._next_permission()

    def _next_permission(self):
        if self._perm_widget is not None or not self._perm_queue:
            return
        permission, origin, label, key = self._perm_queue.pop(0)
        bar = QWidget(self, objectName="toast")
        bar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 8, 8)
        lay.setSpacing(10)
        lay.addWidget(QLabel("%s wants to %s" % (origin, label)))
        allow = QToolButton(text="Allow")
        deny = QToolButton(text="Deny")
        lay.addWidget(allow)
        lay.addWidget(deny)
        self._perm_widget = bar

        def decide(granted):
            permission.grant() if granted else permission.deny()
            self._session_perms[key] = granted
            if granted:  # only allows are remembered across restarts
                self.config.setdefault("permissions", {})[key] = True
                self.save_config()
            bar.deleteLater()
            self._perm_widget = None
            self._next_permission()

        allow.clicked.connect(lambda: decide(True))
        deny.clicked.connect(lambda: decide(False))
        bar.adjustSize()
        bar.move(max(0, (self.width() - bar.width()) // 2), 54)
        bar.show()
        bar.raise_()

    # ---- tab groups (Chrome-style inline headers) ----
    def _group_of(self, widget):
        if widget is None or self._is_header(widget):
            return None
        return getattr(widget, "group", None)

    def _is_header(self, widget):
        return getattr(widget, "group_header", None) is not None

    def _header_index(self, group):
        for i in range(self.tabs.count()):
            if getattr(self.tabs.widget(i), "group_header", None) == group:
                return i
        return None

    def _group_indices(self, group):
        return [i for i in range(self.tabs.count())
                if not self._is_header(self.tabs.widget(i))
                and getattr(self.tabs.widget(i), "group", None) == group]

    def _group_dot(self, group):
        pix = QPixmap(12, 12)
        pix.fill(QColor(self.group_colors.get(group, "#6c7086")))
        return QIcon(pix)

    def _group_menu(self):
        menu = GroupMenu(self)
        listed = [g for g in self.groups
                  if self.group_sessions.get(g, "main") == self.active_session]
        for g in listed:
            action = menu.addAction(self._group_dot(g), g)
            action.setData(g)
            action.triggered.connect(lambda _, g=g: self._goto_group(g))
        if listed:
            menu.addSeparator()
        menu.addAction("New group\u2026").triggered.connect(self._new_group)
        menu.exec(self._book.mapToGlobal(self._book.rect().bottomLeft()))

    def _prompt_group(self):
        """Ask for a name and color; returns (name, color) or None."""
        name, ok = QInputDialog.getText(self, "New group", "Group name:")
        name = name.strip()
        if not ok or not name or name in self.groups:
            return None
        picker = QMenu(self)
        for label, color in GROUP_COLORS:
            pix = QPixmap(12, 12)
            pix.fill(QColor(color))
            picker.addAction(QIcon(pix), label).setData(color)
        chosen = picker.exec(
            self._book.mapToGlobal(self._book.rect().bottomLeft()))
        fallback = GROUP_COLORS[len(self.groups) % len(GROUP_COLORS)][1]
        return name, (chosen.data() if chosen else fallback)

    def _register_group(self, name, color, at=None, session=None):
        self.groups.append(name)
        self.group_colors[name] = color
        self.collapsed[name] = False
        session = session or self.active_session
        self.group_sessions[name] = session
        header = QWidget()
        header.group_header = name
        header.session = session
        if at is None:
            self.tabs.addTab(header, name)
        else:
            self.tabs.insertTab(at, header, name)
        self.tabs.tabBar().update()

    def _new_group(self):
        result = self._prompt_group()
        if result is None:
            return
        self._register_group(*result)
        self.new_tab(group=result[0])  # every group starts with a fresh tab

    def _tab_to_new_group(self, index):
        result = self._prompt_group()
        if result is None:
            return
        view = self.tabs.widget(index)
        self._register_group(*result, at=index)  # header lands before the tab
        view.group = result[0]
        self._sync_profile(view)
        self.tabs.tabBar().update()

    def _move_tab_to_group(self, index, group):
        view = self.tabs.widget(index)
        old = self._group_of(view)
        if old == group:
            return
        title = self.tabs.tabText(index)
        was_current = view is self.current()
        self.tabs.removeTab(index)
        view.group = group
        if group is not None:
            if self.collapsed.get(group):
                self._toggle_collapse(group)
            block = [self._header_index(group)] + self._group_indices(group)
            j = self.tabs.insertTab(max(block) + 1, view, title)
        else:
            j = self.tabs.addTab(view, title)
        self.tabs.setTabIcon(j, view.icon())
        self._add_close_button(j, view)
        self._sync_profile(view)
        if was_current:
            self.tabs.setCurrentIndex(j)
        if old is not None:
            self._cleanup_group_if_empty(old)
        self.tabs.tabBar().update()

    def _cleanup_group_if_empty(self, group):
        if self._group_indices(group):
            return
        h = self._header_index(group)
        if h is not None:
            hw = self.tabs.widget(h)
            self.tabs.removeTab(h)
            hw.deleteLater()
        if group in self.groups:
            self.groups.remove(group)
        self.group_colors.pop(group, None)
        self.collapsed.pop(group, None)

    def _rename_group(self, old, new):
        new = new.strip()
        if not new or new in self.groups or old not in self.groups:
            return
        for i in self._group_indices(old):
            self.tabs.widget(i).group = new
        h = self._header_index(old)
        if h is not None:
            self.tabs.widget(h).group_header = new
            self.tabs.setTabText(h, new)
        self.groups[self.groups.index(old)] = new
        self.group_colors[new] = self.group_colors.pop(old, "#6c7086")
        self.collapsed[new] = self.collapsed.pop(old, False)
        if old in self.group_ids:
            self.group_ids[new] = self.group_ids.pop(old)
        self.tabs.tabBar().update()

    def ungroup(self, group):
        """Dissolve the group but keep its tabs, like Chrome's Ungroup."""
        if self.collapsed.get(group):
            self._toggle_collapse(group)
        for i in self._group_indices(group):
            member = self.tabs.widget(i)
            member.group = None
            self._sync_profile(member)
        h = self._header_index(group)
        if h is not None:
            hw = self.tabs.widget(h)
            self.tabs.removeTab(h)
            hw.deleteLater()
        if group in self.groups:
            self.groups.remove(group)
        self.group_colors.pop(group, None)
        self.collapsed.pop(group, None)
        self.tabs.tabBar().update()

    def _tab_menu(self, index):
        view = self.tabs.widget(index)
        group = self._group_of(view)
        menu = QMenu(self)
        if group is None:
            menu.addAction("Add tab to new group\u2026").triggered.connect(
                lambda: self._tab_to_new_group(self.tabs.indexOf(view)))
            if self.groups:
                sub = menu.addMenu("Add tab to group")
                for g in self.groups:
                    sub.addAction(self._group_dot(g), g).triggered.connect(
                        lambda _, g=g: self._move_tab_to_group(
                            self.tabs.indexOf(view), g))
        else:
            menu.addAction("New tab in group").triggered.connect(
                lambda: self.new_tab(group=group))
            menu.addAction("Remove from group").triggered.connect(
                lambda: self._move_tab_to_group(self.tabs.indexOf(view), None))
        menu.addSeparator()
        menu.addAction("Close tab").triggered.connect(
            lambda: self.close_tab(self.tabs.indexOf(view)))
        bar = self.tabs.tabBar()
        menu.exec(bar.mapToGlobal(bar.tabRect(index).bottomLeft()))

    def _header_menu(self, index):
        group = self.tabs.widget(index).group_header
        menu = QMenu(self)
        menu.addAction("New tab in group").triggered.connect(
            lambda: self.new_tab(group=group))
        menu.addAction("Rename\u2026").triggered.connect(
            lambda: self._rename_dialog(group))
        colors = menu.addMenu("Color")
        for label, color in GROUP_COLORS:
            pix = QPixmap(12, 12)
            pix.fill(QColor(color))
            colors.addAction(QIcon(pix), label).triggered.connect(
                lambda _, c=color: self._set_group_color(group, c))
        menu.addSeparator()
        menu.addAction("Ungroup").triggered.connect(
            lambda: self.ungroup(group))
        menu.addAction("Close group").triggered.connect(
            lambda: self.delete_group(group))
        bar = self.tabs.tabBar()
        menu.exec(bar.mapToGlobal(bar.tabRect(index).bottomLeft()))

    def _rename_dialog(self, group):
        name, ok = QInputDialog.getText(
            self, "Rename group", "Group name:", text=group)
        if ok:
            self._rename_group(group, name)

    def _set_group_color(self, group, color):
        if group in self.group_colors:
            self.group_colors[group] = color
            self.tabs.tabBar().update()

    def _goto_group(self, group):
        if self.collapsed.get(group):
            self._toggle_collapse(group)
        members = self._group_indices(group)
        if members:
            self.tabs.setCurrentIndex(members[0])

    def _toggle_collapse(self, group):
        self.collapsed[group] = not self.collapsed.get(group, False)
        bar = self.tabs.tabBar()
        for i in self._group_indices(group):
            bar.setTabVisible(i, not self.collapsed[group])

    def _nearest_tab(self, index):
        bar = self.tabs.tabBar()
        order = list(range(index + 1, self.tabs.count()))
        order += list(range(index - 1, -1, -1))
        for i in order:
            if bar.isTabVisible(i) and not self._is_header(self.tabs.widget(i)):
                return i
        return None

    def delete_group(self, group):
        """Close the group's tabs and its header."""
        for i in reversed(self._group_indices(group)):
            w = self.tabs.widget(i)
            self.tabs.removeTab(i)
            w.deleteLater()
        h = self._header_index(group)
        if h is not None:
            hw = self.tabs.widget(h)
            self.tabs.removeTab(h)
            hw.deleteLater()
        if group in self.groups:
            self.groups.remove(group)
        self.group_colors.pop(group, None)
        self.collapsed.pop(group, None)
        self._ensure_tab_or_quit()

    def _ensure_tab_or_quit(self):
        """Closing the very last tab closes the browser, like Chrome.
        Other virtual browsers keep this one alive with a fresh tab;
        tabs surviving only in folded groups unfold instead."""
        real = [i for i in range(self.tabs.count())
                if not self._is_header(self.tabs.widget(i))]
        if not real:
            self.close()
            return
        mine = [i for i in real
                if getattr(self.tabs.widget(i), "session", "main")
                == self.active_session]
        if not mine:
            self.new_tab(group=None)
            return
        bar = self.tabs.tabBar()
        if not any(bar.isTabVisible(i) for i in mine):
            for g in self.groups:
                if (self.collapsed.get(g)
                        and self.group_sessions.get(g, "main")
                        == self.active_session):
                    self._toggle_collapse(g)
                    members = self._group_indices(g)
                    if members:
                        self.tabs.setCurrentIndex(members[0])
                    break

    # ---- navigation ----
    def _navigate(self):
        text = self.urlbar.text().strip()
        if not text:
            return
        if " " in text or ("." not in text and text != "localhost"):
            url = SEARCH_URL.format(QUrl.toPercentEncoding(text).data().decode())
        elif "://" in text:
            url = text
        else:
            url = "https://" + text
        self.current().load(QUrl(url))
        self.current().setFocus()

    def _focus_url(self):
        self.urlbar.setFocus()
        self.urlbar.selectAll()

    # ---- suggestions ----
    def _fetch_suggestions(self):
        text = self.urlbar.text().strip().lower()
        if len(text) < 2 or "://" in text or not self.urlbar.hasFocus():
            return
        domains = [d for d in COMMON_SITES + sorted(self.known_hosts)
                   if d.startswith(text) or d.split(".")[0].startswith(text)
                   or d.startswith("www." + text)]
        domains = list(dict.fromkeys(domains))
        domains = [d for d in domains
                   if not (d.startswith("www.") and d[4:] in domains)][:3]
        if self._suggest_reply is not None:
            self._suggest_reply.abort()
        url = QUrl(SUGGEST_URL)
        q = QUrlQuery()
        q.addQueryItem("client", "firefox")
        q.addQueryItem("q", text)
        url.setQuery(q)
        reply = self._nam.get(QNetworkRequest(url))
        self._suggest_reply = reply
        reply.finished.connect(
            lambda r=reply, t=text, d=domains: self._got_suggestions(r, t, d))

    def _got_suggestions(self, reply, text, domains):
        if reply is self._suggest_reply:
            self._suggest_reply = None
        searches = []
        try:
            searches = json.loads(bytes(reply.readAll()).decode())[1]
        except Exception:
            pass
        reply.deleteLater()
        if self.urlbar.text().strip().lower() != text:
            return  # user typed on; a newer request is coming
        items = domains + [s for s in searches if s not in domains][:6]
        self.suggest_model.setStringList(items)
        if items and self.urlbar.hasFocus():
            self.completer.complete()

    def _remember_host(self, url):
        host = url.host()
        if url.scheme() in ("http", "https") and host and host not in self.known_hosts:
            self.known_hosts.add(host)
            try:
                HOSTS_FILE.parent.mkdir(parents=True, exist_ok=True)
                HOSTS_FILE.write_text(json.dumps(sorted(self.known_hosts)))
            except OSError:
                pass

    def _url_changed(self, view, url):
        self._remember_host(url)
        host = url.host().removeprefix("www.")
        native_dark = any(host == d or host.endswith("." + d)
                          for d in NATIVE_DARK_SITES)
        view.page().settings().setAttribute(
            QWebEngineSettings.WebAttribute.ForceDarkMode, not native_dark)
        # never clobber the bar while the user is typing in it
        if view is self.current() and not self.urlbar.hasFocus():
            self.urlbar.setText("" if url == START_PAGE else url.toString())
            self.urlbar.setCursorPosition(0)

    def _place_newtab(self):
        btn = getattr(self, "_newtab_btn", None)
        if btn is None:
            return
        bar = self.tabs.tabBar()
        last = None
        for i in range(self.tabs.count()):
            if bar.isTabVisible(i):
                last = i
        x = 6 if last is None else bar.tabRect(last).right() + 6
        x = min(x, bar.width() - btn.width() - 2)
        y = max(0, (bar.height() - btn.height()) // 2)
        btn.move(max(0, x), y)
        btn.raise_()

    def _update_close_buttons(self):
        """Very small tabs show just the site icon: the close button
        survives only on the active tab, like Chrome."""
        bar = self.tabs.tabBar()
        current = self.tabs.currentIndex()
        for i in range(self.tabs.count()):
            holder = bar.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if holder is None:
                continue
            want = bar.tabRect(i).width() >= 90 or i == current
            if holder.isVisibleTo(bar) != want:
                holder.setVisible(want)

    def _icon_changed(self, view, icon):
        i = self.tabs.indexOf(view)
        if i >= 0:
            self.tabs.setTabIcon(i, icon)

    def _title_changed(self, view, title):
        i = self.tabs.indexOf(view)
        if i >= 0:
            self.tabs.setTabText(i, title or "New tab")
            self.tabs.setTabToolTip(i, title)
        self._record_history(view.url(), title)

    # ---- history ----
    def _record_history(self, url, title):
        if not self.config.get("history", True):
            return
        if url.scheme() not in ("http", "https") or not title:
            return
        entry = {"url": url.toString(), "title": title, "t": int(time.time())}
        if self.history and self.history[-1]["url"] == entry["url"]:
            self.history[-1] = entry  # same page: refresh title/time only
        else:
            self.history.append(entry)
            if len(self.history) > HISTORY_MAX:
                del self.history[:len(self.history) - HISTORY_MAX + 500]
        self.save_history()

    def save_history(self):
        try:
            HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_FILE.write_text(json.dumps(self.history))
        except OSError:
            pass

    def save_config(self):
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self.config))
        except OSError:
            pass

    def eventFilter(self, obj, event):
        # dragging a virtual-browser button: it lifts out of the row
        # and floats with the cursor, a gap marks where it will land
        if isinstance(obj, QToolButton) and hasattr(obj, "_session_sid"):
            if (event.type() == QEvent.Type.MouseButtonPress
                    and event.button() == Qt.MouseButton.LeftButton):
                local = self.sessrow.mapFromGlobal(
                    event.globalPosition().toPoint()).x()
                self._sess_drag = {"btn": obj, "moved": False,
                                   "x": local,
                                   "grip": event.position().x(),
                                   "spacer": None, "index": 0}
            elif (event.type() == QEvent.Type.MouseMove
                    and getattr(self, "_sess_drag", None)):
                self._drag_session_move(self.sessrow.mapFromGlobal(
                    event.globalPosition().toPoint()).x())
            elif (event.type() == QEvent.Type.MouseButtonRelease
                    and getattr(self, "_sess_drag", None)):
                drag = self._sess_drag
                self._sess_drag = None
                if drag["moved"]:
                    self._drag_session_drop(drag)
                    return True  # a drag is not a click
            return False
        # group headers act as fold/unfold buttons: swallow their clicks
        # before Qt selects them, so the page never flashes
        if (obj is self.tabs.tabBar()
                and event.type() in (QEvent.Type.MouseButtonPress,
                                     QEvent.Type.MouseButtonDblClick)):
            i = obj.tabAt(event.position().toPoint())
            if event.type() == QEvent.Type.MouseButtonPress:
                self._drag_active = True
                w0 = self.tabs.widget(i) if i >= 0 else None
                # remember which tab the hand is on: during a drag Qt
                # also reports the tabs being pushed aside, and only the
                # held tab may change group membership
                self._drag_view = (w0 if w0 is not None
                                   and not self._is_header(w0) else None)
            if i >= 0:
                w = self.tabs.widget(i)
                if event.button() == Qt.MouseButton.RightButton:
                    if self._is_header(w):
                        self._header_menu(i)
                    else:
                        self._tab_menu(i)
                    return True
                if self._is_header(w):
                    if event.button() == Qt.MouseButton.LeftButton:
                        self._header_clicked(w.group_header, i)
                    return True
        if (obj is self.tabs.tabBar()
                and event.type() == QEvent.Type.MouseButtonRelease
                and getattr(self, "_drag_active", False)):
            self._drag_active = False
            self._drag_view = None
            QTimer.singleShot(0, self._finalize_drag)
        return super().eventFilter(obj, event)

    def _tab_changed(self, index):
        w = self.tabs.widget(index)
        if w is not None and self._is_header(w):
            # selection landed on a header some indirect way: step off it
            QTimer.singleShot(0, lambda: self._step_off_header(index))
            return
        if w is not None and getattr(w, "_pending", None):
            pending = w._pending
            w._pending = None
            w.load(QUrl(pending))
        if w is not None and hasattr(w, "url"):
            url = w.url()
            self.urlbar.setText("" if url == START_PAGE else url.toString())
        self._update_close_buttons()

    def _step_off_header(self, index):
        # only act if the selection is still stuck on that header
        w = self.tabs.widget(index)
        if (self.tabs.currentIndex() != index or w is None
                or not self._is_header(w)):
            return
        target = self._nearest_tab(index)
        if target is not None:
            self.tabs.setCurrentIndex(target)

    def _header_clicked(self, group, index):
        bar = self.tabs.tabBar()
        if not self.collapsed.get(group, False):
            # about to collapse: leave the group BEFORE its tabs hide,
            # otherwise Qt momentarily selects the header (flash)
            cur = self.tabs.currentIndex()
            if self._group_of(self.tabs.widget(cur)) == group:
                outside = [i for i in range(self.tabs.count())
                           if bar.isTabVisible(i)
                           and not self._is_header(self.tabs.widget(i))
                           and self._group_of(self.tabs.widget(i)) != group]
                if outside:
                    self.tabs.setCurrentIndex(
                        min(outside, key=lambda i: abs(i - cur)))
                else:
                    self.new_tab(group=None)  # fresh ungrouped tab
        self._toggle_collapse(group)

    # ---- misc ----
    def set_fullscreen(self, on):
        self.chrome.setVisible(not on)
        self.tabs.tabBar().setVisible(not on)
        self.showFullScreen() if on else self.showNormal()

    def _make_profile(self, storage):
        """A fully configured cookie jar; each tab group gets its own."""
        profile = QWebEngineProfile(storage, self)
        profile.setPersistentCookiesPolicy(
            QWebEngineProfile.PersistentCookiesPolicy.ForcePersistentCookies)
        profile.downloadRequested.connect(self._download)
        s = profile.settings()
        s.setAttribute(QWebEngineSettings.WebAttribute.FullScreenSupportEnabled, True)
        s.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
        # the start page is a local file; without this it may not navigate
        # to the web (search box / quick links -> ERR_NETWORK_ACCESS_DENIED)
        s.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
        # let calls ring and voice chats start without a prior click
        s.setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture, False)
        # auto-darken pages that have no dark theme of their own
        s.setAttribute(QWebEngineSettings.WebAttribute.ForceDarkMode, True)
        # some sites (Teams…) block calls on unknown browsers; the engine
        # IS Chromium, so drop the QtWebEngine token from the identity
        profile.setHttpUserAgent(
            re.sub(r"\s?QtWebEngine/[\d.]+", "", profile.httpUserAgent()))
        script = QWebEngineScript()
        script.setName("google-black")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentReady)
        script.setWorldId(QWebEngineScript.ScriptWorldId.ApplicationWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(GOOGLE_BLACK_JS)
        profile.scripts().insert(script)
        return profile

    def _profile_for(self, group, session="main"):
        """Cookies are per virtual browser: every tab in it — grouped
        or not — shares that browser's jar."""
        return self._session_profile(session or "main")

    def _session_profile(self, sid):
        if sid == "main":
            return self.profile
        if sid not in self.session_profiles:
            self.session_profiles[sid] = self._make_profile("browser-s-" + sid)
        return self.session_profiles[sid]

    def _sync_profile(self, view):
        """Keep a tab in its virtual browser's cookie jar (no-op unless
        it somehow ended up in the wrong one)."""
        want = self._profile_for(self._group_of(view),
                                 getattr(view, "session", "main"))
        if view.page().profile() is want:
            return
        url = view.url()
        target = url if url.toString() else QUrl(getattr(view, "_requested", ""))
        view.attach_profile(want)
        view.load(target if target.toString() else START_PAGE)

    def _download(self, request):
        request.setDownloadDirectory(str(DOWNLOAD_DIR))
        # don't overwrite existing files: name.pdf -> name (1).pdf
        name = request.downloadFileName()
        stem, suffix = Path(name).stem, Path(name).suffix
        n = 1
        while (DOWNLOAD_DIR / name).exists():
            name = f"{stem} ({n}){suffix}"
            n += 1
        request.setDownloadFileName(name)
        request.accept()
        widget = DownloadWidget(request, self._dismiss_download)
        self.dllay.insertWidget(self.dllay.count() - 1, widget)
        self.dlbar.show()

    def restart(self):
        """Relaunch the browser (used after an update)."""
        if getattr(self, "_instance_server", None) is not None:
            # free the single-instance socket so the successor
            # becomes the new primary instead of handing off to us
            self._instance_server.close()
            QLocalServer.removeServer(SINGLE_INSTANCE_SOCKET)
        # successor waits for this process to exit before starting
        os.environ["BROWSER_RESTART_WAIT"] = str(os.getpid())
        QProcess.startDetached(sys.executable, [str(APP_DIR / "browser.py")])
        QApplication.instance().quit()

    def closeEvent(self, event):
        # closing the window from the compositor (e.g. Super+Q) must end
        # the process too — a lingering ghost would hold the
        # single-instance socket and swallow future launches
        QApplication.instance().quit()
        super().closeEvent(event)

    def _dismiss_download(self, widget):
        self.dllay.removeWidget(widget)
        widget.deleteLater()
        if self.dllay.count() <= 1:  # only the stretch left
            self.dlbar.hide()


SINGLE_INSTANCE_SOCKET = "browser-single-instance"


def _pid_alive(pid):
    if sys.platform == "win32":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def main():
    # a URL argument means we were asked to open a link (e.g. as the
    # system default browser)
    url = sys.argv[1] if len(sys.argv) > 1 else None

    # started by our own restart(): let the old process finish dying
    # so the profile and socket are free
    predecessor = os.environ.pop("BROWSER_RESTART_WAIT", None)
    if predecessor:
        for _ in range(60):
            try:
                if not _pid_alive(int(predecessor)):
                    break
            except ValueError:
                break
            time.sleep(0.1)

    # single instance: two instances sharing one profile breaks Chromium's
    # network/cache storage, so hand the link to the running one instead
    probe = QLocalSocket()
    probe.connectToServer(SINGLE_INSTANCE_SOCKET)
    if probe.waitForConnected(300):
        probe.write((url or "raise").encode())
        probe.flush()
        probe.waitForBytesWritten(300)
        return

    QGuiApplication.setDesktopFileName("browser")
    app = QApplication(sys.argv)
    app.setApplicationName("browser")
    app.setWindowIcon(QIcon(str(APP_DIR / "icon.svg")))
    app.setStyleSheet(STYLE)
    win = Browser(initial_url=url)

    QLocalServer.removeServer(SINGLE_INSTANCE_SOCKET)
    server = QLocalServer()
    server.listen(SINGLE_INSTANCE_SOCKET)
    win._instance_server = server

    def handoff():
        conn = server.nextPendingConnection()

        def read():
            message = bytes(conn.readAll()).decode().strip()
            win.new_tab(url=None if message in ("", "raise") else message)
            win.showNormal()
            win.raise_()
            win.activateWindow()
        conn.readyRead.connect(read)

    server.newConnection.connect(handoff)

    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
