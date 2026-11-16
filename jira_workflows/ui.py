"""Sign in page, workflows page, statuses page, workflow viewer dialog and main window."""
import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from PyQt6.QtCore import QSettings, QBuffer, QEvent, QIODevice, QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import (QColor, QDesktopServices, QFontDatabase, QFontMetrics, QGuiApplication, QIcon, QKeySequence, QPainter, QPen, QPixmap,
                         QShortcut)
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QPlainTextEdit, QButtonGroup, QCheckBox, QComboBox, QDialog, QFileDialog, QFrame, QGraphicsDropShadowEffect,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QGridLayout, QScrollArea, QTextBrowser,
    QProgressBar, QPushButton, QRadioButton, QSplitter, QStackedWidget,
    QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget,
)

from . import APP_NAME, RELEASE_DATE, __version__, fluent
from .changelog import LOG_PATH, log_change, read_log
from .confluence import REQUIRED_LABEL, ConfluenceError, ConflictError, clean_label, client_for, page_title, parse_target
from .page import build_storage
from .api import JiraClient, JiraError, scheme_edit_url, screen_url, transition_url, workflow_edit_url
from .flowchart import FlowchartView, has_jira_positions, render_workflow
from .page import is_portal
from .flowexport import to_drawio
from .jobscript import (DIAGRAM_NOTICE, apply_template, can_export, describe_destination, describe_template, diagram_position, generate_script,
                        job_name, load_job, make_job, save_job, script_name)
from .preview import TemplatePreview
from .screens import field_catalog, load_details
from .textutil import titlecase
from .template import (COLOR_STYLES, ELEMENTS, FLOW_FORMATS, GROUPS, LAYOUTS, LINE_STYLES, NODE_SCALES, PNG_QUALITY, WIDTH_OPTIONS, Template,
                       layout_info)
from .fluent import ACCENTS, MODES, TEXT_SIZES, Appearance, T
from .model import CATEGORIES, build_catalog

CACHE_ROOT = Path.home() / "JiraWorkflows"
WIDTH_STEPS = (25, 50, 75, 100)  # extra width offered on top of the default diagram width
CATEGORY_TOKEN = {"TODO": "fg3", "IN_PROGRESS": "brand_fg", "DONE": "success"}


class Btn(QPushButton):
    """A push button whose text is always in Title Case, also when it changes later (Delete (3), Signing in…)."""

    def __init__(self, text=""):
        super().__init__(titlecase(text))

    def setText(self, text):
        super().setText(titlecase(text))


def page_header(title, caption=""):
    """The title of a page with one line under it that says what the page is for."""
    box = QVBoxLayout(); box.setSpacing(2)
    box.addWidget(label(title, "subtitle"))
    if caption:
        box.addWidget(label(caption, "caption"))
    return box


def button(text, primary=False, subtle=False):
    b = Btn(text)
    b.setProperty("primary", primary)
    b.setProperty("subtle", subtle)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


HEADING_ROLES = ("title", "subtitle", "field", "navtitle")


def label(text, role=None, verbatim=False):
    """A label. Headings (titles, card titles, bold labels) are shown in Title Case; `verbatim` keeps data such as workflow names as is."""
    l = QLabel(text if verbatim or role not in HEADING_ROLES else titlecase(text))
    if role:
        l.setProperty("role", role)
    if role == "caption":
        l.setWordWrap(True)
    return l


def link_colors():
    """(idle, hover text, hover tint) of a link in the current theme."""
    c = QColor(T["brand_fg"])
    return T["brand_fg"], (c.lighter(130) if T["dark"] else c.darker(130)).name(), T["brand_tint"]


class LinkLabel(QLabel):
    """Link(s) in a table cell. At rest: accent colour. Under the mouse: darker colour, underline and a tint behind the link."""

    def __init__(self, parts, tip="", sep=", "):
        super().__init__()
        self.parts, self.sep, self._now = parts, sep, None
        self.setOpenExternalLinks(True); self.setWordWrap(True)  # the cell's own padding provides the 12 px inset
        self.setToolTip(tip)
        self.setProperty("lines", [t for t, _ in parts] if sep == "<br>" else [sep.join(t for t, _ in parts)])
        self.linkHovered.connect(self._hover)
        self._hover("")

    def _hover(self, url):
        if url == self._now:
            return
        self._now = url
        idle, hot, tint = link_colors()
        out = []
        for text, u in self.parts:
            href, esc = html.escape(u, quote=True), html.escape(text)
            out.append(f'<a href="{href}" style="color:{hot};text-decoration:underline"><span style="background-color:{tint}">{esc}</span></a>'
                       if u == url else f'<a href="{href}" style="color:{idle};text-decoration:none">{esc}</a>')
        self.setText(self.sep.join(out))


def link(text, url, tip=""):
    return LinkLabel([(text, url)], tip or url)


def padded(widget, h=10, v=6):
    """Wrap a control (button ...) so that it keeps a margin inside its table cell."""
    box = QWidget(); lay = QHBoxLayout(box); lay.setContentsMargins(h, v, h, v); lay.addWidget(widget)
    return box


def fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00").replace("+0000", "+00:00")).strftime("%Y-%m-%d %H:%M")  # no wrap inside a date
    except ValueError:
        return iso or "—"


class Worker(QThread):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)
    progress = pyqtSignal(int, int, str)

    def __init__(self, fn):
        super().__init__()
        self.fn = fn

    def run(self):
        try:
            self.done.emit(self.fn(lambda a, b, m="": self.progress.emit(a, b, m)))
        except JiraError as e:
            self.failed.emit(str(e))
        except Exception as e:  # keep the UI alive on unexpected errors
            self.failed.emit(f"{type(e).__name__}: {e}")


def make_table(columns, stretch):
    t = QTableWidget(0, len(columns))
    t.setHorizontalHeaderLabels(columns)
    t.verticalHeader().hide(); t.setShowGrid(False); t.setAlternatingRowColors(True)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setTextElideMode(Qt.TextElideMode.ElideRight)
    t.horizontalHeader().setHighlightSections(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
    t.horizontalHeader().setStretchLastSection(False)
    t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.ResizeMode.Stretch)
    return t


URL_ROLE = Qt.ItemDataRole.UserRole + 2


def header_text(text):
    """Column headings in Title Case: 'WORKFLOW SCHEME' -> 'Workflow Scheme' (codes such as ID stay upper case)."""
    return titlecase(text.lower())


class AutoFitTable(QTableWidget):
    """Data grid: columns share the width according to their content, long text wraps instead of being cut, headers are
    left aligned (numbers right aligned) and links react to the mouse. `fixed` maps column -> width for check box / action columns."""

    def __init__(self, columns, fixed=None, numeric=()):
        super().__init__(0, len(columns))
        self.fixed = dict(fixed or {})
        self.setHorizontalHeaderLabels([header_text(c) for c in columns])
        head = self.horizontalHeader()
        head.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for c in numeric:
            self.horizontalHeaderItem(c).setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.setSectionResizeMode(QHeaderView.ResizeMode.Fixed); head.setHighlightSections(False); head.setFixedHeight(42)
        self.verticalHeader().hide(); self.verticalHeader().setMinimumSectionSize(48)
        self.setShowGrid(False); self.setAlternatingRowColors(True)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setWordWrap(True); self.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)   # only when the text needs more room than the window has
        self.setMouseTracking(True); self.viewport().installEventFilter(self)
        self._hover = None
        self.empty_text = ""   # shown in the middle of the table while it has no rows
        self.cellEntered.connect(self._entered)
        self.cellClicked.connect(lambda r, c: self.item(r, c) and self.item(r, c).data(URL_ROLE)
                                 and QDesktopServices.openUrl(QUrl(self.item(r, c).data(URL_ROLE))))

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.rowCount() == 0 and self.empty_text:
            p = QPainter(self.viewport())
            p.setPen(QColor(T["fg3"]))
            r = self.viewport().rect().adjusted(40, 60, -40, 0)
            p.drawText(r, int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap), self.empty_text)
            p.end()

    # ---- link items: darker colour + underline under the mouse ----
    def _style_link(self, r, c, on):
        it = self.item(r, c)
        if it is None:
            return
        idle, hot, _ = link_colors()
        it.setForeground(QColor(hot if on else idle))
        f = it.font(); f.setUnderline(on); it.setFont(f)

    def _entered(self, r, c):
        it = self.item(r, c)
        url = it.data(URL_ROLE) if it else None
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor if url else Qt.CursorShape.ArrowCursor)
        if self._hover and self._hover != (r, c):
            self._style_link(*self._hover, False); self._hover = None
        if url and self._hover != (r, c):
            self._style_link(r, c, True); self._hover = (r, c)

    def eventFilter(self, obj, e):
        if obj is self.viewport() and e.type() == QEvent.Type.Leave and self._hover:
            self._style_link(*self._hover, False); self._hover = None
        return super().eventFilter(obj, e)

    # ---- layout ----
    def _measure(self, r, c):
        it, wd = self.item(r, c), self.cellWidget(r, c)
        if it is not None:
            return it.text().split("\n"), 32
        lines = wd.property("lines") if wd is not None else None
        if wd is not None and not lines:   # a widget that did not say what it shows: use the width it asks for
            wd.ensurePolished()
            return [], wd.sizeHint().width()
        return (lines or []), 34

    def refit(self):
        n, avail = self.columnCount(), self.viewport().width()
        if avail < 50 or not n:
            return
        fm, bold = self.fontMetrics(), QFontMetrics(self.font())
        want, hdr = {}, {}
        for c in range(n):
            if c in self.fixed:
                continue
            w = hdr[c] = bold.horizontalAdvance(self.horizontalHeaderItem(c).text()) + 40
            for r in range(self.rowCount()):
                wd = self.cellWidget(r, c)
                if wd is not None and wd.property("fit"):   # a cell widget that sizes itself: take the width it asks for
                    wd.ensurePolished()
                    need = wd.sizeHint().width()
                    for lab in wd.findChildren(LinkLabel):   # a link's size hint was taken before the app font applied: measure its text here
                        lines = lab.property("lines") or []
                        text_w = max((fm.horizontalAdvance(line) for line in lines), default=0) + 8
                        lab.setMinimumWidth(text_w)   # the layout may not give the link less than its text needs
                        need += max(0, text_w - lab.sizeHint().width())
                    w = max(w, need + 8)
                    continue
                lines, pad = self._measure(r, c)
                w = max(w, max((fm.horizontalAdvance(line) for line in lines), default=0) + pad)
            want[c] = w
        room = avail - sum(self.fixed.values())
        total = sum(want.values())
        if total <= room:  # everything fits on one line: hand the spare room out in proportion
            widths = {c: w + (room - total) * w / total for c, w in want.items()}
        else:  # squeeze: every column keeps a minimum, the long ones give up the most and wrap
            lo = {c: min(w, max(160, hdr[c])) for c, w in want.items()}  # a column never gets narrower than its header (or 160 px of text)
            for c, w in want.items():   # a column of no-wrap cells keeps its width
                if any((self.cellWidget(r, c) is not None and self.cellWidget(r, c).property("fit")) for r in range(self.rowCount())):
                    lo[c] = w
            spare, extra = max(0, room - sum(lo.values())), sum(w - lo[c] for c, w in want.items())
            widths = {c: lo[c] + (w - lo[c]) * min(1.0, spare / extra if extra else 1) for c, w in want.items()}
        for c in range(n):
            self.setColumnWidth(c, int(self.fixed.get(c, widths.get(c, 100))))
        self.resizeRowsToContents()
        for r in range(self.rowCount()):  # rows holding wrapped link / text labels grow to show all of it
            wd0 = self.cellWidget(r, 0)
            if wd0 is not None and wd0.property("detail"):   # an open detail row spans the whole table
                m = wd0.contentsMargins()
                self.setRowHeight(r, wd0.heightForWidth(avail - m.left() - m.right() - 4) + m.top() + m.bottom() + 4)
                continue
            h = self.rowHeight(r)
            for c in range(n):
                wd = self.cellWidget(r, c)
                if isinstance(wd, QLabel):
                    m = wd.contentsMargins()
                    h = max(h, wd.heightForWidth(self.columnWidth(c) - 24 - m.left() - m.right()) + 22)  # 24 = item padding
            self.setRowHeight(r, max(h, self.verticalHeader().minimumSectionSize()))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.refit()

    def showEvent(self, e):
        super().showEvent(e)
        self.refit()


def link_cell(text, url, tip=""):
    """A table cell that looks and behaves like a link (AutoFitTable opens its URL on click and highlights it on hover)."""
    it = QTableWidgetItem(text)
    it.setForeground(QColor(link_colors()[0])); it.setData(URL_ROLE, url); it.setToolTip(tip or text)
    return it


def cell(text, align_right=False, color=None, tip=None):
    it = QTableWidgetItem(str(text))
    if align_right:
        it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if color:
        it.setForeground(QColor(color))
    if tip:
        it.setToolTip(tip)
    return it


def sort_rows(rows, keys, col, asc):
    return sorted(rows, key=keys[col], reverse=not asc)


# --------------------------------------------------------------------------- sign in
def tag(text, color="brand_fg"):
    """A small pill next to a name (e.g. Required). `color` is the theme token of its text and border."""
    l = QLabel(text)
    l.setStyleSheet(f"background: {T['brand_tint']}; color: {T[color]}; border: 1px solid {T[color]}; border-radius: 9px; "
                    f"padding: 1px 8px; font-size: {max(12, T['px'] - 1)}px; font-weight: 600;")
    return l


def portal_tag():
    """The small yellow "Portal" tag: a transition that customers can take in the Jira Service Management portal."""
    l = QLabel("Portal")
    fill, edge, text = ("#433519", "#8A6D00", "#FFD43B") if T["dark"] else ("#FFF4CE", "#C9A100", "#6B5200")
    l.setStyleSheet(f"background: {fill}; color: {text}; border: 1px solid {edge}; border-radius: 9px; padding: 1px 8px; "
                    f"font-size: {max(12, T['px'] - 1)}px; font-weight: 600;")
    l.setToolTip("Customers can take this transition in the Jira Service Management portal")
    return l


class ScopesPanel(QFrame):
    """The OAuth 2.0 scopes a service account needs, by product, each group with its own Copy button."""
    # (product, how needed, what it is used for, scope names). Pure data: the widgets below are built from this list.
    GROUPS = [
        ("Jira", "Required", "Sign-in check, spaces, statuses, workflows, workflow schemes, screens with their tabs and fields, work types; "
                             "and deleting inactive workflows, workflow schemes and unused statuses.",
         ["read:jira-work", "read:jira-user", "manage:jira-configuration"]),
        ("Confluence", "To publish", "Reading the parent page or folder, its space and the labels in use; creating and updating the pages, their "
                                     "attachments (diagram image, draw.io file), labels and page properties; adding the report to the parent page.",
         ["read:confluence-content.all", "read:confluence-content.summary", "read:confluence-space.summary", "write:confluence-content",
          "write:confluence-file", "read:confluence-props", "write:confluence-props"]),
    ]

    def __init__(self):
        super().__init__()
        self.setObjectName("scopes")
        v = QVBoxLayout(self); v.setContentsMargins(16, 12, 16, 12); v.setSpacing(4)
        v.addWidget(label("Scopes of the service account", "field"))
        v.addWidget(label("Add these scopes when you create its OAuth 2.0 credentials.", "caption"))
        self.chips = []
        for title, badge, text, scopes in self.GROUPS:
            v.addSpacing(6)
            row = QHBoxLayout(); row.setSpacing(8)
            row.addWidget(label(title, "field"), alignment=Qt.AlignmentFlag.AlignVCenter); row.addWidget(tag(badge), alignment=Qt.AlignmentFlag.AlignVCenter); row.addStretch()
            cp = button("Copy", subtle=True); cp.setToolTip(f"Copy the {title} scopes, separated by spaces")
            cp.clicked.connect(lambda _=False, sc=scopes, b=cp: self._copy(sc, b)); row.addWidget(cp)   # sc=, b= bind this loop's values
            v.addLayout(row)
            v.addWidget(label(text, "caption"))
            chips = QLabel(); chips.setWordWrap(True); chips.setTextFormat(Qt.TextFormat.RichText)
            chips.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); v.addWidget(chips)
            self.chips.append((chips, scopes))
        v.addSpacing(6)
        v.addWidget(label("The account also needs the Administer Jira permission, and permission to add pages in the Confluence space. "
                          "Scopes only limit what the credentials may request.", "caption"))
        self.restyle()

    @staticmethod
    def _copy(scopes, button_):
        # scopes go to the clipboard space-separated (the format the Atlassian credentials form accepts); the button says "Copied" for 1.5 s
        QApplication.clipboard().setText(" ".join(scopes))
        button_.setText("Copied")
        QTimer.singleShot(1500, lambda: button_.setText("Copy"))

    def restyle(self):
        # Colors come from the theme dict T. The scope names are shown as HTML "chips"; the hyphens become non-breaking hyphens
        # (U+2011) so a name never wraps in the middle.
        self.setStyleSheet(f"QFrame#scopes {{ background: {T['alt']}; border: 1px solid {T['stroke2']}; border-radius: 8px; }}")
        for chips, scopes in self.chips:
            chips.setText('<p style="line-height:165%">' + "&nbsp; ".join(
                f'<span style="white-space:nowrap; font-family:\'Cascadia Mono\', Consolas, monospace; font-size:{max(12, T["px"] - 1)}px; color:{T["brand_fg"]}; '
                f'background-color:{T["brand_tint"]};">&nbsp;{sc.replace("-", chr(0x2011))}&nbsp;</span>' for sc in scopes) + "</p>")


class ScopesDialog(QDialog):
    """The scopes of the service account, in a pop-up, so the sign-in card stays short."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Permission Scopes"); self.setModal(True)
        v = QVBoxLayout(self); v.setContentsMargins(20, 20, 20, 16)
        panel = ScopesPanel(); panel.setFixedWidth(520); v.addWidget(panel)
        row = QHBoxLayout(); row.addStretch(); close = button("Close", primary=True); close.clicked.connect(self.accept); row.addWidget(close); v.addLayout(row)


class LoadSteps(QFrame):
    """The steps of loading the site's data after sign-in, each with its state, and one progress bar for the whole load."""
    PENDING, RUNNING, DONE, FAILED = range(4)

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(5)
        self.names, self.icons, self.texts, self.state = [], [], [], []
        self.rows = QVBoxLayout(); self.rows.setSpacing(7); v.addLayout(self.rows)
        self.meter = QProgressBar(); self.meter.setTextVisible(False); self.meter.setRange(0, 1000); v.addSpacing(4); v.addWidget(self.meter)
        self.caption = label("", "caption"); v.addWidget(self.caption)

    def begin(self, names):
        """Start over with these steps (the first one is already running)."""
        while self.rows.count():
            it = self.rows.takeAt(0)
            if it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()
        self.names, self.icons, self.texts, self.state = list(names), [], [], [self.PENDING] * len(names)
        for n in names:
            row = QHBoxLayout(); row.setSpacing(8)
            ic, tx = QLabel(), QLabel(n)
            ic.setFixedWidth(18); ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(ic); row.addWidget(tx, 1)
            self.rows.addLayout(row); self.icons.append(ic); self.texts.append(tx)
        self.meter.setValue(0); self.caption.setText("")
        self.restyle()

    def update_step(self, name, done=0, total=0):
        """`name` is the step now running: the steps before it are done. done / total is its own progress, when it has one."""
        if name not in self.names:
            return
        i = self.names.index(name)
        self.state = [self.DONE if k < i else self.RUNNING if k == i else self.PENDING for k in range(len(self.names))]
        frac = (done / total) if total else 0
        self.meter.setValue(int(1000 * (i + frac) / len(self.names)))
        self.caption.setText(f"Step {i + 1} of {len(self.names)}: {name}" + (f"  ({done} of {total})" if total else ""))
        self.restyle()

    def finish(self):
        self.state = [self.DONE] * len(self.names); self.meter.setValue(1000)
        self.caption.setText(f"All {len(self.names)} steps are done."); self.restyle()

    def fail(self, msg):
        self.state = [self.FAILED if s == self.RUNNING else s for s in self.state]
        self.caption.setText(msg); self.restyle()

    def restyle(self):
        marks = {self.PENDING: ("○", T["fg3"]), self.RUNNING: ("●", T["brand_fg"]), self.DONE: ("✔", T["success"]), self.FAILED: ("✖", T["danger"])}
        for ic, tx, st in zip(self.icons, self.texts, self.state):
            mark, col = marks[st]
            ic.setText(mark); ic.setStyleSheet(f"color: {col}; font-weight: 700;")
            tx.setStyleSheet(f"color: {T['fg1'] if st in (self.RUNNING, self.FAILED) else col if st == self.PENDING else T['fg2']};"
                             + (" font-weight: 600;" if st == self.RUNNING else ""))


