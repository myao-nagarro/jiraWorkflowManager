"""Fluent 2 design tokens, appearance settings (mode / accent / text size) and the application style sheet."""
import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QGuiApplication, QPalette

MODES = [("light", "Light"), ("dark", "Dark"), ("system", "Use system setting")]
TEXT_SIZES = [("small", "Small", 13), ("default", "Default", 14), ("large", "Large", 16), ("xlarge", "Extra large", 18)]
FONT_FAMILIES = ["Segoe UI", "Segoe UI Variable", "Helvetica Neue", "Arial"]   # Fluent 2 type: Segoe UI first
FONT_STACK = ", ".join(f'"{f}"' for f in FONT_FAMILIES) + ", sans-serif"

# accent key -> (label, light (fill, hover, pressed, text/indicator, tint), dark (fill, hover, pressed, text/indicator, tint))
# Fill = filled buttons; text/indicator = links, focus underline and selection marks (lighter in dark mode, as in Fluent 2).
ACCENTS = {
    "blue": ("Blue", ("#0F6CBD", "#115EA3", "#0F548C", "#0F6CBD", "#EBF3FC"),
             ("#115EA3", "#0F6CBD", "#0C3B5E", "#479EF5", "#082338")),
    "teal": ("Teal", ("#038387", "#026E72", "#025E61", "#038387", "#E6F5F5"),
             ("#026E72", "#038387", "#025256", "#3BC3C8", "#062A2C")),
    "green": ("Green", ("#107C10", "#0E700E", "#0C5E0C", "#107C10", "#EAF5EA"),
              ("#0E700E", "#107C10", "#0B530B", "#54B054", "#0B2A0B")),
    "purple": ("Purple", ("#6B4FBB", "#5B3FA8", "#4D3490", "#6B4FBB", "#F1EEFA"),
               ("#5B3FA8", "#6B4FBB", "#4D3490", "#A99BE8", "#241E43")),
    "magenta": ("Magenta", ("#C239B3", "#A62E99", "#8C2681", "#B02FA2", "#FBEEF9"),
                ("#A62E99", "#C239B3", "#8C2681", "#E88AE0", "#3A1236")),
    "orange": ("Orange", ("#BC4B09", "#A24008", "#873707", "#BC4B09", "#FCEFE6"),
               ("#A24008", "#BC4B09", "#873707", "#F0A56B", "#3A1C08")),
    "red": ("Red", ("#C50F1F", "#A80D1A", "#8C0B16", "#C50F1F", "#FDF3F4"),
            ("#A80D1A", "#C50F1F", "#8C0B16", "#EE5F5B", "#3B0D11")),
}

_LIGHT = dict(
    canvas="#FAFAFA", card="#FFFFFF", sidebar="#F5F5F5", input="#FFFFFF", hover="#F0F0F0", pressed="#E0E0E0",
    alt="#FAFAFA", row_hover="#F5F5F5", disabled_bg="#F0F0F0", fg1="#242424", fg2="#424242", fg3="#616161",
    fg_dis="#BDBDBD", muted="#707070", stroke1="#D1D1D1", stroke2="#E0E0E0", stroke_ctl="#616161",
    success="#107C10", danger="#B10E1C", success_bg="#F1FAF1", success_bd="#9FD89F", error_bg="#FDF3F4",
    error_bd="#EEACB2", scope_space="#6B4FA3", syn_num="#B5530A", syn_lit="#8764B8", syn_str="#0B7A3B",
    smart="#C239B3", gutter_bg="#F5F5F5", gutter_fg="#9A9A9A", scroll="#BDBDBD", shadow=40,
    fc=dict(accent=dict(trigger="#0F6CBD", action="#107C10", condition="#BC4B09", branch="#8764B8"),
            tint=dict(trigger="#F3F8FD", action="#F4FBF4", condition="#FEF8F4", branch="#F8F5FC"),
            text="#1A1A1A", text2="#1F1F1F", sub="#5A5A5A", border="#C8C8C8", arrow="#707070", pill_bg="#FFFFFF",
            pill_text="#424242", smart="#A02B93", card="#FFFFFF"),
)
_DARK = dict(
    canvas="#1F1F1F", card="#292929", sidebar="#171717", input="#292929", hover="#383838", pressed="#2E2E2E",
    alt="#252525", row_hover="#333333", disabled_bg="#333333", fg1="#FFFFFF", fg2="#D6D6D6", fg3="#ADADAD",
    fg_dis="#6B6B6B", muted="#9E9E9E", stroke1="#666666", stroke2="#454545", stroke_ctl="#ADADAD",
    success="#6CCB5F", danger="#F1707B", success_bg="#10250F", success_bd="#2E6B2A", error_bg="#3B0D11",
    error_bd="#8E2B2F", scope_space="#B79DE8", syn_num="#F0A56B", syn_lit="#B79DE8", syn_str="#7FD38A",
    smart="#E88AE0", gutter_bg="#1A1A1A", gutter_fg="#7A7A7A", scroll="#6B6B6B", shadow=120,
    fc=dict(accent=dict(trigger="#479EF5", action="#54B054", condition="#F0A56B", branch="#B79DE8"),
            tint=dict(trigger="#13283D", action="#142B16", condition="#33210F", branch="#271E3B"),
            text="#F5F5F5", text2="#E6E6E6", sub="#B0B0B0", border="#5C5C5C", arrow="#A6A6A6", pill_bg="#2B2B2B",
            pill_text="#E0E0E0", smart="#E88AE0", card="#292929"),
)