class LoginPage(QWidget):
    logged_in = pyqtSignal(object, dict)
    signed_out = pyqtSignal()
    go_workflows = pyqtSignal()
    retry_requested = pyqtSignal()
    loading = False   # the data of the site is being read right now (the steps are shown)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        card = QFrame(); card.setObjectName("card"); card.setFixedWidth(520)
        shadow = QGraphicsDropShadowEffect(blurRadius=24, yOffset=4); shadow.setColor(QColor(0, 0, 0, 40))
        card.setGraphicsEffect(shadow)
        v = QVBoxLayout(card); v.setContentsMargins(36, 32, 36, 28); v.setSpacing(4)
        v.addWidget(label(APP_NAME, "subtitle"))
        v.addWidget(label("Sign in to read the workflows and statuses of your Jira Cloud site.", "caption"))
        v.addSpacing(20)

        self.prefs = QSettings("Nagarro", APP_NAME)   # the site, the account and the client ID are remembered, the token and the secret never are
        self.site = QLineEdit(placeholderText="https://your-company.atlassian.net"); self.site.setText(str(self.prefs.value("site", "")))
        self.email = QLineEdit(placeholderText="you@company.com"); self.email.setText(str(self.prefs.value("email", "")))
        self.token = QLineEdit(placeholderText="Atlassian API token"); self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.client_id = QLineEdit(placeholderText="Client ID of the service account"); self.client_id.setText(str(self.prefs.value("client_id", "")))
        self.client_secret = QLineEdit(placeholderText="Client secret"); self.client_secret.setEchoMode(QLineEdit.EchoMode.Password)

        def field(layout, text, widget, hint=""):
            """Fluent 2 field: the label above, the input, the hint under it."""
            layout.addWidget(label(text, "field")); layout.addWidget(widget)
            if hint:
                layout.addWidget(label(hint, "caption"))
            layout.addSpacing(12)

        field(v, "Jira site URL", self.site, "The address of your Jira Cloud site. It is used by both ways of signing in.")

        # -- how to sign in: one tab for each
        self.tabs = QTabWidget(); self.tabs.setObjectName("pivot"); self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setExpanding(False); self.tabs.tabBar().setDrawBase(False); self.tabs.tabBar().setUsesScrollButtons(False)
        scopes_btn = button("View the required permission scopes", subtle=True); scopes_btn.clicked.connect(lambda: ScopesDialog(self.window()).exec())
        notes = []
        for title, fields, note in (
                ("Username / Token", [("Username / email", self.email, ""), ("User token", self.token, "")],
                 "Create a token at id.atlassian.com → Security → API tokens. The account needs the Administer Jira permission."),
                ("Service Account", [("Client ID", self.client_id, ""), ("Client secret", self.client_secret, "")],
                 "Create the service account and its OAuth 2.0 credentials at admin.atlassian.com → Directory → Service accounts. "
                 "The credentials need the Jira API scopes (and the Confluence scopes to publish).")):
            pane = QWidget(); g = QVBoxLayout(pane); g.setContentsMargins(0, 16, 0, 0); g.setSpacing(4)
            for text, widget, hint in fields:
                field(g, text, widget, hint)
            if title == "Service Account":   # the scopes the credentials need, right where the credentials are entered
                g.addWidget(scopes_btn, alignment=Qt.AlignmentFlag.AlignLeft)
            self.tabs.addTab(pane, title); notes.append(note)
        v.addWidget(self.tabs)
        self.note = label("", "caption"); v.addWidget(self.note)   # outside the tabs: a tab does not make room for wrapped text
        self.tabs.currentChanged.connect(lambda i: self.note.setText(notes[i]))
        v.addSpacing(12)
        for w in (self.site, self.email, self.token, self.client_id, self.client_secret):
            w.returnPressed.connect(self.sign_in)
        self.error = QLabel(); self.error.setObjectName("error"); self.error.setWordWrap(True); self.error.hide()
        v.addWidget(self.error); v.addSpacing(4)
        self.btn = button("Sign in", primary=True); self.btn.setMinimumHeight(36); self.btn.clicked.connect(self.sign_in)
        v.addWidget(self.btn)
        v.addSpacing(4)
        v.addWidget(label("Your credentials are kept in memory only. The token and the client secret are never saved.", "caption"))
        self.tabs.currentChanged.connect(lambda _: (self.error.hide(), self._fit_tabs()))
        self.tabs.setCurrentIndex(1 if str(self.prefs.value("method", "basic")) == "oauth" else 0)
        self.note.setText(notes[self.tabs.currentIndex()])
        self._fit_tabs()
        self.form_card = card

        self.done_card = QFrame(); self.done_card.setObjectName("card"); self.done_card.setFixedWidth(520)
        d = QVBoxLayout(self.done_card); d.setContentsMargins(36, 32, 36, 28); d.setSpacing(6)
        d.addWidget(label("Connected", "subtitle"))
        self.who = label("", "field"); d.addWidget(self.who)
        self.where = label("", "caption"); d.addWidget(self.where)
        d.addSpacing(14)
        self.loading_title = label("Loading your data", "field"); d.addWidget(self.loading_title)
        self.steps = LoadSteps(); d.addWidget(self.steps)
        self.load_error = QLabel(); self.load_error.setObjectName("error"); self.load_error.setWordWrap(True); self.load_error.hide(); d.addWidget(self.load_error)
        d.addSpacing(14)
        row = QHBoxLayout()
        self.go = button("View workflows", primary=True); self.go.clicked.connect(self.go_workflows); self.go.setEnabled(False)
        self.retry = button("Try again"); self.retry.clicked.connect(self.retry_requested); self.retry.hide()
        out = button("Sign out"); out.clicked.connect(self.signed_out)
        row.addWidget(self.go); row.addWidget(self.retry); row.addWidget(out); row.addStretch(); d.addLayout(row)
        self.done_card.hide()

        outer = QVBoxLayout(self); outer.addStretch()
        outer.addWidget(self.form_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(self.done_card, alignment=Qt.AlignmentFlag.AlignHCenter)
        outer.addStretch()

    def _fit_tabs(self):
        """A tab widget sizes itself too small for these fields; give it the height of the taller tab."""
        self.tabs.setMinimumHeight(self.tabs.tabBar().sizeHint().height()
                                   + max(self.tabs.widget(i).sizeHint().height() for i in range(self.tabs.count())))
        card = self.tabs.parentWidget(); lay = card.layout(); lay.invalidate()
        card.setMinimumHeight(lay.totalHeightForWidth(card.width()))   # wrapped captions: the card gets the height they need
        card.updateGeometry()

    def showEvent(self, e):
        super().showEvent(e)
        self._fit_tabs()

    def set_connected(self, me, site, account=""):
        self.who.setText(f"{me.get('displayName', '')} ({me.get('emailAddress') or account or self.email.text()})")
        self.where.setText(site)
        self.form_card.hide(); self.done_card.show()

    def set_disconnected(self):
        self.loading = False
        self.done_card.hide(); self.form_card.show()

    # ---- the steps of loading the data after sign-in ----
    def load_started(self, names):
        self.loading = True
        self.steps.begin(names); self.load_error.hide(); self.retry.hide(); self.go.setEnabled(False)
        self.loading_title.setText(titlecase("Loading your data"))

    def load_progress(self, name, done, total):
        self.steps.update_step(name, done, total)

    def load_done(self):
        self.loading = False
        self.steps.finish(); self.go.setEnabled(True); self.loading_title.setText(titlecase("Your data is ready"))

    def load_failed(self, msg):
        self.loading = False
        self.steps.fail("The data could not be read."); self.load_error.setText(msg); self.load_error.show()
        self.retry.show(); self.go.setEnabled(True); self.loading_title.setText(titlecase("Loading stopped"))

    def sign_in(self):
        site = self.site.text()
        if self.tabs.currentIndex() == 1:
            if not (site.strip() and self.client_id.text().strip() and self.client_secret.text().strip()):
                self._error("Enter the site URL, the client ID and the client secret.")
                return
            cid, secret = self.client_id.text(), self.client_secret.text()

            def job(_):   # looking up the site and getting the token are network calls
                client = JiraClient.service_account(site, cid, secret)
                return client, client.login()
        else:
            if not (site.strip() and self.email.text().strip() and self.token.text().strip()):
                self._error("Enter the site URL, username / email and user token.")
                return
            client = JiraClient(site, self.email.text(), self.token.text())

            def job(_):
                return client, client.login()
        self.error.hide(); self.btn.setEnabled(False); self.btn.setText("Signing in…")
        self.w = Worker(job)
        self.w.done.connect(lambda r: self._ok(*r)); self.w.failed.connect(self._error)
        self.w.start()

    def _reset(self):
        self.btn.setEnabled(True); self.btn.setText("Sign in")

    def _ok(self, client, me):
        self.prefs.setValue("site", self.site.text().strip()); self.prefs.setValue("email", self.email.text().strip())
        self.prefs.setValue("client_id", self.client_id.text().strip())
        self.prefs.setValue("method", "oauth" if self.tabs.currentIndex() == 1 else "basic")
        self._reset(); self.token.clear(); self.client_secret.clear()
        self.set_connected(me, client.site, f"service account {client.email}" if client.cloud_id else ""); self.logged_in.emit(client, me)

    def _error(self, msg):
        self._reset(); self.error.setText(msg); self.error.show()


# --------------------------------------------------------------------------- viewer
class WorkflowViewer(QDialog):
    """Pop-up with the flowchart of one workflow plus its status and transition lists."""

    def __init__(self, wf, site, parent=None):
        super().__init__(parent)
        self.wf = wf
        self.setWindowTitle(f"Workflow — {wf['name']}")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True); self.resize(1400, 820); self.setMinimumSize(720, 420)

        chart = FlowchartView(); self.chart = chart
        chart.set_workflow(wf)
        chart.selected.connect(self._chart_selected)

        header = QHBoxLayout()
        header.addWidget(label(wf["name"], "subtitle", verbatim=True)); header.addStretch()
        for text, fn in (("−", lambda: chart.zoom(1 / 1.2)), ("+", lambda: chart.zoom(1.2)),
                         ("Fit", chart.fit), ("100%", chart.reset_zoom)):
            b = button(text, subtle=True); b.clicked.connect(fn); header.addWidget(b)
        width = QComboBox()
        width.addItems(["Default"] + [f"+{p}%" for p in WIDTH_STEPS])
        width.setToolTip("Widen the diagram beyond the default horizontal distance between statuses")
        width.currentIndexChanged.connect(lambda i: chart.set_extra_width(WIDTH_STEPS[i - 1] if i else 0))
        header.addWidget(label("Width", "field")); header.addWidget(width); header.addSpacing(8)
        style = QComboBox(); style.addItems(["Right-angle lines", "Curved lines"])
        style.currentIndexChanged.connect(lambda i: chart.set_curved(i == 1))
        header.addWidget(style); header.addSpacing(8)
        jira_pos = QCheckBox("Use Jira Status positions"); jira_pos.toggled.connect(chart.set_use_jira)
        if has_jira_positions(wf):
            jira_pos.setToolTip("Place the statuses where the Jira workflow editor has them (the X and Y values Jira reports)")
        else:
            jira_pos.setEnabled(False); jira_pos.setToolTip("Jira did not report status positions for this workflow")
        header.addWidget(jira_pos); header.addSpacing(8)
        labels = QCheckBox("Show all transition labels"); labels.toggled.connect(chart.set_labels)
        labels.setToolTip("Off: labels appear for the status or transition you hover or select")
        header.addWidget(labels); header.addSpacing(8)
        png = button("Save PNG"); png.clicked.connect(self.save_png); header.addWidget(png)
        edit = button("Edit in Jira", primary=True)
        edit.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(workflow_edit_url(site, wf))))
        header.addWidget(edit)

        spaces = ", ".join(wf["project_keys"]) or "—"
        schemes = ", ".join(s["name"] for s in wf["schemes"]) or "—"
        info = label(f"{len(wf['statuses'])} statuses · {len(wf['transitions'])} transitions · "
                     f"{'Active' if wf['active'] else 'Inactive'} · Space: {spaces} · Scheme: {schemes}", "caption")

        self.st = AutoFitTable(["ID", "STATUS", "CATEGORY", "PROPERTIES"])
        for s in sorted(wf["statuses"], key=lambda s: (CATEGORIES[s["category"]][1], s["name"].lower())):
            r = self.st.rowCount(); self.st.insertRow(r)
            self.st.setItem(r, 0, cell(s["id"])); self.st.setItem(r, 1, cell(s["name"], tip=s["name"]))
            self.st.setItem(r, 2, cell(CATEGORIES[s["category"]][0], color=T[CATEGORY_TOKEN[s["category"]]]))
            props = "\n".join(f"{k} = {v}" if v != "" else k for k, v in sorted(s["properties"].items()))
            self.st.setItem(r, 3, cell(props, tip=props) if props else cell("—", color=T["fg3"]))
            self.st.item(r, 0).setData(Qt.ItemDataRole.UserRole, s["id"])
        self.st.itemSelectionChanged.connect(self._status_selected)

        names = {s["id"]: s["name"] for s in wf["statuses"]}
        self.tr = AutoFitTable(["ID", "TRANSITION", "FROM → TO", "SCREEN", "TRANSITION PROPERTIES"])
        for t in wf["transitions"]:
            src = ", ".join(names.get(f, f) for f in t["from"]) or ("Start" if t["type"] == "initial" else "Any status")
            r = self.tr.rowCount(); self.tr.insertRow(r)
            self.tr.setItem(r, 0, cell(t["id"]))
            link_text, link_tip = t["name"] or f"Transition {t['id']}", f"{t['name']}\nClick to open transition {t['id']} in Jira"
            if is_portal(t):   # the link and a yellow Portal tag next to it
                box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 8, 0); bl.setSpacing(8)
                name_link = LinkLabel([(link_text, transition_url(site, wf, t["id"]))], link_tip, sep="<br>")
                name_link.setWordWrap(False); bl.addWidget(name_link)
                bl.addWidget(portal_tag(), alignment=Qt.AlignmentFlag.AlignVCenter); bl.addStretch()
                box.setProperty("fit", True)
                self.tr.setCellWidget(r, 1, box)
            else:
                self.tr.setItem(r, 1, link_cell(link_text, transition_url(site, wf, t["id"]), link_tip))
            to = names.get(t["to"], t["to"])
            self.tr.setItem(r, 2, cell(f"{src} → {to}", tip=f"{src} → {to}"))
            if t["screen_id"]:
                name = t["screen_name"] or f"Screen {t['screen_id']}"
                self.tr.setItem(r, 3, link_cell(name, screen_url(site, t["screen_id"]), f"{name}\nClick to open screen {t['screen_id']} in Jira"))
            else:
                self.tr.setItem(r, 3, cell("—", color=T["fg3"]))
            props = "\n".join(f"{k} = {v}" if v != "" else k for k, v in sorted((t.get("properties") or {}).items()))
            self.tr.setItem(r, 4, cell(props, tip=props) if props else cell("—", color=T["fg3"]))
            self.tr.item(r, 0).setData(Qt.ItemDataRole.UserRole, t["id"])
        self.tr.itemSelectionChanged.connect(self._transition_selected)

        self.tabs = tabs = QTabWidget(); tabs.addTab(self.st, f"Statuses ({len(wf['statuses'])})")
        tabs.addTab(self.tr, f"Transitions ({len(wf['transitions'])})")
        tabs.setDocumentMode(True)

        split = QSplitter(); split.addWidget(chart); split.addWidget(tabs)
        split.setChildrenCollapsible(False); split.setStretchFactor(0, 3); split.setStretchFactor(1, 1)
        split.setSizes([640, 760])
        v = QVBoxLayout(self); v.setContentsMargins(20, 16, 20, 20)
        v.addLayout(header); v.addWidget(info); v.addWidget(split, 1)

    def _status_selected(self):
        r = self.st.currentRow() if self.st.selectedItems() else -1
        if r >= 0:
            self.chart.lock("node", self.st.item(r, 0).data(Qt.ItemDataRole.UserRole))

    def _transition_selected(self):
        r = self.tr.currentRow() if self.tr.selectedItems() else -1
        if r >= 0:
            self.chart.lock("edge", self.tr.item(r, 0).data(Qt.ItemDataRole.UserRole))

    def _chart_selected(self, kind, ident):
        """The chart was clicked (or reset): show the same status / transition in the matching tab."""
        tables = (self.st, self.tr)
        for t in tables:
            t.blockSignals(True); t.clearSelection()
        if kind:
            table, tab = (self.st, 0) if kind == "node" else (self.tr, 1)
            self.tabs.setCurrentIndex(tab)
            for r in range(table.rowCount()):
                if table.item(r, 0).data(Qt.ItemDataRole.UserRole) == ident:
                    table.selectRow(r); table.setCurrentCell(r, 0)
                    table.scrollToItem(table.item(r, 0), QAbstractItemView.ScrollHint.PositionAtCenter)
                    break
        for t in tables:
            t.blockSignals(False)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.chart.locked is not None:
            self.chart.reset_view()  # first Esc lets go of the selection, the next one closes the window
        else:
            super().keyPressEvent(e)

    def save_png(self):
        safe = re.sub(r"[^\w.-]+", "_", self.wf["name"])
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Save flowchart"), str(Path.home() / f"{safe}.png"), "PNG image (*.png)")
        if path:
            self.chart.save_png(path)

    def showEvent(self, e):
        super().showEvent(e)
        self.chart.fit()


# --------------------------------------------------------------------------- workflows
W_COLS = ["", "WORKFLOW", "SPACE", "WORKFLOW SCHEME", "LAST UPDATED", "STATUSES", "PROPERTIES", "TRANSITIONS", "SCREENS", "STATE", ""]
W_KEYS = [lambda w: 0, lambda w: w["name"].lower(), lambda w: ", ".join(w["project_keys"]).lower(),
          lambda w: ", ".join(s["name"] for s in w["schemes"]).lower(), lambda w: w["updated"],
          lambda w: len(w["statuses"]), lambda w: w["property_count"], lambda w: len(w["transitions"]), lambda w: len(w["screens"]),
          lambda w: w["active"], lambda w: 0]


class WorkflowsPage(QWidget):
    # the steps of reading a site, in order; the names are the messages the loading job reports (the login page lists them)
    STEPS = ["Reading spaces", "Reading statuses", "Reading resolutions", "Reading workflow schemes", "Reading screens and work types",
             "Reading workflow scheme assignments", "Reading workflows", "Saving a local copy"]
    loaded = pyqtSignal(dict)
    load_started = pyqtSignal(list)
    load_progress = pyqtSignal(str, int, int)   # step name, done, total
    load_failed = pyqtSignal(str)
    template_requested = pyqtSignal(list)
    job_requested = pyqtSignal(list)   # the ticked workflows (maybe none: the Job page can add spaces)

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.client, self.catalog, self.shown, self.filtered = None, None, [], []   # shown = the rows of the current page
        self.sort, self.checked = (1, True), set()  # checked = names of the selected workflows
        self.page, self.page_size = 0, 50

        top = QHBoxLayout()
        top.addLayout(page_header("Workflows", "Browse the workflows of your Jira site. Tick workflows to review the page template, create a job or delete inactive ones."), 1)

        self.state_active, self.state_inactive = QRadioButton("Active"), QRadioButton("Inactive")
        self.state_active.setChecked(True)
        self.state_group = QButtonGroup(self)
        for b in (self.state_active, self.state_inactive):
            self.state_group.addButton(b)
        self.space = QComboBox(); self.space.setMinimumWidth(240)
        self.search = QLineEdit(placeholderText="Search workflow or scheme"); self.search.setClearButtonEnabled(True)
        self.refresh_btn = button("Fetch workflows", primary=True); self.refresh_btn.clicked.connect(self.fetch)
        self.csv_btn = button("Export CSV"); self.csv_btn.clicked.connect(self.export_csv); self.csv_btn.setEnabled(False)
        filt = QHBoxLayout(); filt.setSpacing(10)
        filt.addWidget(label("Status", "field")); filt.addWidget(self.state_active); filt.addWidget(self.state_inactive)
        filt.addSpacing(8)
        filt.addWidget(label("Space", "field")); filt.addWidget(self.space)
        self.delete_btn = button("Delete"); self.delete_btn.setProperty("danger", True); self.delete_btn.setMinimumWidth(116)  # room for "Delete (12)"
        self.delete_btn.clicked.connect(self.delete_selected); self.delete_btn.setEnabled(False)
        filt.addWidget(self.search, 1); filt.addWidget(self.delete_btn); filt.addWidget(self.csv_btn); filt.addWidget(self.refresh_btn)

        self.table = AutoFitTable(W_COLS, fixed={0: 56, 10: 104}, numeric=(5, 6, 7, 8))
        self.table.horizontalHeader().sectionClicked.connect(self._sort)
        self.table.horizontalHeader().setSortIndicatorShown(True)
        self.table.horizontalHeader().setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        self.table.cellDoubleClicked.connect(lambda r, c: self.view(r) if c else None)
        header = self.table.horizontalHeader()
        self.select_all = QCheckBox(header); self.select_all.setTristate(True); self.select_all.setToolTip("Select all workflows")
        self.select_all.clicked.connect(self._toggle_all)
        header.sectionResized.connect(lambda *_: self._place_select_all())
        header.geometriesChanged.connect(self._place_select_all)

        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.hide()
        self.status = label("Sign in to load workflows.", "caption")
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        # bottom bar: what is shown, paging, and the blue Review Template button
        self.size_combo = QComboBox()
        for n in (25, 50, 100, 200):
            self.size_combo.addItem(str(n), n)
        self.size_combo.setCurrentIndex(1)
        self.prev_btn, self.next_btn = button("‹  Previous", subtle=True), button("Next  ›", subtle=True)
        self.page_lbl = label("Page 1 of 1", "caption"); self.page_lbl.setMinimumWidth(80)
        self.review_btn = button("Review Template", primary=True); self.review_btn.setMinimumWidth(150); self.review_btn.setEnabled(False)
        self.review_btn.clicked.connect(self.review_template)
        self.job_btn = button("Create Job"); self.job_btn.setMinimumWidth(130); self.job_btn.setEnabled(False)
        self.job_btn.setToolTip("Turn the ticked workflows into a job that a script can repeat on a server (spaces can be added on the Job page)")
        self.job_btn.clicked.connect(self.create_job)
        bottom = QHBoxLayout(); bottom.setSpacing(8)
        bottom.addWidget(self.status, 1)
        bottom.addWidget(label("Rows per page", "caption")); bottom.addWidget(self.size_combo)
        bottom.addSpacing(8); bottom.addWidget(self.prev_btn); bottom.addWidget(self.page_lbl); bottom.addWidget(self.next_btn)
        bottom.addSpacing(16); bottom.addWidget(self.job_btn); bottom.addWidget(self.review_btn)
        v.addLayout(top); v.addLayout(filt); v.addWidget(self.progress); v.addWidget(self.table, 1); v.addLayout(bottom)

        self.state_group.buttonToggled.connect(self._state_changed)
        self.space.currentIndexChanged.connect(lambda _: self._filter_changed())
        self.search.textChanged.connect(lambda _: self._filter_changed())
        self.size_combo.currentIndexChanged.connect(lambda _: self._size_changed())
        self.prev_btn.clicked.connect(lambda: self._step(-1))
        self.next_btn.clicked.connect(lambda: self._step(1))

    # ---- loading ----
    def start(self, client, me):
        self.client = client
        self.me = me
        self.fetch()

    def reset(self):
        self.client, self.catalog, self.shown, self.filtered, self.page = None, None, [], [], 0
        self.table.setRowCount(0); self.csv_btn.setEnabled(False); self.checked.clear(); self._sync_selection()
        self.table.empty_text = "Sign in to load your workflows."
        self.status.setText("Sign in to load workflows.")

    def fetch(self):
        if not self.client:
            return
        self.refresh_btn.setEnabled(False); self.progress.setRange(0, 0); self.progress.show()
        self.status.setText("Fetching workflows…")
        self.load_started.emit(self.STEPS)
        client = self.client

        def job(progress):
            progress(0, 0, "Reading spaces")
            projects = client.projects()
            progress(0, 0, "Reading statuses")
            statuses = client.statuses()
            progress(0, 0, "Reading resolutions")
            resolutions = client.resolutions()
            progress(0, 0, "Reading workflow schemes")
            schemes = client.workflow_schemes()
            progress(0, 0, "Reading screens and work types")
            screens = client.screens()
            work_types = client.issue_types()
            progress(0, 0, "Reading workflow scheme assignments")
            assigned = client.project_schemes([p["id"] for p in projects], progress)
            progress(0, 0, "Reading workflows")
            workflows, wf_statuses = client.workflows(progress)
            progress(0, 0, "Saving a local copy")
            self._save_cache(client, {"projects": projects, "statuses": statuses, "workflow_schemes": schemes,
                                      "project_scheme_assignments": assigned, "workflows": workflows,
                                      "workflow_statuses": wf_statuses, "screens": screens, "issue_types": work_types, "resolutions": resolutions})
            return build_catalog(workflows, statuses, wf_statuses, schemes, assigned, projects, screens, work_types, resolutions)

        self.w = Worker(job)
        self.w.progress.connect(self._progress)
        self.w.done.connect(self._loaded); self.w.failed.connect(self._failed)
        self.w.start()

    @staticmethod
    def _save_cache(client, payloads):
        """Keep the raw REST responses as JSON files on disk (one folder per site)."""
        d = CACHE_ROOT / urlparse(client.site).netloc
        try:
            d.mkdir(parents=True, exist_ok=True)
            for name, data in payloads.items():
                (d / f"{name}.json").write_text(json.dumps(data, indent=2), "utf-8")
        except OSError:
            pass

    def _progress(self, a, b, msg):
        if b:
            self.progress.setRange(0, b); self.progress.setValue(a)
        self.status.setText(f"{msg}…")
        if msg in self.STEPS:
            self.load_progress.emit(msg, a, b)

    def _failed(self, msg):
        self.refresh_btn.setEnabled(True); self.progress.hide(); self.status.setText(msg)
        self.load_failed.emit(msg)

    def _loaded(self, catalog):
        self.refresh_btn.setEnabled(True); self.progress.hide(); self.csv_btn.setEnabled(True)
        self.catalog = catalog
        self.checked &= {w["name"] for w in catalog["workflows"]}
        current = self.space.currentData()
        self.space.blockSignals(True); self.space.clear(); self.space.addItem("All spaces", None)
        for s in catalog["spaces"]:
            self.space.addItem(f"{s['key']} — {s['name']}", s["key"])
        i = self.space.findData(current)
        self.space.setCurrentIndex(max(i, 0)); self.space.blockSignals(False)
        self.apply_filter()
        self.loaded.emit(catalog)

    # ---- table ----
    def apply_filter(self):
        if not self.catalog:
            return
        want_active, key, q = self.state_active.isChecked(), self.space.currentData(), self.search.text().strip().lower()
        rows = []
        for w in self.catalog["workflows"]:
            if w["active"] != want_active:
                continue
            if key and key not in w["project_keys"]:
                continue
            if q and q not in w["name"].lower() and not any(q in s["name"].lower() for s in w["schemes"]):
                continue
            rows.append(w)
        self.filtered = sort_rows(rows, W_KEYS, *self.sort)
        pages = max(1, -(-len(self.filtered) // self.page_size))
        self.page = min(self.page, pages - 1)
        first = self.page * self.page_size
        self.shown = self.filtered[first:first + self.page_size]
        self.page_lbl.setText(f"Page {self.page + 1} of {pages}")
        self.prev_btn.setEnabled(self.page > 0); self.next_btn.setEnabled(self.page < pages - 1)
        site = self.client.site if self.client else ""
        self.table.setRowCount(0)
        self.table.empty_text = "No workflows match the current filters. Change the Status, the Space or the search text."
        for w in self.shown:
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setCellWidget(r, 0, self._checkbox(w))
            self.table.setCellWidget(r, 1, link(w["name"], workflow_edit_url(site, w), "Edit this workflow in Jira"))
            keys = w["project_keys"]
            self.table.setItem(r, 2, cell(", ".join(keys[:3]) + (f" +{len(keys) - 3}" if len(keys) > 3 else "") or "—",
                                          tip=", ".join(keys)))
            if w["schemes"]:
                self.table.setCellWidget(r, 3, LinkLabel([(sc["name"], scheme_edit_url(site, sc["id"])) for sc in w["schemes"]],
                                                         "Edit this workflow scheme in Jira"))
            else:
                self.table.setItem(r, 3, cell("—", color=T["fg3"]))
            self.table.setItem(r, 4, cell(fmt_date(w["updated"])))
            self.table.setItem(r, 5, cell(len(w["statuses"]), True))
            props = [f"{s['name']}: {k}" + (f" = {v}" if v != "" else "") for s in w["statuses"] for k, v in sorted(s["properties"].items())]
            self.table.setItem(r, 6, cell(w["property_count"], True, tip="\n".join(props[:25]) + (f"\n… +{len(props) - 25} more" if len(props) > 25 else "")
                                          or "No status properties"))
            self.table.setItem(r, 7, cell(len(w["transitions"]), True))
            self.table.setItem(r, 8, cell(len(w["screens"]), True, tip="\n".join(n for _, n in w["screens"]) or "No transition screens"))
            self.table.setItem(r, 9, cell("Active" if w["active"] else "Inactive",
                                          color=T["success"] if w["active"] else T["fg3"]))
            b = button("View", subtle=True); b.setToolTip("Show the flowchart"); b.clicked.connect(lambda _, r=r: self.view(r))
            self.table.setCellWidget(r, 10, padded(b))
        self.table.refit()
        self._sync_selection(); self._place_select_all()
        n = len(self.catalog["workflows"])
        self.status.setText((f"{first + 1}–{first + len(self.shown)} of {len(self.filtered)}" if self.shown else "0")
                            + f" matching · {n} workflows in total · raw JSON saved in "
                            f"{CACHE_ROOT / urlparse(self.client.site).netloc}" if self.client else "")

    # ---- paging ----
    def _filter_changed(self):
        self.page = 0
        self.apply_filter()

    def _size_changed(self):
        self.page_size, self.page = self.size_combo.currentData(), 0
        self.apply_filter()

    def _step(self, d):
        self.page += d
        self.apply_filter()

    def _sort(self, col):
        if col in (0, len(W_COLS) - 1):
            return
        self.page = 0
        self.sort = (col, not self.sort[1] if self.sort[0] == col else True)
        self.table.horizontalHeader().setSortIndicator(col, Qt.SortOrder.AscendingOrder if self.sort[1] else Qt.SortOrder.DescendingOrder)
        self.apply_filter()

    # ---- selection and deletion ----
    def _checkbox(self, w):
        box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox(); cb.setChecked(w["name"] in self.checked)
        cb.toggled.connect(lambda on, name=w["name"]: self._set_checked(name, on))
        bl.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)
        return box

    def _set_checked(self, name, on):
        (self.checked.add if on else self.checked.discard)(name)
        self._sync_selection()

    def _toggle_all(self):
        names = {w["name"] for w in self.shown}
        self.checked = self.checked - names if names <= self.checked else self.checked | names
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
            cb.blockSignals(True); cb.setChecked(self.shown[r]["name"] in self.checked); cb.blockSignals(False)
        self._sync_selection()

    def _sync_selection(self):
        names = {w["name"] for w in self.shown}
        n = len(names & self.checked)
        self.select_all.setCheckState(Qt.CheckState.Unchecked if not n else
                                      Qt.CheckState.Checked if n == len(names) else Qt.CheckState.PartiallyChecked)
        self.delete_btn.setText(f"Delete ({len(self.checked)})" if self.checked else "Delete")
        self.review_btn.setEnabled(bool(self.checked))
        self.job_btn.setEnabled(self.catalog is not None)
        self.review_btn.setToolTip("Open the Confluence page template for the selected workflows" if self.checked
                                   else "Tick one or more workflows to review the Confluence page template")
        inactive = self.state_inactive.isChecked()
        self.delete_btn.setEnabled(bool(self.checked) and self.client is not None and inactive)
        self.delete_btn.setToolTip("Delete the selected inactive workflows" if inactive
                                   else "Switch Status to Inactive to delete workflows (active workflows cannot be deleted)")

    def _place_select_all(self):
        h = self.table.horizontalHeader()
        s = self.select_all.sizeHint()
        self.select_all.move(h.sectionViewportPosition(0) + (h.sectionSize(0) - s.width()) // 2, (h.height() - s.height()) // 2)

    def _state_changed(self, *_):
        self.checked.clear()  # a selection never mixes active and inactive workflows
        self.page = 0
        self.apply_filter()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_select_all()

    def set_checked(self, names):
        """Tick exactly these workflows (the Job page sends its selection here)."""
        self.checked = set(names) & {w["name"] for w in self.catalog["workflows"]} if self.catalog else set(names)
        self.apply_filter()

    def create_job(self):
        self.job_requested.emit([w for w in self.catalog["workflows"] if w["name"] in self.checked] if self.catalog else [])

    def review_template(self):
        chosen = [w for w in self.catalog["workflows"] if w["name"] in self.checked]
        if chosen:
            self.template_requested.emit(chosen)

    def delete_selected(self):
        chosen = [w for w in self.catalog["workflows"] if w["name"] in self.checked]
        active = [w["name"] for w in chosen if w["active"]]

        def bullets(names):
            return "\n".join(f"• {n}" for n in names[:15]) + (f"\n… and {len(names) - 15} more" if len(names) > 15 else "")

        if active:
            QMessageBox.warning(self, titlecase("Active workflows selected"),
                                "Only inactive workflows can be deleted. Unselect these active workflows first:\n\n" + bullets(active))
            return
        box = QMessageBox(QMessageBox.Icon.Warning, titlecase("Delete workflows"),
                          f"Permanently delete {len(chosen)} inactive workflow(s)?\n\n" + bullets([w["name"] for w in chosen]),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText("This cannot be undone.")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.delete_btn.setEnabled(False); self.refresh_btn.setEnabled(False)
        client, me = self.client, getattr(self, "me", {})
        who = f"{me.get('displayName', '')} <{me.get('emailAddress') or client.email}>".strip()

        def job(progress):
            failed = []
            for i, w in enumerate(chosen):
                progress(i, len(chosen), f"Deleting {w['name']}")
                try:
                    client.delete_workflow(w)
                    log_change(client.site, who, "Delete workflow", w["name"], w["id"], "Success", "Deleted inactive workflow")
                except JiraError as e:
                    failed.append(f"{w['name']}: {e}")
                    log_change(client.site, who, "Delete workflow", w["name"], w["id"], "Failed", str(e))
            return len(chosen) - len(failed), failed

        self.progress.setRange(0, len(chosen)); self.progress.show()
        self.w = Worker(job)
        self.w.progress.connect(self._progress); self.w.failed.connect(self._failed)
        self.w.done.connect(self._deleted); self.w.start()

    def _deleted(self, result):
        ok, failed = result
        self.checked.clear(); self.progress.hide()
        if failed:
            QMessageBox.warning(self, titlecase("Some workflows were not deleted"), f"{ok} deleted, {len(failed)} failed:\n\n" + "\n".join(failed[:10]))
        self.fetch()

    def view(self, row):
        if 0 <= row < len(self.shown):
            WorkflowViewer(self.shown[row], self.client.site, self.window()).exec()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Export workflows"), str(Path.home() / "jira_workflows.csv"), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["Workflow", "Workflow ID", "Spaces", "Workflow schemes", "Last updated", "Statuses", "Status properties", "Transitions", "Transition screens", "State"])
            for w in self.filtered:   # every workflow that matches the filters, not only the current page
                wr.writerow([w["name"], w["id"], ", ".join(w["project_keys"]), ", ".join(s["name"] for s in w["schemes"]),
                             w["updated"], len(w["statuses"]), w["property_count"], len(w["transitions"]), len(w["screens"]), "Active" if w["active"] else "Inactive"])
        self.status.setText(f"Exported {len(self.filtered)} workflows to {path}")


# --------------------------------------------------------------------------- workflow schemes
SC_COLS = ["", "WORKFLOW SCHEME", "SPACES", "WORK TYPES", "WORKFLOWS", "STATE"]
SC_KEYS = [lambda s: 0, lambda s: s["name"].lower(), lambda s: ", ".join(s["project_keys"]).lower(),
           lambda s: sum(len(r["work_types"]) for r in s["rows"]), lambda s: len(s["rows"]), lambda s: s["active"]]


class SchemesPage(QWidget):
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.client, self.me, self.catalog, self.shown = None, {}, None, []
        self.sort, self.checked = (1, True), set()

        top = QHBoxLayout()
        top.addLayout(page_header("Workflow schemes", "The schemes that connect workflows to work types in each space. Inactive schemes are used by no space and can be deleted."), 1)

        self.state_active, self.state_inactive = QRadioButton("Active"), QRadioButton("Inactive")
        self.state_active.setChecked(True)
        self.state_group = QButtonGroup(self)
        for b in (self.state_active, self.state_inactive):
            self.state_group.addButton(b)
        self.space = QComboBox(); self.space.setMinimumWidth(240)
        self.search = QLineEdit(placeholderText="Search scheme, workflow or space"); self.search.setClearButtonEnabled(True)
        self.refresh_btn = button("Fetch schemes", primary=True); self.refresh_btn.clicked.connect(self.refresh_requested)
        self.csv_btn = button("Export CSV"); self.csv_btn.clicked.connect(self.export_csv); self.csv_btn.setEnabled(False)
        self.delete_btn = button("Delete"); self.delete_btn.setProperty("danger", True); self.delete_btn.setMinimumWidth(116)  # room for "Delete (12)"
        self.delete_btn.clicked.connect(self.delete_selected); self.delete_btn.setEnabled(False)
        filt = QHBoxLayout(); filt.setSpacing(10)
        filt.addWidget(label("Status", "field")); filt.addWidget(self.state_active); filt.addWidget(self.state_inactive)
        filt.addSpacing(8)
        filt.addWidget(label("Space", "field")); filt.addWidget(self.space)
        filt.addWidget(self.search, 1); filt.addWidget(self.delete_btn); filt.addWidget(self.csv_btn); filt.addWidget(self.refresh_btn)

        self.table = AutoFitTable(SC_COLS, fixed={0: 56})
        header = self.table.horizontalHeader()
        header.sectionClicked.connect(self._sort)
        header.setSortIndicatorShown(True); header.setSortIndicator(1, Qt.SortOrder.AscendingOrder)
        self.select_all = QCheckBox(header); self.select_all.setTristate(True); self.select_all.setToolTip("Select all workflow schemes")
        self.select_all.clicked.connect(self._toggle_all)
        header.sectionResized.connect(lambda *_: self._place_select_all())
        header.geometriesChanged.connect(self._place_select_all)

        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.hide()
        self.status = label("Sign in to load workflow schemes.", "caption")
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        v.addLayout(top); v.addLayout(filt); v.addWidget(self.progress); v.addWidget(self.table, 1); v.addWidget(self.status)

        self.state_group.buttonToggled.connect(self._state_changed)
        self.space.currentIndexChanged.connect(lambda _: self.apply_filter())
        self.search.textChanged.connect(lambda _: self.apply_filter())

    # ---- data ----
    def start(self, client, me):
        self.client, self.me = client, me

    def reset(self):
        self.client, self.catalog, self.shown = None, None, []
        self.checked.clear(); self.table.setRowCount(0); self.csv_btn.setEnabled(False); self._sync_selection()
        self.table.empty_text = "Sign in to load your workflow schemes."
        self.status.setText("Sign in to load workflow schemes.")

    def set_catalog(self, catalog):
        self.catalog = catalog
        self.checked &= {s["id"] for s in catalog["schemes"]}
        current = self.space.currentData()
        self.space.blockSignals(True); self.space.clear(); self.space.addItem("All spaces", None)
        for s in catalog["spaces"]:
            self.space.addItem(f"{s['key']} — {s['name']}", s["key"])
        self.space.setCurrentIndex(max(self.space.findData(current), 0)); self.space.blockSignals(False)
        self.refresh_btn.setEnabled(True); self.csv_btn.setEnabled(True)
        self.apply_filter()

    # ---- table ----
    def apply_filter(self):
        if not self.catalog:
            return
        want_active, key, q = self.state_active.isChecked(), self.space.currentData(), self.search.text().strip().lower()
        rows = []
        for s in self.catalog["schemes"]:
            if s["active"] != want_active or (key and key not in s["project_keys"]):
                continue
            hay = " ".join([s["name"], *s["project_keys"], *(r["workflow"] for r in s["rows"])]).lower()
            if q and q not in hay:
                continue
            rows.append(s)
        self.shown = sort_rows(rows, SC_KEYS, *self.sort)
        site = self.client.site if self.client else ""
        by_name = {w["name"]: w for w in self.catalog["workflows"]}
        self.table.setRowCount(0)
        self.table.empty_text = "No workflow schemes match the current filters."
        for s in self.shown:
            r = self.table.rowCount(); self.table.insertRow(r)
            self.table.setCellWidget(r, 0, self._checkbox(s))
            self.table.setCellWidget(r, 1, link(s["name"], scheme_edit_url(site, s["id"]), "Edit this workflow scheme in Jira"))
            keys = s["project_keys"]
            self.table.setItem(r, 2, cell(", ".join(keys[:3]) + (f" +{len(keys) - 3}" if len(keys) > 3 else "") or "—", tip=", ".join(keys)))
            types = [", ".join(row["work_types"]) for row in s["rows"]]
            # work types and workflows are listed line by line in the same order, so each line pairs a workflow with its work types
            t_lbl = QLabel("<br>".join(html.escape(t) for t in types)); t_lbl.setWordWrap(True)
            t_lbl.setProperty("lines", types)
            t_lbl.setToolTip("\n".join(f"{row['workflow']}: {t}" for row, t in zip(s["rows"], types)))
            w_lbl = LinkLabel([(row["workflow"], workflow_edit_url(site, by_name.get(row["workflow"], {"name": row["workflow"]})))
                               for row in s["rows"]], "Open the workflow in Jira", sep="<br>")
            self.table.setCellWidget(r, 3, t_lbl); self.table.setCellWidget(r, 4, w_lbl)
            self.table.setItem(r, 5, cell("Active" if s["active"] else "Inactive", color=T["success"] if s["active"] else T["fg3"]))
        self.table.refit()
        self._sync_selection(); self._place_select_all()
        self.status.setText(f"{len(self.shown)} of {len(self.catalog['schemes'])} workflow schemes" if self.client else "")

    def _sort(self, col):
        if col == 0:
            return
        self.sort = (col, not self.sort[1] if self.sort[0] == col else True)
        self.table.horizontalHeader().setSortIndicator(col, Qt.SortOrder.AscendingOrder if self.sort[1] else Qt.SortOrder.DescendingOrder)
        self.apply_filter()

    # ---- selection and deletion ----
    def _checkbox(self, s):
        box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
        cb = QCheckBox(); cb.setChecked(s["id"] in self.checked)
        cb.toggled.connect(lambda on, sid=s["id"]: self._set_checked(sid, on))
        bl.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)
        return box

    def _set_checked(self, sid, on):
        (self.checked.add if on else self.checked.discard)(sid)
        self._sync_selection()

    def _toggle_all(self):
        ids = {s["id"] for s in self.shown}
        self.checked = self.checked - ids if ids <= self.checked else self.checked | ids
        for r in range(self.table.rowCount()):
            cb = self.table.cellWidget(r, 0).findChild(QCheckBox)
            cb.blockSignals(True); cb.setChecked(self.shown[r]["id"] in self.checked); cb.blockSignals(False)
        self._sync_selection()

    def _sync_selection(self):
        ids = {s["id"] for s in self.shown}
        n = len(ids & self.checked)
        self.select_all.setCheckState(Qt.CheckState.Unchecked if not n else
                                      Qt.CheckState.Checked if n == len(ids) else Qt.CheckState.PartiallyChecked)
        self.delete_btn.setText(f"Delete ({len(self.checked)})" if self.checked else "Delete")
        inactive = self.state_inactive.isChecked()
        self.delete_btn.setEnabled(bool(self.checked) and self.client is not None and inactive)
        self.delete_btn.setToolTip("Delete the selected inactive workflow schemes" if inactive
                                   else "Switch Status to Inactive to delete workflow schemes (active schemes cannot be deleted)")

    def _place_select_all(self):
        h = self.table.horizontalHeader()
        sz = self.select_all.sizeHint()
        self.select_all.move(h.sectionViewportPosition(0) + (h.sectionSize(0) - sz.width()) // 2, (h.height() - sz.height()) // 2)

    def _state_changed(self, *_):
        self.checked.clear()  # a selection never mixes active and inactive schemes
        self.apply_filter()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_select_all()

    def delete_selected(self):
        chosen = [s for s in self.catalog["schemes"] if s["id"] in self.checked]
        active = [s["name"] for s in chosen if s["active"]]

        def bullets(names):
            return "\n".join(f"• {n}" for n in names[:15]) + (f"\n… and {len(names) - 15} more" if len(names) > 15 else "")

        if active:
            QMessageBox.warning(self, titlecase("Active workflow schemes selected"),
                                "Only inactive workflow schemes can be deleted. Unselect these active schemes first:\n\n" + bullets(active))
            return
        box = QMessageBox(QMessageBox.Icon.Warning, titlecase("Delete workflow schemes"),
                          f"Permanently delete {len(chosen)} inactive workflow scheme(s)?\n\n" + bullets([s["name"] for s in chosen]),
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.setInformativeText("This cannot be undone. The workflows used by the schemes are not deleted.")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        self.delete_btn.setEnabled(False); self.refresh_btn.setEnabled(False)
        client, me = self.client, self.me
        who = f"{me.get('displayName', '')} <{me.get('emailAddress') or client.email}>".strip()

        def job(progress):
            failed = []
            for i, s in enumerate(chosen):
                progress(i, len(chosen), f"Deleting {s['name']}")
                try:
                    client.delete_workflow_scheme(s)
                    log_change(client.site, who, "Delete workflow scheme", s["name"], s["id"], "Success", "Deleted inactive workflow scheme")
                except JiraError as e:
                    failed.append(f"{s['name']}: {e}")
                    log_change(client.site, who, "Delete workflow scheme", s["name"], s["id"], "Failed", str(e))
            return len(chosen) - len(failed), failed

        self.progress.setRange(0, len(chosen)); self.progress.show()
        self.w = Worker(job)
        self.w.progress.connect(lambda a, b, m: self.status.setText(f"{m}…"))
        self.w.failed.connect(self._delete_failed); self.w.done.connect(self._deleted); self.w.start()

    def _delete_failed(self, msg):
        self.progress.hide(); self.refresh_btn.setEnabled(True); self.status.setText(msg)

    def _deleted(self, result):
        ok, failed = result
        self.checked.clear(); self.progress.hide()
        if failed:
            QMessageBox.warning(self, titlecase("Some workflow schemes were not deleted"),
                                f"{ok} deleted, {len(failed)} failed:\n\n" + "\n".join(failed[:10]))
        self.refresh_requested.emit()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Export workflow schemes"), str(Path.home() / "jira_workflow_schemes.csv"), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            wr = csv.writer(f)
            wr.writerow(["Workflow scheme", "Scheme ID", "Spaces", "Workflows", "Work types by workflow", "State"])
            for s in self.shown:
                wr.writerow([s["name"], s["id"], ", ".join(s["project_keys"]), "; ".join(r["workflow"] for r in s["rows"]),
                             "; ".join(f"{r['workflow']}: {', '.join(r['work_types'])}" for r in s["rows"]),
                             "Active" if s["active"] else "Inactive"])
        self.status.setText(f"Exported {len(self.shown)} workflow schemes to {path}")


# --------------------------------------------------------------------------- statuses
S_COLS = ["", "STATUS", "STATUS ID", "CATEGORY", "MANAGED BY", "ASSOCIATED WORKFLOWS", "ASSOCIATED SPACES"]   # expand arrow, ...
S_KEYS = [lambda s: 0, lambda s: s["name"].lower(), lambda s: int(s["id"]) if s["id"].isdigit() else 0,
          lambda s: CATEGORIES[s["category"]][1], lambda s: s["scope"], lambda s: len(s["workflows"]), lambda s: len(s["spaces"])]


TAG_COLORS = {"JSM": "success", "Default": "danger"}   # theme token of a tag (the others are blue)
TAG_TIPS = {"Built-in": "Created by default in every new Jira site", "JSM": "Created by Jira Service Management",
            "Default": "The default resolution: Jira sets it when none is chosen"}


def status_name_cell(st):
    """The status name with its small tags (Built-in, JSM) next to it."""
    box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(12, 0, 8, 0); bl.setSpacing(8)
    name = QLabel(st["name"]); name.setWordWrap(False); bl.addWidget(name)
    for t in st.get("tags", []):
        pill = tag(t, TAG_COLORS.get(t, "brand_fg"))
        pill.setToolTip(TAG_TIPS.get(t, ""))
        bl.addWidget(pill, alignment=Qt.AlignmentFlag.AlignVCenter)
    bl.addStretch()
    box.setProperty("fit", True)   # AutoFitTable gives the column the width this widget needs: the name is never wrapped or cut
    return box


def status_detail(st, what="status"):
    """The expanded part of a status (or resolution) row: its spaces, and its workflows with the spaces each of them is used in."""
    lab = QLabel(); lab.setTextFormat(Qt.TextFormat.RichText); lab.setWordWrap(True)
    lab.setProperty("detail", True)
    lab.setContentsMargins(56, 8, 16, 12)
    wf = st["workflows"]
    rows = "".join(f"<tr><td style='padding-right:28px'>{html.escape(n)}</td><td>{html.escape(', '.join(st['wf_spaces'].get(n) or []) or '—')}</td></tr>"
                   for n in wf[:100])
    more = f"<p>… and {len(wf) - 100} more workflows</p>" if len(wf) > 100 else ""
    spaces = html.escape(", ".join(st["spaces"]) or "—")
    body = (f"<p><b>Associated spaces ({len(st['spaces'])})</b>: {spaces}</p>"
            + (f"<p><b>Associated workflows ({len(wf)})</b></p><table cellspacing='2'><tr><td style='padding-right:28px'><i>Workflow</i></td>"
               f"<td><i>Spaces</i></td></tr>{rows}</table>{more}" if wf else f"<p>No workflow uses this {what}.</p>"))
    lab.setText(body)
    return lab


class StatusesPage(QWidget):
    """Every status of the site with its tags, and the workflows and spaces that use it (a row opens to list them). Read only."""
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.catalog, self.shown, self.sort = None, [], (2, True)   # by status ID, ascending
        top = QHBoxLayout()
        top.addLayout(page_header("Statuses", "Every status of your site. Open a row to see the workflows and spaces that use it."), 1)
        self.category = QComboBox(); self.category.addItems(["All categories"] + [c[0] for c in CATEGORIES.values()])
        self.state_active, self.state_inactive = QRadioButton("Active"), QRadioButton("Inactive")
        self.state_active.setChecked(True)   # active: used by at least one workflow; inactive: used by none
        self.state_active.setToolTip("Statuses used by one or more workflows")
        self.state_inactive.setToolTip("Statuses that no workflow uses")
        self.state_group = QButtonGroup(self)
        for b in (self.state_active, self.state_inactive):
            self.state_group.addButton(b)
        self.managed_company, self.managed_team = QRadioButton("Company-managed"), QRadioButton("Team-managed")
        self.managed_company.setChecked(True)
        self.managed_group = QButtonGroup(self)
        for b in (self.managed_company, self.managed_team):
            self.managed_group.addButton(b)
        self.managed_company.setToolTip("Statuses shared by the whole site")
        self.managed_team.setToolTip("Statuses that belong to one space")
        self.search = QLineEdit(placeholderText="Search status name or ID"); self.search.setClearButtonEnabled(True)
        refresh = button("Refresh", primary=True); refresh.clicked.connect(self.refresh_requested)
        filt = QHBoxLayout(); filt.setSpacing(10)
        filt.addWidget(label("Status", "field")); filt.addWidget(self.state_active); filt.addWidget(self.state_inactive)
        filt.addSpacing(8)
        filt.addWidget(label("Category", "field")); filt.addWidget(self.category)
        filt.addSpacing(8)
        filt.addWidget(label("Managed by", "field"))
        for b in (self.managed_company, self.managed_team):
            filt.addWidget(b)
        filt.addWidget(self.search, 1); filt.addWidget(refresh)

        self.table = AutoFitTable(S_COLS, fixed={0: 40}, numeric=(5, 6))
        self.expanded = set()   # IDs of the statuses whose row is open
        header = self.table.horizontalHeader()
        header.sectionClicked.connect(self._sort)
        header.setSortIndicatorShown(True); header.setSortIndicator(2, Qt.SortOrder.AscendingOrder)
        self.table.cellDoubleClicked.connect(lambda r, c: self._toggle_row(self._sid(r)) if self._sid(r) else None)
        self.status = label("Sign in to load statuses.", "caption")
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        v.addLayout(top); v.addLayout(filt); v.addWidget(self.table, 1); v.addWidget(self.status)
        self.category.currentIndexChanged.connect(lambda _: self.fill())
        self.managed_group.buttonToggled.connect(lambda *_: self.fill())
        self.state_group.buttonToggled.connect(lambda *_: self.fill())
        self.search.textChanged.connect(lambda _: self.fill())

    def set_catalog(self, catalog):
        self.catalog = catalog
        self.fill()

    def reset(self):
        self.catalog, self.shown = None, []
        self.table.setRowCount(0); self.status.setText("Sign in to load statuses.")
        self.table.empty_text = "Sign in to load your statuses."

    # ---- expanding a row ----
    def _sid(self, r):
        """The status ID of a status row (None for the open detail row below it)."""
        w = self.table.cellWidget(r, 0)
        return w.property("sid") if w is not None else None

    def _arrow(self, sid):
        b = QPushButton("▾" if sid in self.expanded else "▸"); b.setFlat(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setProperty("sid", sid); b.setToolTip("Show the workflows and spaces that use this status")
        b.setStyleSheet(f"QPushButton {{ border: none; background: transparent; color: {T['fg2']}; font-size: 16px; min-height: 0; padding: 0; }}"
                        f"QPushButton:hover {{ color: {T['brand_fg']}; }}")
        b.clicked.connect(lambda _=False, s=sid: self._toggle_row(s))
        return b

    def _toggle_row(self, sid):
        """Open or close the detail row under a status, keeping the rest of the table where it is."""
        r = next((r for r in range(self.table.rowCount()) if self._sid(r) == sid), None)
        st = next((s for s in self.shown if s["id"] == sid), None)
        if r is None or st is None:
            return
        if sid in self.expanded:
            self.expanded.discard(sid); self.table.removeRow(r + 1)
        else:
            self.expanded.add(sid); self._insert_detail(r + 1, st)
        self.table.setCellWidget(r, 0, self._arrow(sid))
        self.table.refit()

    def _insert_detail(self, r, st):
        self.table.insertRow(r)
        self.table.setSpan(r, 0, 1, self.table.columnCount())
        self.table.setCellWidget(r, 0, status_detail(st))

    # ---- table ----
    def fill(self):
        if not self.catalog:
            return
        cat, q = self.category.currentIndex(), self.search.text().strip().lower()
        want = list(CATEGORIES)[cat - 1] if cat else None
        team_only, active = self.managed_team.isChecked(), self.state_active.isChecked()
        rows = [s for s in self.catalog["statuses"]
                if (not want or s["category"] == want) and (s["scope"] == "PROJECT") == team_only
                and bool(s["workflows"]) == active and (not q or q in s["name"].lower() or q in s["id"])]
        self.shown = sort_rows(rows, S_KEYS, *self.sort)
        spaces = {sp["id"]: sp["key"] for sp in self.catalog["spaces"]}
        self.table.setRowCount(0)
        self.table.empty_text = "No statuses match the current filters. Change Status, Category, Managed By or the search text."
        self.expanded &= {s["id"] for s in self.shown}
        for s in self.shown:
            r = self.table.rowCount(); self.table.insertRow(r)
            wf = s["workflows"]
            self.table.setCellWidget(r, 0, self._arrow(s["id"]))
            self.table.setCellWidget(r, 1, status_name_cell(s))
            self.table.setItem(r, 2, cell(s["id"]))
            self.table.setItem(r, 3, cell(CATEGORIES[s["category"]][0], color=T[CATEGORY_TOKEN[s["category"]]]))
            team = s["scope"] == "PROJECT"
            self.table.setItem(r, 4, cell("Team-managed" + (f" · {spaces[s['project_id']]}" if s["project_id"] in spaces else "") if team
                                          else "Company-managed", tip="Belongs to one space" if team else "Shared by the whole site"))
            self.table.setItem(r, 5, cell(len(wf), True, tip="\n".join(wf[:30]) + (f"\n… +{len(wf) - 30} more" if len(wf) > 30 else "")
                                          or "Not used by any workflow"))
            sp = s["spaces"]
            self.table.setItem(r, 6, cell(len(sp), True, tip=", ".join(sp[:40]) + (f" … +{len(sp) - 40} more" if len(sp) > 40 else "")
                                          or "Not used in any space"))
            if s["id"] in self.expanded:
                self._insert_detail(r + 1, s)
        self.table.refit()
        self.status.setText(f"{len(self.shown)} of {len(self.catalog['statuses'])} statuses")

    def _sort(self, col):
        if col == 0:
            return
        self.sort = (col, not self.sort[1] if self.sort[0] == col else True)
        self.table.horizontalHeader().setSortIndicator(col, Qt.SortOrder.AscendingOrder if self.sort[1] else Qt.SortOrder.DescendingOrder)
        self.fill()


# --------------------------------------------------------------------------- resolutions
R_COLS = ["", "RESOLUTION", "RESOLUTION ID", "DESCRIPTION", "ASSOCIATED WORKFLOWS", "ASSOCIATED SPACES"]   # expand arrow, ...
R_KEYS = [lambda r: 0, lambda r: r["name"].lower(), lambda r: int(r["id"]) if r["id"].isdigit() else 0, lambda r: r["description"].lower(),
          lambda r: len(r["workflows"]), lambda r: len(r["spaces"])]


class ResolutionsPage(QWidget):
    """Every resolution of the site with its tags, and the workflows and spaces that use it (a row opens to list them)."""
    refresh_requested = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.catalog, self.shown, self.sort = None, [], (2, True)   # by resolution ID, ascending
        self.expanded = set()   # IDs of the resolutions whose row is open
        top = QHBoxLayout()
        top.addLayout(page_header("Resolutions", "The resolutions of your site. A workflow uses a resolution when one of its transitions sets it. "
                                                  "Open a row to see the workflows and spaces."), 1)
        self.use_all, self.use_used, self.use_unused = QRadioButton("All"), QRadioButton("Used"), QRadioButton("Unused")
        self.use_all.setChecked(True)
        self.use_used.setToolTip("Resolutions that a workflow sets"); self.use_unused.setToolTip("Resolutions that no workflow sets")
        self.use_group = QButtonGroup(self)
        for b in (self.use_all, self.use_used, self.use_unused):
            self.use_group.addButton(b)
        self.search = QLineEdit(placeholderText="Search resolution name, ID or description"); self.search.setClearButtonEnabled(True)
        self.csv_btn = button("Export CSV"); self.csv_btn.clicked.connect(self.export_csv); self.csv_btn.setEnabled(False)
        refresh = button("Refresh", primary=True); refresh.clicked.connect(self.refresh_requested)
        filt = QHBoxLayout(); filt.setSpacing(10)
        filt.addWidget(label("Usage", "field"))
        for b in (self.use_all, self.use_used, self.use_unused):
            filt.addWidget(b)
        filt.addWidget(self.search, 1); filt.addWidget(self.csv_btn); filt.addWidget(refresh)

        self.table = AutoFitTable(R_COLS, fixed={0: 40}, numeric=(4, 5))
        header = self.table.horizontalHeader()
        header.sectionClicked.connect(self._sort)
        header.setSortIndicatorShown(True); header.setSortIndicator(2, Qt.SortOrder.AscendingOrder)
        self.table.cellDoubleClicked.connect(lambda r, c: self._toggle_row(self._rid(r)) if self._rid(r) else None)
        self.status = label("Sign in to load resolutions.", "caption")
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        v.addLayout(top); v.addLayout(filt); v.addWidget(self.table, 1); v.addWidget(self.status)
        self.use_group.buttonToggled.connect(lambda *_: self.fill())
        self.search.textChanged.connect(lambda _: self.fill())
        self.table.empty_text = "Sign in to load your resolutions."

    def set_catalog(self, catalog):
        self.catalog = catalog
        self.csv_btn.setEnabled(True)
        self.fill()

    def reset(self):
        self.catalog, self.shown = None, []
        self.table.setRowCount(0); self.csv_btn.setEnabled(False); self.status.setText("Sign in to load resolutions.")
        self.table.empty_text = "Sign in to load your resolutions."

    # ---- expanding a row ----
    def _rid(self, r):
        w = self.table.cellWidget(r, 0)
        return w.property("sid") if w is not None else None

    def _arrow(self, rid):
        b = QPushButton("▾" if rid in self.expanded else "▸"); b.setFlat(True); b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setProperty("sid", rid); b.setToolTip("Show the workflows and spaces that use this resolution")
        b.setStyleSheet(f"QPushButton {{ border: none; background: transparent; color: {T['fg2']}; font-size: 16px; min-height: 0; padding: 0; }}"
                        f"QPushButton:hover {{ color: {T['brand_fg']}; }}")
        b.clicked.connect(lambda _=False, s=rid: self._toggle_row(s))
        return b

    def _toggle_row(self, rid):
        r = next((r for r in range(self.table.rowCount()) if self._rid(r) == rid), None)
        item = next((x for x in self.shown if x["id"] == rid), None)
        if r is None or item is None:
            return
        if rid in self.expanded:
            self.expanded.discard(rid); self.table.removeRow(r + 1)
        else:
            self.expanded.add(rid); self._insert_detail(r + 1, item)
        self.table.setCellWidget(r, 0, self._arrow(rid))
        self.table.refit()

    def _insert_detail(self, r, item):
        self.table.insertRow(r)
        self.table.setSpan(r, 0, 1, self.table.columnCount())
        self.table.setCellWidget(r, 0, status_detail(item, "resolution"))

    # ---- table ----
    def fill(self):
        if not self.catalog:
            return
        q = self.search.text().strip().lower()
        used = None if self.use_all.isChecked() else self.use_used.isChecked()
        rows = [r for r in self.catalog.get("resolutions", [])
                if (used is None or bool(r["workflows"]) == used) and (not q or q in r["name"].lower() or q in r["id"] or q in r["description"].lower())]
        self.shown = sort_rows(rows, R_KEYS, *self.sort)
        self.table.setRowCount(0)
        self.table.empty_text = ("Jira did not return any resolutions (reading them needs the Browse permission)."
                                 if not self.catalog.get("resolutions") else "No resolutions match the current filters.")
        self.expanded &= {r["id"] for r in self.shown}
        for res in self.shown:
            r = self.table.rowCount(); self.table.insertRow(r)
            wf, sp = res["workflows"], res["spaces"]
            self.table.setCellWidget(r, 0, self._arrow(res["id"]))
            self.table.setCellWidget(r, 1, status_name_cell(res))
            self.table.setItem(r, 2, cell(res["id"]))
            self.table.setItem(r, 3, cell(res["description"] or "—"))
            self.table.setItem(r, 4, cell(len(wf), True, tip="\n".join(wf[:30]) + (f"\n… +{len(wf) - 30} more" if len(wf) > 30 else "")
                                          or "Not set by any workflow"))
            self.table.setItem(r, 5, cell(len(sp), True, tip=", ".join(sp[:40]) + (f" … +{len(sp) - 40} more" if len(sp) > 40 else "")
                                          or "Not used in any space"))
            if res["id"] in self.expanded:
                self._insert_detail(r + 1, res)
        self.table.refit()
        total = len(self.catalog.get("resolutions", []))
        self.status.setText(f"{len(self.shown)} of {total} resolutions")

    def _sort(self, col):
        if col == 0:
            return
        self.sort = (col, not self.sort[1] if self.sort[0] == col else True)
        self.table.horizontalHeader().setSortIndicator(col, Qt.SortOrder.AscendingOrder if self.sort[1] else Qt.SortOrder.DescendingOrder)
        self.fill()

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Export resolutions"), str(Path.home() / "jira_resolutions.csv"), "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["Resolution", "Resolution ID", "Description", "Built-in", "Default", "Associated workflows", "Associated spaces"])
            for r in self.shown:
                w.writerow([r["name"], r["id"], r["description"], "Yes" if "Built-in" in r["tags"] else "", "Yes" if r["default"] else "",
                            "; ".join(r["workflows"]), "; ".join(r["spaces"])])
        self.status.setText(f"Exported {len(self.shown)} resolutions to {path}")


# --------------------------------------------------------------------------- template
TEMPLATE_PATH = CACHE_ROOT / "template.json"


class ElementList(QListWidget):
    """One column of the page; items can be dragged within it and into the other columns."""

    def __init__(self, changed):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setSpacing(3); self.setMinimumHeight(190)
        m = self.model()
        for sig in (m.rowsInserted, m.rowsRemoved, m.rowsMoved):
            sig.connect(lambda *a: changed())

    def ids(self):
        return [self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]

    def set_ids(self, ids):
        self.clear()
        for e in ids:
            it = QListWidgetItem(ELEMENTS[e]); it.setData(Qt.ItemDataRole.UserRole, e); self.addItem(it)


class TemplatePage(QWidget):
    """Configure the Confluence page generated for the selected workflows. Changes are kept in memory until you save them."""
    changed = pyqtSignal()
    back_requested = pyqtSignal()
    publish_requested = pyqtSignal()
    ELEMENT_ATTR = {"toc": "toc", "info": "info", "flow": "flow", "details": "details", "json": "code_block"}

    def __init__(self, path=TEMPLATE_PATH):
        super().__init__()
        self.setObjectName("page")
        self.path = path
        self.template = Template.load(path)
        self.workflows, self._image_key, self._image = [], None, None
        self.client, self.screen_cache, self.field_cache, self._screen_note = None, {}, None, ""
        self.lists, self._loading, self._sync_pending, self._dirty = [], True, False, False

        def card(title, hint=""):
            f = QFrame(); f.setObjectName("card")
            l = QVBoxLayout(f); l.setContentsMargins(20, 16, 20, 16); l.setSpacing(8)
            l.addWidget(label(title, "field"))
            if hint:
                l.addWidget(label(hint, "caption"))
            return f, l

        def combo(items, minimum=14):
            c = QComboBox()
            for it in items:
                c.addItem(it[0], it[1])
            c.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            c.setMinimumContentsLength(minimum)
            return c

        # -- the workflows the page is generated for
        c0, l0 = card("Selected workflows", "Pick workflows on the Workflows page, then click Review Template. The preview shows the first one.")
        self.selection = label("", "caption")
        row0 = QHBoxLayout(); row0.addWidget(self.selection, 1)
        back = button("Choose workflows"); back.clicked.connect(self.back_requested); row0.addWidget(back)
        l0.addLayout(row0)

        # -- page layout
        self.layout_combo = combo([(text, key) for text, key, _ in LAYOUTS])
        self.full_width = QCheckBox("Use the full page width")
        c1, l1 = card("Page layout", "Columns of the generated page. The page elements are placed in them below.")
        row = QHBoxLayout(); row.addWidget(label("Columns", "caption")); row.addWidget(self.layout_combo, 1)
        row.addSpacing(16); row.addWidget(self.full_width); row.addStretch(); l1.addLayout(row)

        # -- page elements, grouped by relevance
        self.boxes = {
            "toc": QCheckBox("Table of Contents macro"), "info": QCheckBox(ELEMENTS["info"]),
            "flow": QCheckBox(ELEMENTS["flow"]), "details": QCheckBox(ELEMENTS["details"]),
            "json": QCheckBox("Raw JSON code block macro")}
        self.boxes["details"].setToolTip("All statuses (name, ID, category, properties, links to their transitions), all transitions (name, ID, "
                                         "path, screen link, rules, triggers, properties, event) and the transition screens (tabs and fields).")
        self.flow_jira = QCheckBox("Use Jira Status positions")
        self.flow_jira.setToolTip("Draw the statuses where the Jira workflow editor has them (the X and Y values Jira reports). "
                                  "Workflows without those values keep the automatic layout.")
        self.details_full = QCheckBox("Use the full page width")
        self.details_full.setToolTip("Put Workflow Details in its own full-width section below the columns")
        self.code_collapse, self.code_lines = QCheckBox("Collapse the code block"), QCheckBox("Show line numbers")
        c2, l2 = card("Page elements", "Turn elements on or off, then drag them between the columns below, or select one and use the arrows. "
                      "Disabled elements are left out of the page.")
        grid = QGridLayout(); grid.setHorizontalSpacing(28); grid.setVerticalSpacing(10)   # the groups sit side by side
        for gi, (title, elems) in enumerate(GROUPS):
            col = QVBoxLayout(); col.setSpacing(6)
            head = QLabel(title.upper()); head.setProperty("role", "caption"); col.addWidget(head)
            for e in elems:
                col.addWidget(self.boxes[e])
                if e == "flow":
                    sub = QVBoxLayout(); sub.setContentsMargins(26, 0, 0, 0); sub.addWidget(self.flow_jira); col.addLayout(sub)
                if e == "details":
                    sub = QVBoxLayout(); sub.setContentsMargins(26, 0, 0, 0); sub.addWidget(self.details_full); col.addLayout(sub)
                if e == "json":
                    sub = QVBoxLayout(); sub.setContentsMargins(26, 0, 0, 0); sub.setSpacing(6)
                    sub.addWidget(self.code_collapse); sub.addWidget(self.code_lines); col.addLayout(sub)
            col.addStretch()
            grid.addLayout(col, gi // 2, gi % 2)
        grid.setColumnStretch(0, 1); grid.setColumnStretch(1, 1)
        l2.addLayout(grid)
        l2.addSpacing(8)
        l2.addWidget(label("Placement", "field"))
        self.cols_host = QHBoxLayout(); self.cols_host.setSpacing(12)
        l2.addLayout(self.cols_host)
        mv = QHBoxLayout()
        for text, dx, dy in (("↑  Up", 0, -1), ("↓  Down", 0, 1), ("←  Left", -1, 0), ("→  Right", 1, 0)):
            b = button(text); b.clicked.connect(lambda _=False, dx=dx, dy=dy: self._move(dx, dy)); mv.addWidget(b)
        mv.addStretch(); l2.addLayout(mv)

        # -- workflow diagram
        self.width_combo = combo(list(WIDTH_OPTIONS), 8)
        self.flow_labels = QCheckBox("Show the transition labels (name and ID)")
        self.flow_combo = combo(list(FLOW_FORMATS), 16)
        self.quality = combo([(t, k) for t, k, _ in PNG_QUALITY])
        self.line_combo, self.color_combo = combo(LINE_STYLES, 14), combo(COLOR_STYLES, 14)
        self.l_lbl, self.k_lbl = label("Line Style", "caption"), label("Color Style", "caption")
        self.node_combo = combo(NODE_SCALES, 14)
        self.n_lbl = label("Status Size", "caption")
        self.node_combo.setToolTip("The scale of the generated PNG diagram: 100% shows it at its natural size")
        self.png_preview = QCheckBox("Also show a PNG preview image")
        c3, l3 = card("Workflow diagram", "Width works like the Width choice in the workflow pop-up. draw.io diagrams are attached as an "
                      "editable .drawio file; Confluence cannot show them directly, so a PNG preview is recommended.")
        self.w_lbl, self.f_lbl, self.q_lbl = label("Width", "caption"), label("Format", "caption"), label("Image Sharpness", "caption")
        r3 = QHBoxLayout(); r3.addWidget(self.w_lbl); r3.addWidget(self.width_combo)
        r3.addSpacing(16); r3.addWidget(self.flow_labels); r3.addStretch(); l3.addLayout(r3)
        r5 = QHBoxLayout(); r5.addWidget(self.n_lbl); r5.addWidget(self.node_combo); r5.addStretch(); l3.addLayout(r5)
        r6 = QHBoxLayout(); r6.addWidget(self.l_lbl); r6.addWidget(self.line_combo); r6.addSpacing(16)
        r6.addWidget(self.k_lbl); r6.addWidget(self.color_combo, 1); l3.addLayout(r6)
        r4 = QHBoxLayout(); r4.addWidget(self.f_lbl); r4.addWidget(self.flow_combo, 1)
        r4.addSpacing(16); r4.addWidget(self.q_lbl); r4.addWidget(self.quality); l3.addLayout(r4)
        l3.addWidget(self.png_preview)

        self.status = label("Click “Save template” to keep your settings.", "caption")
        self.save_btn = button("Save template", primary=True); self.save_btn.clicked.connect(self.persist)
        reset = button("Reset to defaults", subtle=True); reset.clicked.connect(self._reset)

        inner = QWidget(); inner.setObjectName("page")
        iv = QVBoxLayout(inner); iv.setContentsMargins(0, 0, 8, 0); iv.setSpacing(12)
        for c in (c0, c1, c2, c3):
            iv.addWidget(c)
        iv.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(inner)
        head = QHBoxLayout(); head.addWidget(label("Confluence page template", "subtitle")); head.addStretch(); head.addWidget(reset)
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(10)
        v.addLayout(head)
        v.addWidget(label("Choose what the generated Confluence page contains and how it is laid out.", "caption"))
        # live mock-up of the generated page next to the settings
        self.mockup = TemplatePreview()
        pscroll = QScrollArea(); pscroll.setWidgetResizable(True); pscroll.setFrameShape(QFrame.Shape.NoFrame); pscroll.setWidget(self.mockup)
        pcard = QFrame(); pcard.setObjectName("card"); pcard.setMinimumWidth(360)
        pl = QVBoxLayout(pcard); pl.setContentsMargins(16, 12, 16, 12); pl.setSpacing(6)
        ph = QHBoxLayout(); ph.addWidget(label("Preview", "field")); ph.addStretch()
        self.export_btn = button("Save diagram…", subtle=True); self.export_btn.clicked.connect(self.export_diagram); ph.addWidget(self.export_btn)
        pl.addLayout(ph)
        pl.addWidget(label("A mock-up of the page: columns, element order and macros, with the data of the first selected workflow.", "caption"))
        pl.addWidget(pscroll, 1)
        body = QHBoxLayout(); body.setSpacing(16); body.addWidget(scroll, 3); body.addWidget(pcard, 2)
        footer = QHBoxLayout(); footer.setSpacing(8)
        go = button("Continue to Publish"); go.clicked.connect(self.publish_requested)
        footer.addWidget(self.status, 1); footer.addWidget(self.save_btn); footer.addWidget(go)
        v.addLayout(body, 1); v.addLayout(footer)

        self._timer = QTimer(self); self._timer.setSingleShot(True); self._timer.setInterval(250)
        self._timer.timeout.connect(self._refresh_preview)
        self.layout_combo.currentIndexChanged.connect(self._layout_changed)
        self.full_width.toggled.connect(lambda on: self._set("full_width", on))
        for e, box in self.boxes.items():
            box.toggled.connect(lambda on, attr=self.ELEMENT_ATTR[e]: self._toggle_element(attr, on))
        self.details_full.toggled.connect(lambda on: self._toggle_element("details_full_width", on))
        self.flow_jira.toggled.connect(lambda on: self._set("flow_jira_pos", on))
        self.code_collapse.toggled.connect(lambda on: self._set("code_collapse", on))
        self.code_lines.toggled.connect(lambda on: self._set("code_lines", on))
        self.width_combo.currentIndexChanged.connect(lambda: self._set("flow_width", self.width_combo.currentData()))
        self.flow_labels.toggled.connect(lambda on: self._set("flow_labels", on))
        self.flow_combo.currentIndexChanged.connect(lambda: self._set("flow_format", self.flow_combo.currentData()))
        self.quality.currentIndexChanged.connect(lambda: self._set("png_quality", self.quality.currentData()))
        self.node_combo.currentIndexChanged.connect(lambda: self._set("flow_node_scale", self.node_combo.currentData()))
        self.line_combo.currentIndexChanged.connect(lambda: self._set("flow_line_style", self.line_combo.currentData()))
        self.color_combo.currentIndexChanged.connect(lambda: self._set("flow_color_style", self.color_combo.currentData()))
        self.png_preview.toggled.connect(lambda on: self._set("png_preview", on))
        self._populate()
        self.set_workflows([])

    # ---- transition screens: tabs and fields come from Jira ----
    def set_client(self, client):
        self.client, self.screen_cache, self.field_cache = client, {}, None
        self._load_screens()

    def _load_screens(self):
        wf = self.workflows[0] if self.workflows else None
        if wf is None or not wf.get("screens"):
            return
        need = [(sid, name) for sid, name in wf["screens"][:2] if str(sid) not in self.screen_cache]   # the preview shows the first two
        if not need:
            return
        if self.client is None:
            self._screen_note = "Sign in to load the tabs and fields of the transition screens."
            return
        client, known = self.client, self.field_cache
        self._screen_note = "Loading the screen tabs and fields from Jira…"

        def job(_):
            fields = known if known is not None else field_catalog(client)
            return fields, load_details(client, need, fields, client.site)

        self._screen_worker = Worker(job)
        self._screen_worker.done.connect(self._screens_loaded)
        self._screen_worker.failed.connect(lambda msg: (setattr(self, "_screen_note", f"Could not load the screens: {msg}"), self._refresh_preview()))
        self._screen_worker.start()

    def _screens_loaded(self, result):
        self.field_cache, details = result
        self.screen_cache.update(details)
        self._screen_note = ""
        self._refresh_preview()

    # ---- selected workflows ----
    def set_workflows(self, workflows):
        self.workflows = list(workflows)
        names = [w["name"] for w in self.workflows]
        self.selection.setText("No workflow selected yet: the preview shows a sample." if not names else
                               f"{len(names)} workflow(s): " + ", ".join(names[:5]) + (f" … and {len(names) - 5} more" if len(names) > 5 else ""))
        self._image_key = None
        self._load_screens()
        self._refresh_preview()

    # ---- state <-> widgets ----
    def _save(self):
        """A setting changed: refresh the preview and remember that the template is not saved yet."""
        self._dirty = True
        self._timer.start()
        self.status.setText("Unsaved changes. Click “Save template” to keep them.")
        self.changed.emit()

    def is_dirty(self) -> bool:
        return self._dirty

    def persist(self) -> bool:
        if self.template.save(self.path):
            self._dirty = False
            self.status.setText(f"Template saved to {self.path}.")
            return True
        self.status.setText(f"Could not save the template to {self.path}.")
        return False

    def _set(self, name, value):
        if self._loading:
            return
        setattr(self.template, name, value)
        self.template.normalize()
        self._sync_controls(); self._save()

    def _populate(self):
        t, self._loading = self.template, True
        for w, val in ((self.full_width, t.full_width), (self.details_full, t.details_full_width), (self.code_collapse, t.code_collapse), (self.code_lines, t.code_lines),
                       (self.flow_labels, t.flow_labels), (self.png_preview, t.png_preview), (self.flow_jira, t.flow_jira_pos),
                       *((self.boxes[e], getattr(t, attr)) for e, attr in self.ELEMENT_ATTR.items())):
            w.blockSignals(True); w.setChecked(val); w.blockSignals(False)
        for c, data in ((self.layout_combo, t.layout), (self.flow_combo, t.flow_format), (self.quality, t.png_quality),
                        (self.node_combo, t.flow_node_scale), (self.line_combo, t.flow_line_style), (self.color_combo, t.flow_color_style),
                        (self.width_combo, t.flow_width)):
            c.blockSignals(True); c.setCurrentIndex(max(0, c.findData(data))); c.blockSignals(False)
        self._rebuild_columns(); self._sync_controls()
        self._loading = False

    def _sync_controls(self):
        t = self.template
        for w in (self.code_collapse, self.code_lines):
            w.setEnabled(t.code_block)
        self.details_full.setEnabled(t.details)
        self.flow_jira.setEnabled(t.flow)
        diagram = (self.width_combo, self.flow_labels, self.flow_combo, self.w_lbl, self.f_lbl, self.node_combo, self.n_lbl,
                   self.line_combo, self.color_combo, self.l_lbl, self.k_lbl)
        for w in diagram:
            w.setEnabled(t.flow)
        self.q_lbl.setEnabled(t.flow and t.shows_png); self.quality.setEnabled(t.flow and t.shows_png)
        self.png_preview.setEnabled(t.flow and t.flow_format == "drawio")
        self.png_preview.setToolTip("Only applies to draw.io diagrams." if t.flow_format != "drawio" else "")
        self.export_btn.setEnabled(t.flow)

    # ---- columns ----
    def _rebuild_columns(self):
        while self.cols_host.count():
            w = self.cols_host.takeAt(0).widget()
            if w:
                w.hide(); w.setParent(None); w.deleteLater()   # gone at once, not on the next event loop turn
        self.lists = []
        caps = layout_info(self.template.layout)[2]
        for i, cap in enumerate(caps):
            box = QWidget(); bl = QVBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(4)
            bl.addWidget(label(f"Column {i + 1} · {cap}", "caption"))
            lst = ElementList(self._sync_soon); lst.set_ids(self.template.columns[i])
            lst.itemSelectionChanged.connect(lambda me=lst: self._one_selection(me))
            bl.addWidget(lst, 1); self.lists.append(lst)
            self.cols_host.addWidget(box, {"Narrow": 1, "Wide": 3, "Equal": 2, "Full width": 1}.get(cap, 2))

    def _one_selection(self, me):
        for l in self.lists:
            if l is not me and l.selectedItems():
                l.blockSignals(True); l.clearSelection(); l.blockSignals(False)

    def _read_lists(self):
        seen, cols = set(), []
        for l in self.lists:
            cols.append([e for e in l.ids() if e not in seen and not seen.add(e)])
        self.template.columns = cols

    def _sync_soon(self):  # drag & drop fires several model signals; handle them once, afterwards
        if not self._loading and not self._sync_pending:
            self._sync_pending = True
            QTimer.singleShot(0, self._sync)

    def _sync(self):
        self._sync_pending = False
        if self._loading:
            return
        self._read_lists(); self._save()

    def _move(self, dx, dy):
        for ci, l in enumerate(self.lists):
            sel = l.selectedItems()
            if not sel:
                continue
            row = l.row(sel[0])
            if dy and 0 <= row + dy < l.count():
                it = l.takeItem(row); l.insertItem(row + dy, it); l.setCurrentItem(it)
            elif dx and 0 <= ci + dx < len(self.lists):
                it = l.takeItem(row); tgt = self.lists[ci + dx]
                tgt.insertItem(min(row, tgt.count()), it); tgt.setCurrentItem(it)
            break

    def _layout_changed(self):
        if self._loading:
            return
        self._read_lists()
        self.template.layout = self.layout_combo.currentData()
        self.template.normalize()
        self._loading = True; self._rebuild_columns(); self._loading = False
        self._save()

    def _toggle_element(self, attr, on):
        if self._loading:
            return
        self._read_lists()
        setattr(self.template, attr, on)
        self.template.normalize()
        self._loading = True; self._rebuild_columns(); self._sync_controls(); self._loading = False
        self._save()

    def _reset(self):
        self.template = Template().normalize()
        self._populate(); self._save()

    # ---- preview and export ----
    def _refresh_preview(self):
        t, wf = self.template, (self.workflows[0] if self.workflows else None)
        image = None
        if wf is not None and t.flow and t.shows_png:
            key = (wf["id"], t.flow_width, t.flow_labels, t.flow_node_scale, t.flow_line_style, t.flow_color_style, t.flow_jira_pos)
            if key != self._image_key:   # drawing the diagram takes a moment, so it is reused while other settings change
                try:
                    (self._image, self._image_px), self._image_key = render_workflow(wf, t.flow_width, t.flow_labels, t.flow_node_scale, 1,
                                                                                       t.flow_line_style == "curved", t.flow_color_style == "mono", t.flow_jira_pos), key
                except Exception:
                    self._image, self._image_px, self._image_key = None, 0, None
            image = self._image
        self.mockup.set_template(t, wf, image, self.screen_cache, self.client.site if self.client else "", self._screen_note,
                                 getattr(self, "_image_px", 0) if image is not None else 0)

    def export_diagram(self):
        """Write the diagram of the first selected workflow the way the template says (PNG, or draw.io plus its PNG preview)."""
        wf, t = (self.workflows[0] if self.workflows else None), self.template
        if wf is None:
            QMessageBox.information(self, titlecase("No workflow"), "Select a workflow on the Workflows page first.")
            return
        safe = re.sub(r"[^\w.-]+", "_", wf["name"])
        drawio = t.flow_format == "drawio"
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Save diagram"), str(Path.home() / f"{safe}.{'drawio' if drawio else 'png'}"),
                                              "draw.io diagram (*.drawio)" if drawio else "PNG image (*.png)")
        if not path:
            return
        if drawio:
            Path(path).write_text(to_drawio(wf, t.flow_width, t.flow_labels, 100, t.flow_line_style == "curved", t.flow_color_style == "mono", t.flow_jira_pos), "utf-8")
        size = ""
        if not drawio or t.png_preview:
            image, shown = render_workflow(wf, t.flow_width, t.flow_labels, t.flow_node_scale, t.png_scale,
                                       t.flow_line_style == "curved", t.flow_color_style == "mono", t.flow_jira_pos)
            image.save(str(Path(path).with_suffix(".png")))
            size = f" ({image.width()} × {image.height()} px, shown {shown} px wide)"
        self.status.setText(f"Saved {Path(path).name}" + (" and its PNG preview" if drawio and t.png_preview else "") + size + ".")


# --------------------------------------------------------------------------- publish
def png_bytes(image) -> bytes:
    buf = QBuffer(); buf.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def raw_workflow_json(site: str, wf: dict) -> str:
    """The workflow as fetched from Jira (the raw JSON saved by the last fetch), pretty printed."""
    try:
        for raw in json.loads((CACHE_ROOT / urlparse(site).netloc / "workflows.json").read_text("utf-8")):
            ident = raw.get("id")
            if (ident.get("entityId") if isinstance(ident, dict) else ident) == wf["id"]:
                return json.dumps(raw, indent=2, ensure_ascii=False)
    except (OSError, ValueError, AttributeError):
        pass
    return json.dumps({k: v for k, v in wf.items() if k != "legacy_schemes"}, indent=2, ensure_ascii=False, default=str)


class PublishWorker(QThread):
    row = pyqtSignal(int, str, str, bool, dict)   # row, status text, page url, ok, what changed
    parent_done = pyqtSignal(str, str, str)       # status text, parent url, state: ok | skip | error
    done = pyqtSignal(int, int)

    def __init__(self, jira, conf, parent_info, jobs, create_report, who):
        super().__init__()
        self.jira, self.conf, self.parent, self.jobs, self.create_report, self.who = jira, conf, parent_info, jobs, create_report, who

    def _audit(self, action, target, tid, ok, details):
        log_change(self.conf.base, self.who, action, target, tid, "Success" if ok else "Failed", details)

    def run(self):
        ok = bad = 0
        fields = None
        for i, j in enumerate(self.jobs):
            page = None
            try:
                tpl, wf, c = j["tpl"], j["wf"], self.conf
                screens = {}
                if tpl.details and wf.get("screens"):   # the tabs and fields of every transition screen, read from Jira
                    fields = fields if fields is not None else field_catalog(self.jira)
                    try:
                        screens = load_details(self.jira, wf["screens"], fields, self.jira.site)
                    except JiraError:
                        screens = {}
                body = build_storage(wf, j["raw_json"], j["assets"], tpl, screens, self.jira.site)
                space, parent, overwrote = self.parent["spaceId"], self.parent["id"], False
                # an existing page with the same title is overwritten; a suffixed title is only the fallback for titles
                # that cannot be overwritten (the parent page itself, or a title used by something that is not a page)
                for title in (j["title"], f"{j['title'][:240]} ({wf['id'][-8:]})"):
                    existing = c.find_page(space, title)
                    if existing and existing["id"] == parent:
                        continue
                    try:
                        if existing:
                            page, overwrote = c.update_page(existing["id"], title, body, existing["version"], parent_id=parent), True
                        else:
                            page = c.create_page(space, parent, title, body)
                    except ConflictError:
                        continue
                    break
                else:
                    raise ConfluenceError("The page title is already in use and cannot be overwritten.")
                changes = {"title": title, "created": not overwrote, "attachments": [], "labels": [], "screens": len(screens)}
                parts = ["Overwrote the existing page" if overwrote else "Created the page", "workflow information in a Content properties macro"]
                where = f"{self.parent.get('kind', 'page')} “{self.parent.get('title', '')}”"
                if not overwrote:
                    parts.append(f"created under the parent {where}")
                elif page.get("move_error"):
                    parts.append(f"page NOT moved ({page['move_error']})")
                elif existing and existing.get("parentId") != str(parent):
                    parts.append(f"moved under the parent {where}")
                else:
                    parts.append("already under the parent (location unchanged)")
                if screens:
                    parts.append(f"{len(screens)} transition screen(s) documented")
                for name, blob, mime in j["attachments"]:
                    c.attach(page["id"], name, blob, mime)
                    changes["attachments"].append(name)
                if j["attachments"]:
                    parts.append("attachments: " + ", ".join(n for n, _, _ in j["attachments"]))
                try:
                    c.full_width(page["id"], tpl.full_width)
                    parts.append("full width" if tpl.full_width else "fixed width")
                    changes["width"] = "full width" if tpl.full_width else "fixed width"
                except ConfluenceError as e:
                    parts.append(f"page width NOT applied ({e})")
                try:
                    c.add_labels(page["id"], j["labels"])
                    parts.append("labels: " + ", ".join(j["labels"]))
                    changes["labels"] = j["labels"]
                except ConfluenceError as e:
                    parts.append(f"labels NOT added ({e})")
                ok += 1
                self._audit("Publish page", title, page["id"], True, " · ".join(parts))
                self.row.emit(i, " · ".join(parts), page["url"], True, changes)
            except ConfluenceError as e:
                bad += 1
                self._audit("Publish page", j["title"], page["id"] if page else "", False, str(e))
                self.row.emit(i, (f"Page created, but: {e}" if page else str(e)), page["url"] if page else "", False, {})
            except Exception as e:  # never let one workflow stop the batch
                bad += 1
                self._audit("Publish page", j["title"], "", False, f"{type(e).__name__}: {e}")
                self.row.emit(i, f"{type(e).__name__}: {e}", "", False, {})

        # after all pages: put the report on the parent page (a folder cannot hold macros)
        info, c = self.parent, self.conf
        url = f"{c.base}/wiki/pages/viewpage.action?pageId={info['id']}"
        if not ok:
            self.parent_done.emit("No page was published, so the report was not added", url, "skip")
        elif info.get("kind") != "page":
            self.parent_done.emit("The parent is a folder: the pages were created or moved under it. No content properties report is added, "
                                  "because folders cannot hold macros", url, "skip")
        elif not self.create_report:
            self.parent_done.emit("Content properties report not created (the option is turned off)", url, "skip")
        else:
            try:
                res = c.add_report_to_parent(info["id"])
                msg = ("Content properties report macro added" if res == "added" else "Content properties report macro already on the page (unchanged)")
                self._audit("Add content properties report", info.get("title", ""), info["id"], True, msg)
                self.parent_done.emit(msg + f" · lists the pages labelled “{REQUIRED_LABEL}” under this page", url, "ok")
            except Exception as e:  # never leave the status table waiting
                self._audit("Add content properties report", info.get("title", ""), info["id"], False, str(e))
                self.parent_done.emit(f"Content properties report NOT added ({e})", url, "error")
        self.done.emit(ok, bad)


class ResultDialog(QDialog):
    """What the publish run changed in Confluence, shown when it is finished."""

    def __init__(self, parent, html_text):
        super().__init__(parent)
        self.setWindowTitle("Publish Finished"); self.resize(720, 520)
        v = QVBoxLayout(self); v.setContentsMargins(24, 20, 24, 20); v.setSpacing(12)
        v.addWidget(label("Publish Finished", "subtitle"))
        v.addWidget(label("These are the changes made in Confluence. The same entries are on the Logs page.", "caption"))
        view = QTextBrowser(); view.setOpenExternalLinks(True); view.setHtml(html_text); v.addWidget(view, 1)
        row = QHBoxLayout(); row.addStretch()
        close = button("Close", primary=True); close.clicked.connect(self.accept); row.addWidget(close); v.addLayout(row)


class PublishPage(QWidget):
    """Create one Confluence page per selected workflow under a parent page or folder."""
    edit_template = pyqtSignal()
    job_created = pyqtSignal(dict)   # the job that repeats this publish (see jobscript.make_job)

    def __init__(self, template_page):
        super().__init__()
        self.setObjectName("page")
        self.tp = template_page
        self.jira, self.me, self.flows, self.target, self.running = None, {}, [], None, False

        head = QHBoxLayout(); head.addWidget(label("Publish to Confluence", "subtitle")); head.addStretch()
        edit = button("Edit Template", subtle=True); edit.clicked.connect(self.edit_template); head.addWidget(edit)

        # -- target
        card = QFrame(); card.setObjectName("card")
        c = QVBoxLayout(card); c.setContentsMargins(20, 16, 20, 16); c.setSpacing(6)
        c.addWidget(label("Target Parent Page or Folder URL", "field"))
        self.url = QLineEdit(placeholderText="https://your-company.atlassian.net/wiki/spaces/KEY/pages/123456/Parent+page")
        self.url.setClearButtonEnabled(True)
        self.check_btn = button("Check"); self.check_btn.clicked.connect(self._resolve)
        row = QHBoxLayout(); row.addWidget(self.url, 1); row.addWidget(self.check_btn); c.addLayout(row)
        self.ok_lbl = QLabel(); self.ok_lbl.setObjectName("success"); self.ok_lbl.setWordWrap(True); self.ok_lbl.hide()
        self.err_lbl = QLabel(); self.err_lbl.setObjectName("error"); self.err_lbl.setWordWrap(True); self.err_lbl.hide()
        c.addWidget(self.ok_lbl); c.addWidget(self.err_lbl)
        self.report_cb = QCheckBox("Create a content properties report on the parent page")
        self.report_cb.setToolTip("Appends a Content properties report macro to the parent page. It lists the pages published under it "
                                  f"that carry the “{REQUIRED_LABEL}” label. Only possible when the parent is a page, not a folder.")
        c.addWidget(self.report_cb)
        self.prefix_cb = QCheckBox("Add a prefix to the page titles")
        self.prefix_edit = QLineEdit(placeholderText="e.g. [Workflow]")
        prow = QHBoxLayout(); prow.addWidget(self.prefix_cb); prow.addWidget(self.prefix_edit, 1); c.addLayout(prow)
        self.example_lbl = label("", "caption"); c.addWidget(self.example_lbl)
        c.addWidget(label("One page per selected workflow is created under this parent. Your Jira credentials are used to sign in to Confluence.",
                          "caption"))
        trow = QHBoxLayout(); self.tpl_lbl = label("", "caption"); trow.addWidget(self.tpl_lbl, 1); c.addLayout(trow)
        c.addStretch(1)

        # -- labels
        lcard = QFrame(); lcard.setObjectName("card")
        lc = QVBoxLayout(lcard); lc.setContentsMargins(20, 16, 20, 16); lc.setSpacing(6)
        lc.addWidget(label("Labels", "field"))
        lc.addWidget(label(f"“{REQUIRED_LABEL}” is always added. Add more below; they apply to every page published in this run "
                           "(lower case, no spaces: spaces become “-”).", "caption"))
        self.label_combo = QComboBox(); self.label_combo.setEditable(True)
        self.label_combo.lineEdit().setPlaceholderText("Choose an existing label or type a new one")
        self.label_combo.setCurrentIndex(-1)
        add = button("Add"); add.clicked.connect(self._add_label)
        self.label_combo.lineEdit().returnPressed.connect(self._add_label)
        self.load_labels_btn = button("Load Existing Labels"); self.load_labels_btn.clicked.connect(self._load_labels)
        lrow = QHBoxLayout(); lrow.addWidget(self.label_combo, 1); lrow.addWidget(add); lrow.addWidget(self.load_labels_btn); lc.addLayout(lrow)
        self.label_list = QListWidget(); self.label_list.setMaximumHeight(120); lc.addWidget(self.label_list)
        self.label_msg = label("", "caption")
        rem = button("Remove Selected", subtle=True); rem.clicked.connect(self._remove_label)
        lrow2 = QHBoxLayout(); lrow2.addWidget(self.label_msg, 1); lrow2.addWidget(rem); lc.addLayout(lrow2)
        lc.addStretch(1)

        # -- workflows and their publish status
        self.table = AutoFitTable(["WORKFLOW", "PAGE TITLE", "STATUS"])
        self.table.itemDoubleClicked.connect(lambda it: self._open(it.row()))
        self.summary = label("", "caption")
        self.progress = QProgressBar(); self.progress.setTextVisible(False); self.progress.hide()
        remove = button("Remove From Selection", subtle=True); remove.clicked.connect(self._remove)
        self.publish_btn = button("Publish to Confluence", primary=True); self.publish_btn.setMinimumWidth(190)
        self.publish_btn.clicked.connect(self.publish)

        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        for box in (card, lcard):  # captions wrap instead of widening a column
            for lbl in box.findChildren(QLabel):
                lbl.setWordWrap(True)
        cols = QHBoxLayout(); cols.setSpacing(12); cols.addWidget(card, 1); cols.addWidget(lcard, 1)
        v.addLayout(head)
        v.addWidget(label("Create one Confluence page per selected workflow under a parent page or folder.", "caption"))
        v.addLayout(cols)
        v.addWidget(label("Workflows to Publish", "field")); v.addWidget(self.progress); v.addWidget(self.table, 1)
        self.job_btn = button("Create Job"); self.job_btn.setMinimumWidth(130)
        self.job_btn.setToolTip("Turn these workflows, the template and the destination into a job that a script can repeat on a server.")
        self.job_btn.clicked.connect(self.create_job)
        foot = QHBoxLayout(); foot.addWidget(self.summary, 1); foot.addWidget(remove); foot.addSpacing(12)
        foot.addWidget(self.job_btn); foot.addSpacing(8); foot.addWidget(self.publish_btn)
        v.addLayout(foot)

        for cb in (self.prefix_cb,):
            cb.toggled.connect(self._options_changed)
        self.prefix_edit.textChanged.connect(self._options_changed)
        self.prefix_edit.editingFinished.connect(self.tp.persist)
        self.report_cb.toggled.connect(self._report_toggled)
        self.url.textChanged.connect(self._url_changed)
        self.url.editingFinished.connect(self._save_url)

    # ---- session ----
    def start(self, jira, me):
        self.jira, self.me = jira, me
        self.refresh()

    def reset(self):
        self.jira, self.target = None, None
        self.refresh()

    # ---- options (kept with the template) ----
    def _title_for(self, wf) -> str:
        t = self.tp.template
        return page_title(wf["name"], t.title_prefix, t.title_prefix_on)

    def _options_changed(self, *_):
        t = self.tp.template
        t.title_prefix_on, t.title_prefix = self.prefix_cb.isChecked(), self.prefix_edit.text()
        self.prefix_edit.setEnabled(t.title_prefix_on)
        if self.sender() is self.prefix_cb:
            self.tp.persist()
        self._titles()

    def _titles(self):
        sample = self.flows[0] if self.flows else {"name": "Workflow name"}
        self.example_lbl.setText("Example Title:  " + self._title_for(sample))
        for r, wf in enumerate(self.flows):
            if self.table.item(r, 1):
                self.table.item(r, 1).setText(self._title_for(wf))
        self.table.refit()

    def _report_toggled(self, on):
        self.tp.template.parent_report = on
        self.tp.persist()

    def _url_changed(self, _):
        self.target = None; self.ok_lbl.hide(); self.err_lbl.hide(); self.report_cb.setEnabled(True)

    def _save_url(self):
        self.tp.template.parent_url = self.url.text().strip()
        self.tp.persist()

    # ---- labels ----
    def _refresh_labels(self):
        self.label_list.clear()
        fixed = QListWidgetItem(f"{REQUIRED_LABEL}   ·   always added")
        fixed.setFlags(Qt.ItemFlag.ItemIsEnabled); fixed.setForeground(QColor(T["fg3"]))  # not selectable, not removable
        self.label_list.addItem(fixed)
        for name in self.tp.template.labels:
            self.label_list.addItem(name)

    def _add_label(self):
        name = clean_label(self.label_combo.currentText())
        if name == REQUIRED_LABEL:
            self.label_msg.setText(f"“{REQUIRED_LABEL}” is always added already.")
        elif name and name not in self.tp.template.labels:
            self.tp.template.labels.append(name); self._refresh_labels(); self.tp.persist()
        self.label_combo.setCurrentIndex(-1); self.label_combo.clearEditText()

    def _remove_label(self):
        for it in self.label_list.selectedItems():
            if it.text() in self.tp.template.labels:
                self.tp.template.labels.remove(it.text())
        self._refresh_labels(); self.tp.persist()

    def _load_labels(self):
        """Fetch the existing labels of the target Confluence site (the Jira site if no URL is entered yet)."""
        if not self.jira:
            self.label_msg.setText("Sign in first to load existing labels.")
            return
        try:
            base = parse_target(self.url.text())[0]
        except ConfluenceError:
            base = self.jira.site
        try:
            cc = client_for(self.jira, base)
        except ConfluenceError as e:
            self.label_msg.setText(str(e))
            return
        self.label_msg.setText("Loading labels…"); self.load_labels_btn.setEnabled(False)
        self._lw = Worker(lambda _: cc.list_labels())
        self._lw.done.connect(self._labels_loaded)
        self._lw.failed.connect(lambda msg: (self.load_labels_btn.setEnabled(True), self.label_msg.setText(f"Could not load labels: {msg}")))
        self._lw.start()

    def _labels_loaded(self, names):
        self.load_labels_btn.setEnabled(True)
        self.label_combo.clear(); self.label_combo.addItems(names); self.label_combo.setCurrentIndex(-1); self.label_combo.clearEditText()
        self.label_msg.setText(f"{len(names)} existing labels loaded — open the drop-down to pick one.")

    # ---- table ----
    def refresh(self):
        """Called when the page is shown: take over the workflows chosen on the Workflows page and the saved settings."""
        t = self.tp.template
        self.flows = list(self.tp.workflows)
        self.tpl_lbl.setText("Template: " + t.summary())
        for w, val in ((self.report_cb, t.parent_report), (self.prefix_cb, t.title_prefix_on)):
            w.blockSignals(True); w.setChecked(val); w.blockSignals(False)
        self.prefix_edit.blockSignals(True); self.prefix_edit.setText(t.title_prefix); self.prefix_edit.blockSignals(False)
        self.prefix_edit.setEnabled(t.title_prefix_on)
        if not self.url.text() and t.parent_url:
            self.url.blockSignals(True); self.url.setText(t.parent_url); self.url.blockSignals(False)
        self._refresh_labels()
        self.table.setRowCount(len(self.flows))
        for r, wf in enumerate(self.flows):
            self.table.setItem(r, 0, cell(wf["name"]))
            self.table.setItem(r, 1, cell(self._title_for(wf)))
            self.table.setItem(r, 2, cell("Ready", color=T["fg3"]))
        self.table.refit()
        self._titles()
        self.summary.setText(f"{len(self.flows)} workflow(s) selected" if self.flows else
                             "Nothing selected yet. Tick workflows on the Workflows page, then click Review Template.")
        self.publish_btn.setEnabled(bool(self.flows) and self.jira is not None and not self.running)
        self.job_btn.setEnabled(self.publish_btn.isEnabled())

    def _remove(self):
        rows = {i.row() for i in self.table.selectedIndexes()}
        if rows and not self.running:
            self.tp.set_workflows([w for r, w in enumerate(self.flows) if r not in rows])
            self.refresh()

    def _set_status(self, row, text, color, url=""):
        it = cell(text, color=color, tip="• " + text.replace(" · ", "\n• ") + ("\n\nDouble-click to open" if url else ""))
        it.setData(Qt.ItemDataRole.UserRole, url)
        self.table.setItem(row, 2, it)
        self.table.refit()

    def _open(self, row):
        item = self.table.item(row, 2)
        url = item.data(Qt.ItemDataRole.UserRole) if item else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ---- target ----
    def _message(self, ok, text):
        self.ok_lbl.setVisible(ok); self.err_lbl.setVisible(not ok)
        (self.ok_lbl if ok else self.err_lbl).setText(text)

    def _resolve(self, then=None):
        """Validate the URL and look up the parent's space; `then` runs on success."""
        self.target = None
        if not self.jira:
            self._message(False, "Sign in first: your Jira credentials are used for Confluence too.")
            return
        try:
            base, kind, cid = parse_target(self.url.text())
        except ConfluenceError as e:
            self._message(False, str(e))
            return
        try:
            client = client_for(self.jira, base)
        except ConfluenceError as e:
            self.check_btn.setEnabled(True); self.publish_btn.setEnabled(bool(self.flows))
            self._message(False, str(e))
            return
        self.check_btn.setEnabled(False); self.publish_btn.setEnabled(False)
        self._rw = Worker(lambda _: client.resolve_parent(kind, cid))
        self._rw.done.connect(lambda info: self._resolved(base, client, info, then))
        self._rw.failed.connect(self._resolve_failed)
        self._rw.start()

    def _resolve_failed(self, msg):
        self.check_btn.setEnabled(True); self.publish_btn.setEnabled(bool(self.flows)); self._message(False, msg)

    def _resolved(self, base, client, info, then):
        self.check_btn.setEnabled(True); self.publish_btn.setEnabled(bool(self.flows))
        self.target = (client, info)
        space = f"{info['spaceName']} ({info['spaceKey']})" if info["spaceName"] else info["spaceId"]
        folder = info["kind"] == "folder"
        self.report_cb.setEnabled(not folder)   # no report macro on a folder
        self._message(True, f"✔ Parent {info['kind']}: “{info['title']}”  ·  Space: {space}  ·  {urlparse(base).netloc}"
                      + ("\nPages are created or moved directly under this folder. No content properties report is added "
                         "(folders cannot hold macros)." if folder else ""))
        self._save_url()
        if then:
            then()

    # ---- publish ----
    def publish(self):
        if self.flows and self.jira and not self.running:
            self._resolve(then=self._confirm)

    def create_job(self):
        """Check the destination, then hand the current selection, template and destination over to the Job page."""
        if self.flows and self.jira and not self.running:
            self._resolve(then=self._job_ready)

    def _job_ready(self):
        _, info = self.target
        if self.tp.template.flow:
            QMessageBox.information(self, titlecase("Workflow diagrams"), DIAGRAM_NOTICE)
        self.job_created.emit(make_job(self.jira.site, self.tp.template, info, self.url.text().strip(), self.flows))

    def _confirm(self):
        _, info = self.target
        report = self.report_cb.isChecked() and info["kind"] == "page"
        box = QMessageBox(QMessageBox.Icon.Question, titlecase("Publish to Confluence"),
                          f"Create {len(self.flows)} page(s) under the {info['kind']} “{info['title']}” in space {info['spaceKey'] or info['spaceId']}?"
                          "\n\nIf a page with the same title already exists in the space, it will be OVERWRITTEN "
                          "(Confluence keeps the previous version in the page history)."
                          + ("\n\nThe pages will be created or moved directly under the folder." if info["kind"] == "folder" else "")
                          + ("\n\nA content properties report will be added to the parent page." if report else "")
                          + f"\n\nEvery page gets the label “{REQUIRED_LABEL}”" + (f" and {', '.join(self.tp.template.labels)}" if self.tp.template.labels else "") + ".",
                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, self)
        box.button(QMessageBox.StandardButton.Yes).setText("Publish")
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if box.exec() == QMessageBox.StandardButton.Yes:
            self._prepare_and_start()

    def _prepare_and_start(self):
        client, info = self.target
        tpl = self.tp.template
        jobs = []
        self.running = True
        self.publish_btn.setEnabled(False); self.job_btn.setEnabled(False); self.check_btn.setEnabled(False)
        self.progress.setRange(0, len(self.flows)); self.progress.setValue(0); self.progress.show()
        labels = list(dict.fromkeys([REQUIRED_LABEL] + [clean_label(x) for x in tpl.labels if clean_label(x)]))
        for r, wf in enumerate(self.flows):  # drawing the diagrams needs the GUI thread
            self._set_status(r, "Building the page…", T["fg3"]); QApplication.processEvents()
            assets, attachments, fid = {}, [], wf["id"][-8:]
            try:
                if tpl.flow and tpl.shows_png:
                    assets["png_name"] = f"workflow_{fid}.png"
                    image, assets["png_width"] = render_workflow(wf, tpl.flow_width, tpl.flow_labels, tpl.flow_node_scale, tpl.png_scale,
                                                                     tpl.flow_line_style == "curved", tpl.flow_color_style == "mono", tpl.flow_jira_pos)
                    attachments.append((assets["png_name"], png_bytes(image), "image/png"))
                if tpl.flow and tpl.flow_format == "drawio":
                    assets["drawio_name"] = f"workflow_{fid}.drawio"
                    drawio = to_drawio(wf, tpl.flow_width, tpl.flow_labels, 100, tpl.flow_line_style == "curved", tpl.flow_color_style == "mono", tpl.flow_jira_pos)
                    attachments.append((assets["drawio_name"], drawio.encode("utf-8"), "application/vnd.jgraph.mxfile"))
                jobs.append({"wf": wf, "tpl": tpl, "title": self._title_for(wf), "assets": assets, "attachments": attachments, "labels": labels,
                             "raw_json": raw_workflow_json(self.jira.site, wf)})
            except Exception as e:
                jobs.append(None); self._set_status(r, f"Skipped: the page could not be built ({e})", T["danger"])
            self.progress.setValue(r + 1)
        self._row_map = [r for r, j in enumerate(jobs) if j]
        todo = [j for j in jobs if j]
        for r in self._row_map:
            self._set_status(r, "Queued", T["fg3"])
        self.table.setRowCount(len(self.flows) + 1)   # the last row reports the parent page's content properties report
        self._parent_row = len(self.flows)
        self.table.setItem(self._parent_row, 0, cell(f"Parent {info['kind']}: {info.get('title', '')}"))
        self.table.setItem(self._parent_row, 1, cell("Content properties report"))
        self._set_status(self._parent_row, "Waiting for the pages to finish…", T["fg3"])
        me = self.me or {}
        who = f"{me.get('displayName', '')} <{me.get('emailAddress') or self.jira.email}>".strip()
        self._changes = []
        self.progress.setRange(0, max(1, len(todo))); self.progress.setValue(0); self._done_count = 0
        if not todo:
            self._finished(0, 0)
            return
        self.pw = PublishWorker(self.jira, client, info, todo, self.report_cb.isChecked(), who)
        self.pw.row.connect(self._row_done); self.pw.parent_done.connect(self._parent_done); self.pw.done.connect(self._finished)
        self.summary.setText("Publishing…")
        self.pw.start()

    def _row_done(self, i, msg, url, ok, changes):
        self._done_count += 1; self.progress.setValue(self._done_count)
        self._set_status(self._row_map[i], ("✔ " if ok else "✖ ") + msg, T["success"] if ok else T["danger"], url)
        self._changes.append((ok, msg, url, changes))
        self.summary.setText(f"Publishing… {self._done_count} of {self.progress.maximum()}")

    def _parent_done(self, msg, url, state):
        mark, color = {"ok": ("✔ ", "success"), "skip": ("ℹ ", "fg3"), "error": ("✖ ", "danger")}[state]
        self._set_status(self._parent_row, mark + msg, T[color], url if state != "error" else "")
        self._parent_msg = (state, msg, url)

    def _finished(self, ok, bad):
        self.running = False
        self.progress.hide(); self.check_btn.setEnabled(True); self.publish_btn.setEnabled(bool(self.flows)); self.job_btn.setEnabled(bool(self.flows))
        self.summary.setText(f"Published {ok} page(s)" + (f", {bad} failed" if bad else "") + ". Double-click a row to open the page.")
        ResultDialog(self.window(), self._result_html(ok, bad)).exec()

    def _result_html(self, ok, bad) -> str:
        created = sum(1 for g, _, _, c in self._changes if g and c.get("created"))
        over = sum(1 for g, _, _, c in self._changes if g and not c.get("created"))
        parts = [f"<h3>{ok} page(s) published" + (f", {bad} failed" if bad else "") + "</h3><ul>"
                 f"<li><b>{created}</b> page(s) created, <b>{over}</b> existing page(s) overwritten (the previous version stays in the page history)</li>"]
        extra = {l for g, _, _, c in self._changes if g for l in c.get("labels", [])}
        if extra:
            parts.append("<li>Labels added to every page: " + ", ".join(sorted(extra)) + "</li>")
        parts.append("</ul><h3>Pages</h3><ul>")
        for g, msg, url, c in self._changes:
            title = html.escape(c.get("title", "")) or "Page"
            head = f'<a href="{html.escape(url, quote=True)}">{title}</a>' if url else title
            parts.append(f"<li>{'✔' if g else '✖'} {head}<br/><span style='color:gray'>{html.escape(msg)}</span></li>")
        parts.append("</ul>")
        _, msg, _ = getattr(self, "_parent_msg", ("skip", "", ""))
        parts.append(f"<h3>Parent Page</h3><p>{html.escape(msg)}</p>")
        return "".join(parts)


# --------------------------------------------------------------------------- job
JOB_PATH = CACHE_ROOT / "job.json"


def info_grid(rows) -> QWidget:
    """Label / value pairs; the values wrap and can be selected."""
    w = QWidget(); g = QGridLayout(w); g.setContentsMargins(0, 0, 0, 0); g.setHorizontalSpacing(16); g.setVerticalSpacing(6)
    g.setColumnStretch(1, 1)
    for r, (k, v) in enumerate(rows):
        key = label(k, "caption", verbatim=True); key.setWordWrap(False)
        val = QLabel(str(v)); val.setWordWrap(True); val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(key, r, 0, Qt.AlignmentFlag.AlignTop); g.addWidget(val, r, 1)
    return w


def clear_layout(layout):
    """Remove everything from a layout at once (not at the next event loop turn)."""
    while layout.count():
        item = layout.takeAt(0)
        if item.widget() is not None:
            item.widget().hide(); item.widget().setParent(None)
        elif item.layout() is not None:
            clear_layout(item.layout())


class JobPage(QWidget):
    """The job: what it publishes (selected workflows and spaces), how the pages look, where they go, and the portable script that
    repeats it without the GUI."""
    select_more = pyqtSignal(list)         # names of the job's workflows: the Workflows page shows them ticked
    selection_changed = pyqtSignal(list)   # names left in the selection after the job's selection was cleared
    publish_requested = pyqtSignal()       # the destination is set on the Publish page

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        self.job, self.catalog = load_job(JOB_PATH), None
        if self.job:   # jobs saved by an earlier version
            self.job.setdefault("spaces", []); self.job.setdefault("diagram_placeholder", True)
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        head = QHBoxLayout(); head.setSpacing(16)
        titles = QVBoxLayout(); titles.setSpacing(2)
        titles.addWidget(label("Job", "subtitle"))
        self.caption = label("", "caption"); titles.addWidget(self.caption)
        head.addLayout(titles, 1)
        self.export_btn = button("Export Job…", primary=True); self.export_btn.clicked.connect(self.export_job)
        head.addWidget(self.export_btn, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(head)
        self.holder = QVBoxLayout(); v.addLayout(self.holder, 1)
        self.render()

    # ---- the job ----
    def set_job(self, job):
        """A job made on the Publish page (it has the destination). The text typed for the comment, the spaces and the placeholder
        choice of the job it replaces are kept."""
        old = self.job or {}
        job["script"] = {"title": (old.get("script") or {}).get("title", ""), "contact": (old.get("script") or {}).get("contact", "")}
        job["spaces"] = list(old.get("spaces", []))
        job["diagram_placeholder"] = old.get("diagram_placeholder", True)
        job["name"] = job_name(job)
        self.job = job
        save_job(job, JOB_PATH)
        self.render()

    def update_selection(self, site, template, workflows):
        """The Create Job button of the Workflows page: the ticked workflows become the job's workflows. The destination of the
        job (if it has one) stays; a first job has none until it is made on the Publish page."""
        if self.job:
            job = self.job
            job["site"] = site
            job["workflows"] = [{"id": w["id"], "name": w["name"]} for w in workflows]
            apply_template(job, template)
            job["name"] = job_name(job)
        else:
            self.job = make_job(site, template, {}, "", workflows)
        save_job(self.job, JOB_PATH)
        self.render()

    def set_catalog(self, catalog):
        self.catalog = catalog
        if self.job:
            self._fill_spaces()

    def _scope_changed(self):
        """The workflows or spaces of the job changed: save it and bring everything that shows the scope up to date, in place."""
        job = self.job
        job["name"] = job_name(job)
        save_job(job, JOB_PATH)
        self.title_edit.setPlaceholderText(job["name"])
        self.tabs.setTabText(1, f"Workflows ({len(job['workflows'])})")
        self._fill_steps(); self._fill_selected(); self._fill_template(); self._update_export(); self._refresh_preview(); self._space_summary()

    def _update_export(self):
        why = can_export(self.job)
        self.export_btn.setEnabled(not why)
        self.export_btn.setToolTip(why or "Write the job as a Python script to a folder")

    # ---- layout helpers ----
    def _card(self, title, body, note=""):
        card = QFrame(); card.setObjectName("card")
        c = QVBoxLayout(card); c.setContentsMargins(20, 16, 20, 16); c.setSpacing(8)
        c.addWidget(label(title, "field"))
        if note:
            c.addWidget(label(note, "caption"))
        c.addWidget(body)
        return card

    def _pane(self, *widgets):
        """One tab: its cards in a scroll area, so a long tab never squeezes the page."""
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame)
        host = QWidget(); host.setObjectName("page")
        v = QVBoxLayout(host); v.setContentsMargins(0, 12, 8, 4); v.setSpacing(12)
        for w in widgets:
            v.addWidget(w)
        v.addStretch(1)
        scroll.setWidget(host)
        return scroll

    def _body(self):
        """An empty card body and its layout."""
        body = QWidget(); g = QVBoxLayout(body); g.setContentsMargins(0, 0, 0, 0); g.setSpacing(6)
        return body, g

    def render(self):
        clear_layout(self.holder)
        job = self.job
        if not job:
            self.export_btn.setEnabled(False)
            self.caption.setText("No job yet. Select workflows on the Workflows page and click Create Job (or publish them to Confluence "
                                 "and click Create Job on the Publish page).")
            self.holder.addStretch(); return
        when = fmt_date(job["created"]) if job.get("created") else ""
        self.caption.setText(f"Created {when}. The script does not publish a snapshot: every run reads the current data from Jira, "
                             "applies the template and publishes the result to Confluence, without a window.")

        self.tabs = tabs = QTabWidget(); tabs.setObjectName("pivot"); tabs.setDocumentMode(True)
        tabs.tabBar().setExpanding(False); tabs.tabBar().setDrawBase(False); tabs.tabBar().setUsesScrollButtons(False)
        tabs.addTab(self._pane(self._diagram_card(), self._steps_card()), "Overview")
        tabs.addTab(self._pane(self._selected_card(), self._spaces_card()), f"Workflows ({len(job['workflows'])})")
        tabs.addTab(self._pane(self._template_card()), "Template")
        tabs.addTab(self._pane(self._destination_card()), "Destination")
        tabs.addTab(self._pane(self._comment_card(), self._run_card()), "Script")

        split = QSplitter(); split.setChildrenCollapsible(False)
        split.addWidget(tabs); split.addWidget(self._preview_panel())
        split.setStretchFactor(0, 2); split.setStretchFactor(1, 3); split.setSizes([520, 700])
        self.holder.addWidget(split, 1)
        self._fill_steps(); self._fill_selected(); self._fill_template(); self._fill_spaces(); self._update_export()

    # ---- overview ----
    def _diagram_card(self):
        job = self.job
        t = Template.from_dict(job["template"])
        body, g = self._body()
        self.placeholder_cb = QCheckBox("Add a placeholder for the workflow diagram to pages that have no diagram")
        self.placeholder_cb.setChecked(bool(job.get("diagram_placeholder", True)) and t.flow); self.placeholder_cb.setEnabled(t.flow)
        self.placeholder_cb.toggled.connect(self._placeholder_toggled)
        g.addWidget(self.placeholder_cb)
        if t.flow:
            g.addWidget(label(f"Position of the placeholder: {diagram_position(t)}. It is the place the Template page gives the workflow "
                              "diagram, so the page layout stays as designed.", "caption"))
            g.addWidget(label("To update the diagram picture by hand, open the page in Confluence, edit it, and add the picture in the "
                              "placeholder area (Insert, then Files and images; for example the PNG saved from the workflow viewer of "
                              "this app). A later run keeps the picture and only refreshes the rest of the page.", "caption"))
        else:
            g.addWidget(label("The Workflow Diagram element is turned off in the template, so there is nothing to place. Turn it on in "
                              "the Template page and create the job again.", "caption"))
        return self._card("Workflow Diagram Placeholder", body, DIAGRAM_NOTICE)

    def _placeholder_toggled(self, on):
        self.job["diagram_placeholder"] = on
        save_job(self.job, JOB_PATH)
        self._fill_steps(); self._fill_template(); self._refresh_preview()

    def _steps_card(self):
        body, self.steps_box = self._body(); self.steps_box.setSpacing(4)
        return self._card("What the Job Does", body)

    def _scope_text(self):
        job = self.job
        parts = []
        if job["workflows"]:
            parts.append(f"the {len(job['workflows'])} selected workflow(s)")
        if job.get("spaces"):
            parts.append("every workflow of the space(s) " + ", ".join(s["key"] for s in job["spaces"]))
        return " and ".join(parts) or "nothing yet (select workflows or spaces)"

    def _fill_steps(self):
        job = self.job
        d = job["destination"]
        t = Template.from_dict(job["template"])
        target = (f"the {d['kind']} “{d['title'] or d['url']}”" if d.get("url") else "the destination (not set yet: use the Publish page)")
        diagram = ("The workflow diagram is not drawn. A page that has one keeps it" + ("; a page without one gets a placeholder at the "
                   "template's diagram position, where you can add the picture by hand." if job.get("diagram_placeholder", True) else
                   "; a page without one is published without a diagram.")) if t.flow else ""
        steps = [f"Sign in to {job['site']} with the account given by -user and -token, and read the current workflows.",
                 f"Select the workflows of the scope: {self._scope_text()} (a workflow or space deleted in Jira is skipped).",
                 "Build one Confluence page for each of them from that data, with the template.",
                 diagram,
                 f"Create the pages under {target}; a page with the same title is overwritten.",
                 "Add the labels" + (" and the content properties report on the parent page." if d.get("parent_report") else "."),
                 "Write the progress to the screen and a log file, and end with an exit code a scheduler can check."]
        clear_layout(self.steps_box)
        for i, text in enumerate((s for s in steps if s), 1):
            l = QLabel(f"{i}. {text}"); l.setWordWrap(True); self.steps_box.addWidget(l)

    # ---- workflows tab ----
    def _selected_card(self):
        body, g = self._body()
        self.sel_table = AutoFitTable(["WORKFLOW", "ID"])
        self.sel_table.empty_text = "No workflows selected."
        g.addWidget(self.sel_table)
        row = QHBoxLayout(); row.setSpacing(8)
        self.sel_lbl = label("", "caption"); row.addWidget(self.sel_lbl, 1)
        more = button("Select More Workflows…"); more.setToolTip("Go to the Workflows page, tick more workflows and click Create Job")
        more.clicked.connect(lambda: self.select_more.emit([w["name"] for w in self.job["workflows"]]))
        self.clear_btn = button("Clear Selection"); self.clear_btn.clicked.connect(self._clear_workflows)
        row.addWidget(more); row.addWidget(self.clear_btn)
        g.addLayout(row)
        return self._card("Selected Workflows", body, "These workflows are published on every run. A workflow that was deleted in Jira is skipped.")

    def _fill_selected(self):
        flows = self.job["workflows"]
        t = self.sel_table
        t.setRowCount(len(flows))
        for r, w in enumerate(flows):
            t.setItem(r, 0, cell(w["name"])); t.setItem(r, 1, cell(w["id"], color=T["fg3"]))
        t.refit()
        h = min(300, 48 + 44 * max(len(flows), 2))
        t.setMinimumHeight(h); t.setMaximumHeight(h)
        self.sel_lbl.setText(f"{len(flows)} workflow(s) selected")
        self.clear_btn.setEnabled(bool(flows))

    def _clear_workflows(self):
        self.job["workflows"] = []
        self._scope_changed()
        self.selection_changed.emit([])

    def _spaces_card(self):
        body, g = self._body()
        self.space_search = QLineEdit(placeholderText="Search the spaces"); self.space_search.setClearButtonEnabled(True)
        self.space_search.textChanged.connect(lambda _: self._fill_spaces())
        g.addWidget(self.space_search)
        self.space_table = AutoFitTable(["", "SPACE", "KEY", "WORKFLOWS"], fixed={0: 56}, numeric=(3,))
        self.space_table.setMinimumHeight(300); self.space_table.setMaximumHeight(300)
        g.addWidget(self.space_table)
        row = QHBoxLayout(); row.setSpacing(8)
        self.space_lbl = label("", "caption"); row.addWidget(self.space_lbl, 1)
        self.clear_spaces_btn = button("Clear Spaces"); self.clear_spaces_btn.clicked.connect(self._clear_spaces)
        row.addWidget(self.clear_spaces_btn)
        g.addLayout(row)
        return self._card("Spaces", body, "Tick the Jira spaces whose workflows belong to the job. On every run the job publishes all the "
                          "workflows that the space uses at that moment, together with the selected workflows above.")

    def _fill_spaces(self):
        job = self.job
        chosen = {s["key"] for s in job.get("spaces", [])}
        if self.catalog:
            counts = {}
            for w in self.catalog["workflows"]:
                for k in w["project_keys"]:
                    counts[k] = counts.get(k, 0) + 1
            rows = [(s["name"], s["key"], counts.get(s["key"], 0)) for s in self.catalog["spaces"]]
            self.space_table.empty_text = "No space matches the search."
        else:   # not signed in or not loaded yet: only the spaces the job already has
            rows = [(s["name"], s["key"], None) for s in job.get("spaces", [])]
            self.space_table.empty_text = "Sign in to list the Jira spaces."
        q = self.space_search.text().strip().lower()
        rows = sorted((r for r in rows if not q or q in r[0].lower() or q in r[1].lower()), key=lambda r: r[0].lower())
        t = self.space_table
        t.setRowCount(0)
        for name, key, n in rows:
            r = t.rowCount(); t.insertRow(r)
            box = QWidget(); bl = QHBoxLayout(box); bl.setContentsMargins(0, 0, 0, 0)
            cb = QCheckBox(); cb.setChecked(key in chosen)
            cb.toggled.connect(lambda on, k=key, nm=name: self._space_toggled(k, nm, on))
            bl.addWidget(cb, alignment=Qt.AlignmentFlag.AlignCenter)
            t.setCellWidget(r, 0, box)
            t.setItem(r, 1, cell(name)); t.setItem(r, 2, cell(key))
            t.setItem(r, 3, cell("—" if n is None else n, True, color=T["fg3"] if n is None else None))
        t.refit()
        self._space_summary()

    def _space_toggled(self, key, name, on):
        spaces = [s for s in self.job.get("spaces", []) if s["key"] != key]
        if on:
            spaces.append({"key": key, "name": name})
        self.job["spaces"] = sorted(spaces, key=lambda s: s["key"].lower())
        self._scope_changed()

    def _space_summary(self):
        spaces = self.job.get("spaces", [])
        self.clear_spaces_btn.setEnabled(bool(spaces))
        if not spaces:
            self.space_lbl.setText("No space selected")
            return
        text = f"{len(spaces)} space(s) selected"
        if self.catalog:
            keys = {s["key"] for s in spaces}
            n = sum(1 for w in self.catalog["workflows"] if keys & set(w["project_keys"]))
            gone = keys - {s["key"] for s in self.catalog["spaces"]}
            text += f" · {n} workflow(s) in their scope" + (f" · not in Jira any more: {', '.join(sorted(gone))}" if gone else "")
        self.space_lbl.setText(text)

    def _clear_spaces(self):
        self.job["spaces"] = []
        self._fill_spaces()
        self._scope_changed()

    # ---- template and destination ----
    def _template_card(self):
        body, self.tpl_box = self._body()
        return self._card("Confluence Template", body,
                          "The layout of every generated page. Change it on the Template page and click Create Job on the Workflows page to update the job.")

    def _fill_template(self):
        clear_layout(self.tpl_box)
        self.tpl_box.addWidget(info_grid(describe_template(self.job)))

    def _destination_card(self):
        body, g = self._body()
        g.addWidget(info_grid(describe_destination(self.job)))
        if not self.job["destination"].get("url"):
            g.addWidget(label("The destination is set on the Publish page: enter the parent page or folder there and click Create Job.", "caption"))
            go = button("Open the Publish Page"); go.clicked.connect(self.publish_requested)
            row = QHBoxLayout(); row.addWidget(go); row.addStretch(); g.addLayout(row)
        return self._card("Destination", body, "Where the documentation is published.")

    # ---- script tab ----
    def _run_card(self):
        body, g = self._body()
        cmd = QLabel(f"python {script_name(self.job)} -user you@company.com -token <API token>")
        cmd.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)); cmd.setWordWrap(True)
        cmd.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        g.addWidget(cmd)
        g.addWidget(label("Add --dry-run to read Jira and build the pages without changing Confluence. Every run writes a detailed log next to the script "
                          "(<script>.log, or --log-file <path>); show the last run with  -viewlog  (the whole file with  -viewlog all).",
                          "caption"))
        return self._card("Run the Job", body, "The exported script contains no user name or token. Whoever runs it supplies the account "
                          "with the -user and -token options (the account needs access to Jira workflows and to the Confluence space).")

    def _comment_card(self):
        custom = self.job.get("script") or {}
        body, g = self._body()
        self.title_edit = QLineEdit(custom.get("title", "")); self.title_edit.setPlaceholderText(self.job["name"])
        self.contact_edit = QLineEdit(custom.get("contact", "")); self.contact_edit.setPlaceholderText("e.g. Jira team · jira-admins@company.com · ext. 1234")
        g.addWidget(label("Script name", "field")); g.addWidget(self.title_edit)
        g.addWidget(label("Contact info", "field")); g.addWidget(self.contact_edit)
        for edit in (self.title_edit, self.contact_edit):
            edit.textChanged.connect(self._comment_changed)
        return self._card("Script Comment", body, "The first lines of the script are a comment that explains the job. Set the name shown "
                          "there and who to contact about it (leave the name empty to use the job name).")

    def _comment_changed(self, *_):
        self.job["script"] = {"title": self.title_edit.text().strip(), "contact": self.contact_edit.text().strip()}
        save_job(self.job, JOB_PATH)
        self._refresh_preview()

    def _refresh_preview(self):
        text = generate_script(self.job)
        scroll = self.preview.verticalScrollBar(); pos = scroll.value()
        self.preview.setPlainText(text); scroll.setValue(pos)
        self.lines.setText(f"{text.count(chr(10)) + 1:,} lines")

    def _preview_panel(self):
        panel = QWidget(); panel.setObjectName("page")
        v = QVBoxLayout(panel); v.setContentsMargins(8, 0, 0, 0); v.setSpacing(8)
        head = QHBoxLayout(); head.setSpacing(8)
        head.addWidget(label("Script Preview", "field"))
        self.lines = label("", "caption"); self.lines.setWordWrap(False); head.addWidget(self.lines); head.addStretch(1)
        copy = button("Copy", subtle=True); copy.clicked.connect(self.copy_script); head.addWidget(copy)
        v.addLayout(head)
        v.addWidget(label("The Python script exactly as it is exported: the job configuration, the library code and the job logic. "
                          "Needs Python 3.9+ and requests; no Qt and no other packages.", "caption"))
        self.preview = QPlainTextEdit(); self.preview.setReadOnly(True)
        self.preview.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.preview.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        v.addWidget(self.preview, 1)
        self._refresh_preview()
        return panel

    def copy_script(self):
        QApplication.clipboard().setText(generate_script(self.job))

    def export_job(self):
        why = can_export(self.job)
        if why:
            QMessageBox.information(self, titlecase("Job not ready"), why)
            return
        folder = QFileDialog.getExistingDirectory(self, titlecase("Export job to"), str(Path.home()))
        if not folder:
            return
        path = Path(folder) / script_name(self.job)
        if path.exists() and QMessageBox.question(self, titlecase("Replace file"), f"{path.name} already exists in this folder. Replace it?") \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            path.write_text(generate_script(self.job), "utf-8", newline="\n")
        except OSError as e:
            QMessageBox.warning(self, titlecase("Job not exported"), f"Could not write the script: {e}")
            return
        QMessageBox.information(self, titlecase("Job exported"),
                                f"{path}\n\nTest it first:\n    python {path.name} -user you@company.com -token <API token> --dry-run")


# --------------------------------------------------------------------------- logs
L_COLS = ["TIME", "USER", "SITE", "ACTION", "TARGET", "ID", "RESULT", "DETAILS"]


class LogsPage(QWidget):
    """Every change made on the Jira site through this app, newest first."""

    def __init__(self):
        super().__init__()
        self.setObjectName("page")
        top = QHBoxLayout()
        top.addLayout(page_header("Logs", "Every change this app made to your Jira and Confluence sites."), 1)
        self.search = QLineEdit(placeholderText="Search the log"); self.search.setClearButtonEnabled(True); self.search.setMinimumWidth(260)
        refresh = button("Refresh", primary=True); refresh.clicked.connect(self.reload)
        export = button("Export CSV"); export.clicked.connect(self.export_csv)
        opn = button("Open log file"); opn.clicked.connect(lambda: LOG_PATH.exists() and QDesktopServices.openUrl(QUrl.fromLocalFile(str(LOG_PATH))))
        top.addWidget(self.search); top.addWidget(opn); top.addWidget(export); top.addWidget(refresh)
        self.table = AutoFitTable(L_COLS)
        self.table.empty_text = "No changes recorded yet. The app writes an entry here whenever it changes your Jira or Confluence site."
        self.status = label("", "caption")
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(12)
        v.addLayout(top); v.addWidget(self.table, 1); v.addWidget(self.status)
        self.search.textChanged.connect(lambda _: self.fill())
        self.entries = []

    def reload(self):
        self.entries = read_log()
        self.fill()

    def fill(self):
        q = self.search.text().strip().lower()
        rows = [e for e in self.entries if not q or q in " ".join(str(v) for v in e.values()).lower()]
        self.table.setRowCount(0)
        for e in rows:
            r = self.table.rowCount(); self.table.insertRow(r)
            ok = e.get("result") == "Success"
            vals = [fmt_date(e.get("time", "")), e.get("user", ""), urlparse(e.get("site", "")).netloc, e.get("action", ""),
                    e.get("target", ""), e.get("target_id", ""), e.get("result", ""), e.get("details", "")]
            for c, val in enumerate(vals):
                self.table.setItem(r, c, cell(val, color=(T["success"] if ok else T["danger"]) if c == 6 else None, tip=str(val)))
        self.table.refit()
        self.status.setText(f"{len(rows)} of {len(self.entries)} entries · stored in {LOG_PATH}" if self.entries
                            else f"No changes recorded yet. Entries are written to {LOG_PATH} when the app changes your Jira site.")

    def export_csv(self):
        path, _ = QFileDialog.getSaveFileName(self, titlecase("Export log"), str(Path.home() / "workflow_manager_log.csv"), "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                wr = csv.writer(f); wr.writerow(["Time", "User", "Site", "Action", "Target", "ID", "Result", "Details"])
                for e in self.entries:
                    wr.writerow([e.get("time"), e.get("user"), e.get("site"), e.get("action"), e.get("target"),
                                 e.get("target_id"), e.get("result"), e.get("details")])

    def showEvent(self, e):
        super().showEvent(e)
        self.reload()


# --------------------------------------------------------------------------- settings
SETTINGS_PATH = CACHE_ROOT / "settings.json"


class Segmented(QFrame):
    """A row of mutually exclusive toggle buttons."""
    changed = pyqtSignal(str)

    def __init__(self, options, current):
        super().__init__()
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(6)
        self.group, self.btns = QButtonGroup(self), {}
        for key, text in options:
            b = QPushButton(titlecase(text)); b.setCheckable(True); b.setProperty("seg", True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self.group.addButton(b); row.addWidget(b); self.btns[key] = b
        row.addStretch(); self.set(current)

    def set(self, key):
        if key in self.btns:
            self.btns[key].setChecked(True)


class AccentPicker(QFrame):
    """Round colour swatches; the selected one shows a check mark."""
    changed = pyqtSignal(str)

    def __init__(self, current):
        super().__init__()
        self.current, self.btns = current, {}
        row = QHBoxLayout(self); row.setContentsMargins(0, 0, 0, 0); row.setSpacing(10)
        for key, spec in ACCENTS.items():
            b = QPushButton(); b.setToolTip(spec[0]); b.setFixedSize(36, 36)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            row.addWidget(b); self.btns[key] = b
        row.addStretch(); self.restyle()

    def set(self, key):
        self.current = key
        self.restyle()

    def restyle(self):
        for key, b in self.btns.items():
            col = ACCENTS[key][2 if T["dark"] else 1][3]
            sel = key == self.current
            b.setText("✓" if sel else "")
            b.setStyleSheet(
                f"QPushButton {{ background: {col}; color: #FFFFFF; border: {3 if sel else 1}px solid "
                f"{T['fg1'] if sel else T['stroke1']}; border-radius: 18px; padding: 0; min-height: 30px; min-width: 30px; "
                f"font-weight: 700; }} QPushButton:hover {{ border: 3px solid {T['fg3']}; }}")


class SettingsPage(QWidget):
    """Colour mode, accent colour and text size; applied at once and remembered."""
    appearance_changed = pyqtSignal(object)

    def __init__(self, appearance):
        super().__init__()
        self.setObjectName("page")
        self.ap = appearance

        def card(title, hint=""):
            f = QFrame(); f.setObjectName("card")
            l = QVBoxLayout(f); l.setContentsMargins(20, 16, 20, 18); l.setSpacing(8)
            l.addWidget(label(title, "subtitle"))
            if hint:
                l.addWidget(label(hint, "caption"))
            return f, l

        # -- appearance
        self.mode = Segmented(MODES, appearance.mode)
        self.accent = AccentPicker(appearance.accent)
        self.size = Segmented([(k, n) for k, n, _ in TEXT_SIZES], appearance.text_size)
        self.accent_name = label("", "caption")
        self.sample = QLabel("The quick brown fox jumps over the lazy dog. 0123456789")
        c1, l1 = card("Appearance", "Changes apply immediately and are remembered the next time you start the app.")
        l1.addSpacing(6)
        l1.addWidget(label("Color mode", "field")); l1.addWidget(self.mode)
        l1.addSpacing(8)
        arow = QHBoxLayout(); arow.addWidget(label("Accent color", "field")); arow.addWidget(self.accent_name); arow.addStretch()
        l1.addLayout(arow); l1.addWidget(self.accent)
        l1.addSpacing(8)
        l1.addWidget(label("Text size", "field")); l1.addWidget(self.size); l1.addWidget(self.sample)
        reset = button("Reset Appearance", subtle=True); reset.clicked.connect(self._reset)
        rrow = QHBoxLayout(); rrow.addStretch(); rrow.addWidget(reset); l1.addLayout(rrow)

        # -- preview
        c2, l2 = card("Preview", "How controls look with the current color mode and accent.")
        prow = QHBoxLayout(); prow.setSpacing(10)
        prow.addWidget(button("Primary", primary=True)); prow.addWidget(button("Default")); prow.addWidget(button("Subtle", subtle=True))
        sample_in = QLineEdit(); sample_in.setPlaceholderText("Text input"); prow.addWidget(sample_in, 1)
        l2.addLayout(prow)
        ok = QLabel("✔  Published 3 pages"); ok.setObjectName("success"); ok.setMinimumHeight(28)
        err = QLabel("✖  Could not reach Confluence"); err.setObjectName("error"); err.setMinimumHeight(28)
        brow = QHBoxLayout(); brow.addWidget(ok); brow.addWidget(err); l2.addLayout(brow)
        crow = QHBoxLayout(); crow.setSpacing(14)
        check = QCheckBox("Checkbox"); check.setChecked(True); crow.addWidget(check)
        radio = QRadioButton("Radio button"); radio.setChecked(True); crow.addWidget(radio)
        danger = button("Delete"); danger.setProperty("danger", True); crow.addWidget(danger); crow.addStretch()
        l2.addLayout(crow)

        # -- data and about
        c3, l3 = card("Data and About")
        l3.addWidget(label(APP_NAME, "field"))
        l3.addWidget(label(f"Version {__version__}  ·  Released {RELEASE_DATE}", "caption"))
        l3.addWidget(label(f"Fetched JSON, templates, the change log and these settings are stored in {CACHE_ROOT}", "caption"))
        orow = QHBoxLayout(); open_btn = button("Open Data Folder"); open_btn.clicked.connect(
            lambda: (CACHE_ROOT.mkdir(parents=True, exist_ok=True), QDesktopServices.openUrl(QUrl.fromLocalFile(str(CACHE_ROOT)))))
        orow.addWidget(open_btn); orow.addStretch(); l3.addLayout(orow)

        inner = QWidget(); inner.setObjectName("page")
        iv = QVBoxLayout(inner); iv.setContentsMargins(0, 0, 8, 0); iv.setSpacing(12)
        for c in (c1, c2, c3):
            iv.addWidget(c)
        iv.addStretch()
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QFrame.Shape.NoFrame); scroll.setWidget(inner)
        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 20); v.setSpacing(10)
        v.addWidget(label("Settings", "subtitle"))
        v.addWidget(label("Personalize how the app looks. These options do not change the pages you publish.", "caption"))
        v.addWidget(scroll, 1)

        self.mode.changed.connect(lambda k: self._set("mode", k))
        self.accent.changed.connect(lambda k: self._set("accent", k))
        self.size.changed.connect(lambda k: self._set("text_size", k))
        self.refresh()

    def refresh(self):
        self.mode.set(self.ap.mode); self.size.set(self.ap.text_size); self.accent.set(self.ap.accent)
        self.accent_name.setText("· " + ACCENTS[self.ap.accent][0])

    def _set(self, name, value):
        setattr(self.ap, name, value)
        self.ap.save(SETTINGS_PATH)
        self.appearance_changed.emit(self.ap)

    def _reset(self):
        d = Appearance()
        self.ap.mode, self.ap.accent, self.ap.text_size = d.mode, d.accent, d.text_size
        self.ap.save(SETTINGS_PATH)
        self.appearance_changed.emit(self.ap)


# --------------------------------------------------------------------------- icons + main window
def nav_icon(kind, color) -> QIcon:
    pm = QPixmap(40, 40); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    if kind == "signin":
        p.drawEllipse(QRectF(13, 6, 14, 14)); p.drawArc(QRectF(7, 24, 26, 22), 0, 180 * 16)
    elif kind == "workflows":
        p.drawRoundedRect(QRectF(14, 5, 12, 9), 3, 3)
        p.drawRoundedRect(QRectF(4, 27, 12, 9), 3, 3); p.drawRoundedRect(QRectF(24, 27, 12, 9), 3, 3)
        p.drawLine(QPointF(20, 14), QPointF(20, 21)); p.drawLine(QPointF(10, 21), QPointF(30, 21))
        p.drawLine(QPointF(10, 21), QPointF(10, 27)); p.drawLine(QPointF(30, 21), QPointF(30, 27))
    elif kind == "resolutions":   # a check mark in a circle
        p.drawEllipse(QRectF(6, 6, 28, 28)); p.drawLine(QPointF(13, 20), QPointF(18, 26)); p.drawLine(QPointF(18, 26), QPointF(28, 14))
    elif kind == "schemes":
        for y in (6, 15, 24):
            p.drawRoundedRect(QRectF(7, y, 26, 9), 3, 3)
    elif kind == "settings":   # gear
        import math
        p.drawEllipse(QRectF(14, 14, 12, 12))
        for k in range(8):
            a = k * math.pi / 4
            p.drawLine(QPointF(20 + 9 * math.cos(a), 20 + 9 * math.sin(a)), QPointF(20 + 14 * math.cos(a), 20 + 14 * math.sin(a)))
    elif kind == "publish":
        p.drawLine(QPointF(20, 28), QPointF(20, 8)); p.drawLine(QPointF(12, 15), QPointF(20, 7)); p.drawLine(QPointF(28, 15), QPointF(20, 7))
        p.drawLine(QPointF(8, 33), QPointF(32, 33))
    elif kind == "template":
        p.drawRoundedRect(QRectF(7, 5, 26, 30), 3, 3); p.drawLine(QPointF(7, 13), QPointF(33, 13)); p.drawLine(QPointF(18, 13), QPointF(18, 35))
    elif kind == "job":   # clock
        p.drawEllipse(QRectF(6, 6, 28, 28)); p.drawLine(QPointF(20, 12), QPointF(20, 20)); p.drawLine(QPointF(20, 20), QPointF(26, 24))
    elif kind == "exit":   # power symbol
        p.drawArc(QRectF(8, 9, 24, 24), 130 * 16, 280 * 16); p.drawLine(QPointF(20, 5), QPointF(20, 19))
    elif kind == "logs":
        p.drawRoundedRect(QRectF(8, 5, 24, 30), 3, 3)
        for y in (14, 21, 28):
            p.drawLine(QPointF(14, y), QPointF(26, y))
    else:
        for y in (9, 20, 31):
            p.drawEllipse(QRectF(6, y - 3, 6, 6)); p.drawLine(QPointF(18, y), QPointF(34, y))
    p.end()
    return QIcon(pm)


def app_icon_pixmap(size=256):
    pm = QPixmap(size, size); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    u = size / 256
    p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor("#0F6CBD"))
    p.drawRoundedRect(QRectF(8 * u, 8 * u, 240 * u, 240 * u), 54 * u, 54 * u)
    p.setPen(QPen(QColor("#FFFFFF"), 12 * u, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    for x, y in ((28, 100), (88, 150), (148, 100)):  # three statuses on a staircase, joined by transitions
        p.drawRoundedRect(QRectF(x * u, y * u, 76 * u, 46 * u), 12 * u, 12 * u)
    for a, b, c, d in ((104, 146, 116, 150), (164, 150, 176, 146)):
        p.drawLine(QPointF(a * u, b * u), QPointF(c * u, d * u))
    p.end()
    return pm


def app_icon() -> QIcon:
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(app_icon_pixmap(size))
    return icon


class SessionButton(QPushButton):
    """Side-bar panel with the signed-in user; under the mouse it turns into an exit button that closes the app."""

    def __init__(self):
        super().__init__()
        self.setProperty("session", True); self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(22, 22)); self.setMinimumHeight(58)
        self.name = self.site = ""
        self.icons = (QIcon(), QIcon())

    def set_session(self, name, site):
        self.name, self.site = name, site
        self._idle()

    def set_icons(self, idle, hover):
        self.icons = (idle, hover)
        self.setIcon(hover if self.underMouse() else idle)

    def _idle(self):
        fm = self.fontMetrics()  # the panel is narrow: long names are shortened, the tooltip keeps them whole
        self.setText(f"{fm.elidedText(self.name, Qt.TextElideMode.ElideRight, 140)}\n"
                     f"{fm.elidedText(self.site, Qt.TextElideMode.ElideMiddle, 140)}")
        self.setIcon(self.icons[0]); self.setToolTip(f"{self.name} · {self.site}\nClick to exit the app")

    def enterEvent(self, e):
        self.setText("Exit app\nClick to close"); self.setIcon(self.icons[1])
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._idle()
        super().leaveEvent(e)


class MainWindow(QMainWindow):
    PAGE = {}   # nav key -> page index, filled below (so no number in this class has to be kept in step with the list)
    NAV = [("Sign In", "signin"), ("Workflows", "workflows"), ("Schemas", "schemes"), ("Statuses", "statuses"), ("Resolutions", "resolutions"), ("Template", "template"),
           ("Publish", "publish"), ("Job", "job"), ("Logs", "logs"), ("Settings", "settings")]

    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} v{__version__}"); self.resize(1280, 780)
        self.setWindowIcon(app_icon())
        self.ap = Appearance.load(SETTINGS_PATH)  # color mode, accent and text size from the Settings page
        self.login, self.workflows, self.schemes, self.statuses = LoginPage(), WorkflowsPage(), SchemesPage(), StatusesPage()
        self.resolutions = ResolutionsPage()
        self.PAGE = {key: i for i, (_, key) in enumerate(self.NAV)}
        self.template_page, self.logs = TemplatePage(), LogsPage()
        self.settings_page = SettingsPage(self.ap)
        self.publish_page = PublishPage(self.template_page)
        self.job_page = JobPage()
        self.stack = QStackedWidget()
        for p in (self.login, self.workflows, self.schemes, self.statuses, self.resolutions, self.template_page, self.publish_page, self.job_page, self.logs,
                  self.settings_page):
            self.stack.addWidget(p)

        self.side = QFrame(); self.side.setObjectName("sidebar"); self.side.setFixedWidth(220)
        sv = QVBoxLayout(self.side); sv.setContentsMargins(10, 16, 10, 16); sv.setSpacing(4)
        sv.addWidget(label(APP_NAME.removeprefix("Jira "), "navtitle"))
        sv.addSpacing(10)
        self.group = QButtonGroup(self); self.group.setExclusive(True)
        self.nav_btns = []
        for i, (text, _) in enumerate(self.NAV):
            b = QPushButton(text); b.setProperty("nav", True); b.setCheckable(True)
            b.setIconSize(QSize(20, 20)); b.setCursor(Qt.CursorShape.PointingHandCursor)
            keys = f"Ctrl+{i + 1}" if i < 9 else "Ctrl+,"   # Ctrl+1 ... Ctrl+9, and Ctrl+, for the last page (Settings)
            b.setToolTip(f"{text}  ({keys})")
            self.group.addButton(b, i); self.nav_btns.append(b)
            if text != "Settings":
                sv.addWidget(b)
            QShortcut(QKeySequence(keys), self, activated=lambda i=i: self.nav_btns[i].isEnabled() and self.go(i))
        sv.addStretch()
        sv.addWidget(self.nav_btns[-1])   # Settings sits at the bottom of the navigation pane
        sv.addSpacing(6)
        self.conn = label("Not signed in", "caption"); sv.addWidget(self.conn)
        self.session_btn = SessionButton(); self.session_btn.clicked.connect(QApplication.quit); self.session_btn.hide()
        sv.addWidget(self.session_btn)
        sv.addSpacing(6)
        sv.addWidget(label(f"Version {__version__}", "caption"))
        sv.addWidget(label(f"Released {RELEASE_DATE}", "caption"))
        self.group.idClicked.connect(self.go)

        root = QWidget(); h = QHBoxLayout(root); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(0)
        h.addWidget(self.side); h.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.login.logged_in.connect(self._logged_in)
        self.login.signed_out.connect(self._signed_out)
        self.login.go_workflows.connect(lambda: self.go(1))
        self.login.retry_requested.connect(self.workflows.fetch)
        self.workflows.load_started.connect(self.login.load_started)
        self.workflows.load_progress.connect(self.login.load_progress)
        self.workflows.load_failed.connect(self.login.load_failed)
        self.workflows.loaded.connect(self._data_loaded)
        self.workflows.loaded.connect(self.statuses.set_catalog)
        self.workflows.loaded.connect(self.resolutions.set_catalog)
        self.resolutions.refresh_requested.connect(self.workflows.fetch)
        self.workflows.loaded.connect(self.schemes.set_catalog)
        self.schemes.refresh_requested.connect(self.workflows.fetch)
        self.statuses.refresh_requested.connect(self.workflows.fetch)
        self.workflows.template_requested.connect(self._review_template)
        self.workflows.job_requested.connect(self._job_from_selection)
        self.workflows.loaded.connect(self.job_page.set_catalog)
        self.job_page.select_more.connect(self._select_more)
        self.job_page.selection_changed.connect(self.workflows.set_checked)
        self.job_page.publish_requested.connect(lambda: self.go(self.PAGE["publish"]))
        self.template_page.back_requested.connect(lambda: self.go(1))
        self.template_page.publish_requested.connect(lambda: self.go(self.PAGE["publish"]))
        self.publish_page.edit_template.connect(lambda: self.go(self.PAGE["template"]))
        self.publish_page.job_created.connect(lambda job: (self.job_page.set_job(job), self.go(self.PAGE["job"])))
        self.settings_page.appearance_changed.connect(self.apply_appearance)
        try:  # follow the operating system when the color mode is "Use system setting"
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda *_: self.apply_appearance() if self.ap.mode == "system" else None)
        except AttributeError:
            pass
        QShortcut(QKeySequence("Ctrl+F"), self, activated=self._focus_search)
        QShortcut(QKeySequence("F5"), self, activated=self._refresh_current)
        self.settings = QSettings("Nagarro", APP_NAME)
        geo = self.settings.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        self.apply_appearance()
        self._signed_out()

    def _focus_search(self):
        s = getattr(self.stack.currentWidget(), "search", None)
        if s is not None:
            s.setFocus(); s.selectAll()

    def _refresh_current(self):
        """F5: fetch again on the data pages, reload the log."""
        page = self.stack.currentWidget()
        if page in (self.workflows, self.schemes, self.statuses, self.resolutions) and self.workflows.client:
            self.workflows.fetch()
        elif page is self.logs:
            self.logs.reload()

    def closeEvent(self, e):
        if self.template_page.is_dirty():
            box = QMessageBox(QMessageBox.Icon.Question, titlecase("Unsaved template"),
                              "The Confluence page template has unsaved changes. Save them before closing?",
                              QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel, self)
            box.setDefaultButton(QMessageBox.StandardButton.Save)
            ans = box.exec()
            if ans == QMessageBox.StandardButton.Cancel:
                e.ignore()
                return
            if ans == QMessageBox.StandardButton.Save:
                self.template_page.persist()
        self.settings.setValue("geometry", self.saveGeometry())
        e.accept()

    def apply_appearance(self, ap=None):
        self.ap = ap or self.ap
        t = fluent.apply(QApplication.instance(), self.ap)
        self.settings_page.refresh()
        for b, (_, kind) in zip(self.nav_btns, self.NAV):
            b.setIcon(nav_icon(kind, t["fg1"]))
        self.session_btn.set_icons(nav_icon("signin", t["fg1"]), nav_icon("exit", t["danger"]))
        self.workflows.apply_filter(); self.schemes.apply_filter(); self.statuses.fill(); self.resolutions.fill()
        self.login.steps.restyle()
        for panel in self.findChildren(ScopesPanel):
            panel.restyle()

    def go(self, i):
        self.stack.setCurrentIndex(i); self.nav_btns[i].setChecked(True)
        if i == self.PAGE["publish"]:
            self.publish_page.refresh()

    def _set_enabled(self, on):
        for key in ("workflows", "schemes", "statuses", "resolutions", "publish"):   # the template, job, log and settings need no sign-in
            self.nav_btns[self.PAGE[key]].setEnabled(on)

    def _job_from_selection(self, workflows):
        self.job_page.update_selection(self.workflows.client.site, self.template_page.template, workflows)
        self.go(self.PAGE["job"])

    def _select_more(self, names):
        self.workflows.set_checked(names)
        self.go(1)

    def _review_template(self, workflows):
        self.template_page.set_workflows(workflows)
        self.go(self.PAGE["template"])

    def _logged_in(self, client, me):
        self.schemes.start(client, me)
        self.template_page.set_client(client)
        self.publish_page.start(client, me)
        self.workflows.start(client, me)
        self.session_btn.set_session(me.get("displayName") or me.get("emailAddress") or "Signed in", urlparse(client.site).netloc)
        self.conn.hide(); self.session_btn.show()
        self._set_enabled(True)   # stay on the sign-in page: its card lists the steps of loading the data, then opens the workflows

    def _data_loaded(self, _catalog):
        if self.login.loading:   # first load after sign-in (a later refresh does not move the user)
            self.login.load_done()
            self.go(1)

    def _signed_out(self):
        self.workflows.reset(); self.schemes.reset(); self.statuses.reset(); self.resolutions.reset()
        self.template_page.set_client(None); self.publish_page.reset(); self.login.set_disconnected()
        self.session_btn.hide(); self.conn.show()
        self._set_enabled(False); self.go(0)


def run():
    app = QApplication([])
    app.setStyle("Fusion")
    app.setApplicationName(APP_NAME); app.setApplicationVersion(__version__)
    w = MainWindow(); w.show()
    app.exec()