T: dict = {}  # the active design tokens; updated by apply() and read everywhere colours are needed


def tokens(dark: bool, accent: str = "blue") -> dict:
    fill, hover, pressed, fg, tint = ACCENTS.get(accent, ACCENTS["blue"])[2 if dark else 1]
    t = dict(_DARK if dark else _LIGHT)
    t.update(brand=fill, brand_hover=hover, brand_pressed=pressed, brand_fg=fg, brand_tint=tint, on_brand="#FFFFFF",
             dark=dark, accent=accent)
    return t


@dataclass
class Appearance:
    mode: str = "system"       # light | dark | system
    accent: str = "blue"
    text_size: str = "default"

    @property
    def px(self) -> int:
        return next((p for k, _, p in TEXT_SIZES if k == self.text_size), 14)

    def normalize(self) -> "Appearance":
        if self.mode not in {k for k, _ in MODES}:
            self.mode = "system"
        if self.accent not in ACCENTS:
            self.accent = "blue"
        if self.text_size not in {k for k, _, _ in TEXT_SIZES}:
            self.text_size = "default"
        return self

    @classmethod
    def load(cls, path: Path) -> "Appearance":
        try:
            d = json.loads(Path(path).read_text("utf-8-sig"))  # tolerate a BOM added by editors
            return cls(**{k: v for k, v in d.items() if k in {f.name for f in fields(cls)}}).normalize()
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: Path):
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(asdict(self), indent=2), "utf-8")
        except OSError:
            pass


def system_is_dark() -> bool:
    try:
        return QGuiApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except AttributeError:  # Qt < 6.5
        return False


def is_dark(ap: Appearance) -> bool:
    return ap.mode == "dark" or (ap.mode == "system" and system_is_dark())


def make_palette(t: dict) -> QPalette:
    """Native-drawn parts (check boxes, scroll areas, dialogs, tooltips) follow the same tokens."""
    p = QPalette()
    for role, key in ((QPalette.ColorRole.Window, "canvas"), (QPalette.ColorRole.WindowText, "fg1"),
                      (QPalette.ColorRole.Base, "card"), (QPalette.ColorRole.AlternateBase, "alt"),
                      (QPalette.ColorRole.Text, "fg1"), (QPalette.ColorRole.Button, "card"),
                      (QPalette.ColorRole.ButtonText, "fg1"), (QPalette.ColorRole.ToolTipBase, "card"),
                      (QPalette.ColorRole.ToolTipText, "fg1"), (QPalette.ColorRole.PlaceholderText, "fg3"),
                      (QPalette.ColorRole.Highlight, "brand"), (QPalette.ColorRole.HighlightedText, "on_brand"),
                      (QPalette.ColorRole.Link, "brand_fg"), (QPalette.ColorRole.Mid, "stroke_ctl"),
                      (QPalette.ColorRole.Dark, "stroke_ctl"), (QPalette.ColorRole.Light, "card")):
        p.setColor(role, QColor(t[key]))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        p.setColor(QPalette.ColorGroup.Disabled, role, QColor(t["fg_dis"]))
    return p


def _indicator_images(t: dict) -> dict:
    """Fluent-style check box images (outlined / accent-filled with a check / with a dash) for the active theme."""
    from tempfile import gettempdir
    from PyQt6.QtCore import QPointF, QRectF
    from PyQt6.QtGui import QImage, QPainter, QPen
    d = Path(gettempdir()) / "workflow_exporter_theme"
    try:
        d.mkdir(exist_ok=True)
    except OSError:
        return {}
    key = f"{'d' if t['dark'] else 'l'}_{t['brand'][1:]}_{t['stroke_ctl'][1:]}_{t['input'][1:]}"
    out = {}
    for state in ("unchecked", "checked", "partial"):
        path = d / f"cb_{key}_{state}.png"
        if not path.exists():
            img = QImage(20, 20, QImage.Format.Format_ARGB32)
            img.fill(Qt.GlobalColor.transparent)
            p = QPainter(img); p.setRenderHint(QPainter.RenderHint.Antialiasing)
            rect = QRectF(1.5, 1.5, 17, 17)
            if state == "unchecked":
                p.setPen(QPen(QColor(t["stroke_ctl"]), 1.3)); p.setBrush(QColor(t["input"]))
            else:
                p.setPen(Qt.PenStyle.NoPen); p.setBrush(QColor(t["brand"]))
            p.drawRoundedRect(rect, 4, 4)
            if state != "unchecked":
                p.setPen(QPen(QColor("#FFFFFF"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                pts = ([QPointF(5.5, 10.5), QPointF(8.7, 13.8), QPointF(14.5, 6.5)] if state == "checked"
                       else [QPointF(5.8, 10), QPointF(14.2, 10)])
                for a, b in zip(pts, pts[1:]):
                    p.drawLine(a, b)
            p.end()
            img.save(str(path), "PNG")
        out[state] = path.as_posix()
    return out


def build_qss(t: dict, px: int = 14) -> str:
    s = px / 14
    ind = _indicator_images(t)
    ind_qss = "" if not ind else "\n".join((
        "QCheckBox::indicator, QTableView::indicator { width: 20px; height: 20px; }",
        f"QCheckBox::indicator:unchecked, QTableView::indicator:unchecked {{ image: url({ind['unchecked']}); }}",
        f"QCheckBox::indicator:checked, QTableView::indicator:checked {{ image: url({ind['checked']}); }}",
        f"QCheckBox::indicator:indeterminate, QTableView::indicator:indeterminate {{ image: url({ind['partial']}); }}",
        ""))
    def r(v):
        return int(round(v * s))

    return f"""
* {{ font-family: {FONT_STACK}; font-size: {px}px; color: {t['fg1']}; }}
QMainWindow, QDialog, QMessageBox, QWidget#page {{ background: {t['canvas']}; }}
QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {r(28)}px; font-weight: 600; }}
QLabel[role="subtitle"] {{ font-size: {r(24)}px; font-weight: 600; }}
QLabel[role="heading"] {{ font-size: {r(16)}px; font-weight: 600; }}
QLabel[role="navgroup"] {{ font-size: {max(11, px - 2)}px; font-weight: 600; color: {t['fg3']}; padding: 12px 12px 2px 12px; }}
QLabel[role="caption"] {{ font-size: {max(11, px - 2)}px; color: {t['fg3']}; }}
QLabel[role="field"] {{ font-weight: 600; }}
QFrame#card {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px; }}
QFrame#card QLabel {{ background: transparent; }}
QFrame#sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['stroke2']}; }}
QLabel[role="navtitle"] {{ font-size: {r(16)}px; font-weight: 600; padding: 4px 8px; }}
QPushButton[nav="true"] {{ background: transparent; border: none; border-left: 3px solid transparent;
    border-radius: 4px; text-align: left; padding: {r(8)}px {r(12)}px; font-weight: 400; }}
QPushButton[nav="true"]:hover {{ background: {t['hover']}; }}
QPushButton[nav="true"]:checked {{ background: {t['brand_tint']}; border-left: 3px solid {t['brand_fg']}; font-weight: 600; }}
QPushButton[nav="true"]:disabled {{ color: {t['fg_dis']}; background: transparent; border-left: 3px solid transparent; }}
QLabel#success {{ background: {t['success_bg']}; border: 1px solid {t['success_bd']}; border-radius: 4px;
                  color: {t['success']}; padding: 8px 12px; }}
QLabel#error {{ background: {t['error_bg']}; border: 1px solid {t['error_bd']}; border-radius: 4px;
                color: {t['danger']}; padding: 8px 12px; }}

QLineEdit, QComboBox {{ background: {t['input']}; border: 1px solid {t['stroke1']};
    border-bottom: 1px solid {t['stroke_ctl']}; border-radius: 4px; padding: {r(5)}px {r(10)}px; min-height: {r(22)}px;
    selection-background-color: {t['brand']}; selection-color: {t['on_brand']}; }}
QLineEdit:hover, QComboBox:hover {{ border-color: {t['fg3']}; border-bottom-color: {t['fg2']}; }}
QLineEdit:focus, QComboBox:focus {{ border-bottom: 2px solid {t['brand_fg']}; }}
QLineEdit:disabled, QComboBox:disabled {{ background: {t['disabled_bg']}; color: {t['fg_dis']}; border-bottom-color: {t['stroke1']}; }}
QComboBox QAbstractItemView {{ background: {t['card']}; border: 1px solid {t['stroke2']}; outline: 0;
    selection-background-color: {t['brand_tint']}; selection-color: {t['fg1']}; }}

QPushButton {{ background: {t['card']}; border: 1px solid {t['stroke1']}; border-radius: 4px;
    padding: {r(5)}px {r(14)}px; min-height: {r(22)}px; font-weight: 600; }}
QPushButton:hover {{ background: {t['hover']}; border-color: {t['fg3']}; }}
QPushButton:pressed {{ background: {t['pressed']}; }}
QPushButton:focus {{ border: 1px solid {t['brand_fg']}; }}
QPushButton:disabled {{ color: {t['fg_dis']}; background: {t['disabled_bg']}; border-color: {t['stroke2']}; }}
QPushButton[nav="true"]:focus {{ border: none; border-left: 3px solid transparent; }}
QPushButton[nav="true"]:checked:focus {{ border: none; border-left: 3px solid {t['brand_fg']}; }}
QPushButton[primary="true"] {{ background: {t['brand']}; border-color: {t['brand']}; color: {t['on_brand']}; }}
QPushButton[primary="true"]:hover {{ background: {t['brand_hover']}; border-color: {t['brand_hover']}; }}
QPushButton[primary="true"]:pressed {{ background: {t['brand_pressed']}; }}
QPushButton[primary="true"]:focus {{ border: 2px solid {t['fg1']}; }}
QPushButton[primary="true"]:disabled {{ background: {t['disabled_bg']}; color: {t['fg_dis']}; border-color: {t['stroke2']}; }}
QPushButton[danger="true"]:enabled {{ background: #C50F1F; border-color: #C50F1F; color: #FFFFFF; }}
QPushButton[danger="true"]:enabled:hover {{ background: #A80D1A; border-color: #A80D1A; }}
QPushButton[danger="true"]:enabled:pressed {{ background: #8C0B16; border-color: #8C0B16; }}
QPushButton[danger="true"]:enabled:focus {{ border: 2px solid {t['fg1']}; }}
QPushButton[session="true"] {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px; text-align: left;
    padding: {r(8)}px {r(12)}px; font-weight: 400; }}
QPushButton[session="true"]:hover {{ background: {t['error_bg']}; border-color: {t['error_bd']}; color: {t['danger']}; font-weight: 600; }}
QPushButton[session="true"]:pressed {{ background: {t['error_bd']}; }}
QPushButton[subtle="true"] {{ background: transparent; border-color: transparent; }}
QPushButton[subtle="true"]:hover {{ background: {t['hover']}; }}
QPushButton[seg="true"] {{ font-weight: 600; padding: {r(6)}px {r(18)}px; }}
QPushButton[seg="true"]:checked {{ background: {t['brand_tint']}; border: 1px solid {t['brand_fg']}; color: {t['brand_fg']};
    font-weight: 600; }}

QTableWidget {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px;
    gridline-color: transparent; alternate-background-color: {t['alt']};
    selection-background-color: {t['brand_tint']}; selection-color: {t['fg1']}; outline: 0; }}
QTableWidget::item:hover {{ background: {t['row_hover']}; }}
QTableWidget::item {{ padding: 6px 12px; border-bottom: 1px solid {t['stroke2']}; }}
QTableWidget::item:selected {{ background: {t['brand_tint']}; color: {t['fg1']}; }}
QHeaderView::section {{ background: {t['card']}; border: none; border-bottom: 1px solid {t['stroke1']};
    padding: 8px 12px; font-weight: 600; font-size: {px}px; color: {t['fg2']}; }}

QTabWidget::pane {{ border: none; background: transparent; }}
QTabBar {{ background: transparent; }}
QTabBar::tab {{ background: transparent; color: {t['fg2']}; border: none; border-bottom: 2px solid transparent;
    padding: {r(8)}px {r(14)}px; margin-right: 2px; font-weight: 400; }}
QTabBar::tab:hover {{ background: {t['hover']}; color: {t['fg1']}; border-radius: 4px; }}
QTabBar::tab:selected {{ color: {t['fg1']}; font-weight: 600; border-bottom: 2px solid {t['brand_fg']}; }}
QTextBrowser#ruleinfo {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px; padding: 8px; }}
QPlainTextEdit {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px; padding: 8px;
    font-family: "Cascadia Mono", "Cascadia Code", Consolas, "Courier New", monospace; font-size: {max(12, px - 1)}px;
    selection-background-color: {t['brand']}; selection-color: {t['on_brand']}; }}
QListWidget {{ background: {t['card']}; border: 1px solid {t['stroke2']}; border-radius: 8px; padding: 6px; outline: 0; }}
QListWidget::item {{ background: {t['alt']}; border: 1px solid {t['stroke2']}; border-radius: 6px; padding: 9px 12px; margin: 2px 0; }}
QListWidget::item:hover {{ background: {t['hover']}; }}
QListWidget::item:selected {{ background: {t['brand_tint']}; border: 1px solid {t['brand_fg']}; color: {t['fg1']}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
{ind_qss}QGraphicsView {{ background: {t['sidebar']}; border: 1px solid {t['stroke2']}; border-radius: 8px; }}
QScrollArea {{ background: transparent; border: none; }}
QSplitter::handle {{ background: transparent; width: 10px; }}

QProgressBar {{ background: {t['stroke2']}; border: none; border-radius: 2px; max-height: 4px; }}
QProgressBar::chunk {{ background: {t['brand_fg']}; border-radius: 2px; }}
QScrollBar:vertical {{ background: transparent; width: 12px; }}
QScrollBar::handle:vertical {{ background: {t['scroll']}; border-radius: 3px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {t['scroll']}; border-radius: 3px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QToolTip {{ background: {t['card']}; color: {t['fg1']}; border: 1px solid {t['stroke1']}; padding: 4px 8px; }}
"""


def apply(app, ap: Appearance) -> dict:
    """Make `ap` the active appearance: tokens, palette, application font and style sheet."""
    ap.normalize()
    t = tokens(is_dark(ap), ap.accent)
    t["px"], t["scale"] = ap.px, ap.px / 14
    T.clear(); T.update(t)
    f = QFont()
    f.setFamilies(FONT_FAMILIES); f.setPixelSize(ap.px)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality)
    app.setFont(f)
    app.setPalette(make_palette(t))
    app.setStyleSheet(build_qss(t, ap.px))
    return t


# importing modules (and tests) can rely on usable tokens before apply() is called
T.update(tokens(False))
T["px"], T["scale"] = 14, 1.0
QSS = build_qss(T, 14)
