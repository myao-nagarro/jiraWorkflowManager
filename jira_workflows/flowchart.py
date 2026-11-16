"""Interactive workflow flowchart in the Jira style: START dot, statuses as tinted boxes with their ID,
transitions as right-angle arrows carrying a pill with the transition name and ID."""
import math

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QIcon, QImage, QKeySequence, QPainter, QPainterPath,
                         QPainterPathStroker, QPen, QPixmap, QPolygonF)
from PyQt6.QtWidgets import (QFrame, QGraphicsDropShadowEffect, QGraphicsItem, QGraphicsPathItem, QGraphicsPolygonItem, QGraphicsScene,
                             QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QPushButton)

from .fluent import T, tokens
from .layout import build_graph
from .routing import GAP_X, route

NODE_H = 54
# default horizontal distance between statuses (a multiple of the router's base gap) by the number of statuses in the widest row:
# a diagram with few statuses per row is drawn compactly, a crowded one gets room for its lines
SPREAD_BY_ROW = {1: 1.0, 2: 1.0, 3: 1.5, 4: 2.0, 5: 2.5}
MAX_SPREAD = 3.0


def _font(px, bold=False):
    f = QFont(); f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Arial"]); f.setPixelSize(max(5, int(round(px)))); f.setBold(bold)
    return f


def _line_color():
    return QColor(T["fg3"] if not T["dark"] else T["fg3"])


MONO = {"TODO": ("#FFFFFF", "#5C5C5C"), "IN_PROGRESS": ("#EDEDED", "#424242"), "DONE": ("#D6D6D6", "#242424")}   # grey levels, for printing


def _status_colors(category, mono=False):
    if mono:
        return MONO[category]
    if category == "DONE":
        return T["success_bg"], T["success"]
    if category == "TODO":
        return T["hover"], T["stroke1"]
    return T["brand_tint"], T["brand_fg"]


class NodeItem(QGraphicsPathItem):
    def __init__(self, view, node, rect: QRectF):
        super().__init__()
        self.view, self.node = view, node
        self.base_pen, self.halo, self.title = None, None, None
        path = QPainterPath()
        if node.kind == "start":
            path.addEllipse(rect)
            fill, border = T["fg1"], T["fg1"]
        else:
            path.addRoundedRect(rect, 4, 4)
            fill, border = _status_colors(node.category, view.mono)
        self.setPath(path)
        self.base_pen = QPen(QColor(border), 1.6)
        self.setPen(self.base_pen); self.setBrush(QBrush(QColor(fill)))
        self.setZValue(2); self.setAcceptHoverEvents(True)
        if node.kind == "status":
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{node.label}\n{node.sub}" if node.sub else node.label)

        if node.kind == "start":
            t = QGraphicsSimpleTextItem("START", self); t.setFont(_font(9 * view.node_scale, True)); t.setBrush(QColor(T["canvas"]))
            tb = t.boundingRect(); t.setPos(rect.center().x() - tb.width() / 2, rect.center().y() - tb.height() / 2)
            return
        fm = QFontMetricsF(_font(12 * view.node_scale, True))
        title = QGraphicsSimpleTextItem(fm.elidedText(node.label.upper(), Qt.TextElideMode.ElideRight, rect.width() - 16), self)
        title.setFont(_font(12 * view.node_scale, True)); title.setBrush(QColor(T["fc"]["text"])); self.title = title
        sub = QGraphicsSimpleTextItem(node.sub, self); sub.setFont(_font(10 * view.node_scale)); sub.setBrush(QColor(T["fc"]["sub"]))
        tb, sb = title.boundingRect(), sub.boundingRect()
        top = rect.center().y() - (tb.height() + sb.height()) / 2
        title.setPos(rect.center().x() - tb.width() / 2, top)
        sub.setPos(rect.center().x() - sb.width() / 2, top + tb.height())

    def set_active(self, on):
        """The selected status: a soft accent halo around the box, a thick accent outline and bold text."""
        if self.node.kind != "status":
            return
        accent = QColor(T["brand_fg"])
        self.setPen(QPen(accent, 3.4) if on else self.base_pen)
        if on and self.halo is None:
            r = self.path().boundingRect().adjusted(-9, -9, 9, 9)
            p = QPainterPath(); p.addRoundedRect(r, 11, 11)
            self.halo = QGraphicsPathItem(p, self)
            glow = QColor(accent); glow.setAlpha(70 if not T["dark"] else 95)
            self.halo.setBrush(QBrush(glow)); self.halo.setPen(QPen(Qt.PenStyle.NoPen))
            self.halo.setFlag(QGraphicsItem.GraphicsItemFlag.ItemStacksBehindParent)
        if self.halo is not None:
            self.halo.setVisible(on)
        self.title.setBrush(QColor(T["brand_fg"] if on else T["fc"]["text"]))

    def hoverEnterEvent(self, e):
        if self.view.locked is None and self.node.kind == "status":
            self.view.focus(node=self.node.id)

    def hoverLeaveEvent(self, e):
        if self.view.locked is None:
            self.view.focus()

    def mousePressEvent(self, e):
        if self.node.kind == "status" and e.button() == Qt.MouseButton.LeftButton:
            self.view.select_node(self.node.id)
            e.accept()
        else:
            super().mousePressEvent(e)


def _bolt(x, y, color):
    p = QPolygonF([QPointF(x + 7, y), QPointF(x + 1, y + 7.5), QPointF(x + 5, y + 7.5), QPointF(x + 3, y + 14),
                   QPointF(x + 10, y + 5.5), QPointF(x + 6, y + 5.5)])
    it = QGraphicsPolygonItem(p); it.setPen(QPen(Qt.PenStyle.NoPen)); it.setBrush(QBrush(color))
    return it


def _tree(x, y, color):
    path = QPainterPath()
    path.addRect(QRectF(x + 4.5, y + 0.5, 4, 4)); path.addRect(QRectF(x + 0.5, y + 9.5, 4, 4)); path.addRect(QRectF(x + 8.5, y + 9.5, 4, 4))
    path.moveTo(x + 6.5, y + 4.5); path.lineTo(x + 6.5, y + 7); path.moveTo(x + 2.5, y + 9.5); path.lineTo(x + 2.5, y + 7)
    path.lineTo(x + 10.5, y + 7); path.lineTo(x + 10.5, y + 9.5)
    it = QGraphicsPathItem(path); it.setPen(QPen(color, 1.3)); it.setBrush(QBrush(Qt.BrushStyle.NoBrush))
    return it


class Pill(QGraphicsPathItem):
    """A transition label; clicking it selects the transition."""

    def __init__(self, view, tid):
        super().__init__()
        self.view, self.tid = view, tid
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.view.select_edge(self.tid)
            e.accept()
        else:
            super().mousePressEvent(e)


PILL_FONT, PILL_H = 11, 22   # transition labels are small: one text size, one height


def pill_text(name, tid):
    return f"{name or 'Transition'} · ID {tid}"


def pill_width(text, icons=(False, False)):
    return QFontMetricsF(_font(PILL_FONT)).horizontalAdvance(text) + 16 + 15 * sum(bool(i) for i in icons)


def make_pill(text, icons, category, view, tid):
    """Compact rounded label [bolt] [tree] name · ID n, filled with the colour of the status the transition leads to.
    It is centred on its own origin, so setPos() puts its centre on the line and setRotation() turns it along its line when there is no room to keep it horizontal."""
    dark = QColor(T["fc"]["text"])
    pill = Pill(view, tid)
    w = pill_width(text, icons)
    t = QGraphicsSimpleTextItem(text, pill); t.setFont(_font(PILL_FONT)); t.setBrush(dark)
    tb = t.boundingRect()
    x = -w / 2 + 8
    for flag, make in zip(icons, (_bolt, _tree)):
        if flag:
            ic = make(x, 0, dark); ic.setParentItem(pill); ic.setPos(0, -6.5); x += 15
    t.setPos(x, -tb.height() / 2)
    box = QRectF(-w / 2, -PILL_H / 2, w, PILL_H)
    p = QPainterPath(); p.addRoundedRect(box, PILL_H / 2, PILL_H / 2)
    pill.setPath(p); fill, border = _status_colors(category, view.mono)
    pill.setPen(QPen(QColor(border), 1.2)); pill.setBrush(QBrush(QColor(fill)))
    pill.setZValue(4)
    return pill, w


def line_path(pts, curved):
    """The polyline as a path. Curved: every corner becomes a smooth bend (as wide as the neighbouring lines allow)."""
    path = QPainterPath(QPointF(*pts[0]))
    for i in range(1, len(pts)):
        if not curved or i == len(pts) - 1:
            path.lineTo(QPointF(*pts[i]))
            continue
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l_in, l_out = math.hypot(x1 - x0, y1 - y0), math.hypot(x2 - x1, y2 - y1)
        r = min(80.0, l_in / 2, l_out / 2)
        path.lineTo(QPointF(x1 - (x1 - x0) / l_in * r, y1 - (y1 - y0) / l_in * r))
        path.quadTo(QPointF(x1, y1), QPointF(x1 + (x2 - x1) / l_out * r, y1 + (y2 - y1) / l_out * r))
    return path


class HoverPath(QGraphicsPathItem):
    """A line that is easy to hover (wide invisible hit area) and reports hovering to the chart."""

    def __init__(self, path, view, tid):
        super().__init__(path)
        self.view, self.tid = view, tid
        self.setAcceptHoverEvents(True)

    def shape(self):
        s = QPainterPathStroker(); s.setWidth(12)
        return s.createStroke(self.path())

    def hoverEnterEvent(self, e):
        if self.view.locked is None:
            self.view.focus(tid=self.tid)

    def hoverLeaveEvent(self, e):
        if self.view.locked is None:
            self.view.focus()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.view.select_edge(self.tid)
            e.accept()
        else:
            super().mousePressEvent(e)


def _chevron(x0, y0, x1, y1, color):
    ang = math.atan2(y1 - y0, x1 - x0)
    a, b = 10, 0.55
    ah = QPainterPath(); ah.moveTo(x1 - a * math.cos(ang - b), y1 - a * math.sin(ang - b)); ah.lineTo(x1, y1)
    ah.lineTo(x1 - a * math.cos(ang + b), y1 - a * math.sin(ang + b))
    it = QGraphicsPathItem(ah)
    it.setPen(QPen(color, 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    it.setZValue(1)
    return it


class EdgeItem:
    """One transition (or a pair running in opposite directions): orthogonal line, chevron head(s), label pill(s)."""

    def __init__(self, view, edge, pts, label_pos, turned, icons):
        self.edge = edge
        self.members = [edge] + edge.also
        color = _line_color()
        self.line = HoverPath(line_path(pts, view.curved), view, edge.tid)
        self.base_color = color
        self.line.setCursor(Qt.CursorShape.PointingHandCursor)
        self.line.setPen(self._pen(color, 2.0)); self.line.setZValue(1)
        self.line.setToolTip("\n".join(f"{m.name or 'Transition'} · ID {m.tid}" for m in self.members))
        view.scene().addItem(self.line)
        self.arrows = [_chevron(*pts[-2], *pts[-1], color)]
        if edge.also:  # the opposite transition points back at the source
            self.arrows.append(_chevron(*pts[1], *pts[0], color))
        for a in self.arrows:
            view.scene().addItem(a)

        self.labels = []
        for k, m in enumerate(self.members):   # the two directions of a pair are stacked across the line, both on it
            pill, _ = make_pill(pill_text(m.name, m.tid), icons.get(m.tid, (False, False)),
                                view._layout[0].nodes[m.dst].category, view, m.tid)
            off = (k - (len(self.members) - 1) / 2) * PILL_H
            pill.setPos(label_pos[0] + (off if turned else 0), label_pos[1] + (0 if turned else off))
            if turned:
                pill.setRotation(-90)   # the text runs along the line
            pill.setToolTip(f"Transition ID {m.tid}: {m.name}")
            view.scene().addItem(pill); self.labels.append(pill)

    @staticmethod
    def _pen(color, width):
        pen = QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        return pen

    def highlight(self, on):
        """Selected / hovered transition: accent colour and a thicker line."""
        color, width = (QColor(T["brand_fg"]), 3.2) if on else (self.base_color, 2.0)
        self.line.setPen(self._pen(color, width))
        for a in self.arrows:
            a.setPen(self._pen(color, width))
        self.line.setZValue(3 if on else 1)

    def set_opacity(self, v):
        for it in [self.line, *self.arrows, *self.labels]:
            it.setOpacity(v)

    def set_labels(self, on, tid=None):
        """Show / hide the label pills. With a transition ID only that transition's pill (one direction of a two-way line)."""
        for k, m in enumerate(self.members):
            self.labels[k].setVisible(on and (tid is None or m.tid == tid))

    def show_direction(self, tid):
        """Two-way line: keep only the arrowhead of the selected transition (None: both heads)."""
        for k, a in enumerate(self.arrows):
            owners = self.members[:1] if k == 0 else self.members[1:]
            a.setVisible(tid is None or any(m.tid == tid for m in owners))

    def touches(self, tid, node):
        return any(m.tid == tid for m in self.members) or node in (self.edge.src, self.edge.dst)

    def nodes(self):
        return {self.edge.src, self.edge.dst}


class AnyTag:
    """Global ("from any status") transitions into one status: a dark "Any" badge on the box plus a pill per transition."""

    def __init__(self, view, edges, rect: QRectF, icons):
        self.edges, self.dst = edges, edges[0].dst
        sc = view.scene()
        bw, bh = 46, 22
        badge = QRectF(rect.right() - 30 - bw / 2, rect.top() - 30 - bh / 2, bw, bh)
        p = QPainterPath(); p.addRoundedRect(badge, bh / 2, bh / 2)
        b = QGraphicsPathItem(p); b.setPen(QPen(Qt.PenStyle.NoPen)); b.setBrush(QBrush(QColor(T["fg1"]))); b.setZValue(3)
        b.setToolTip("Any status: these transitions can start from every status")
        t = QGraphicsSimpleTextItem("Any", b); t.setFont(_font(12, True)); t.setBrush(QColor(T["canvas"]))
        tb = t.boundingRect(); t.setPos(badge.center().x() - tb.width() / 2, badge.center().y() - tb.height() / 2)
        x, y0 = badge.center().x(), badge.bottom()
        line = QGraphicsPathItem(QPainterPath(QPointF(x, y0)))
        pth = QPainterPath(QPointF(x, y0)); pth.lineTo(x, rect.top())
        line.setPath(pth); line.setPen(QPen(_line_color(), 2.0)); line.setZValue(1)
        ah = QPainterPath(); ah.moveTo(x - 5.5, rect.top() - 8); ah.lineTo(x, rect.top()); ah.lineTo(x + 5.5, rect.top() - 8)
        arrow = QGraphicsPathItem(ah); arrow.setPen(QPen(_line_color(), 2.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)); arrow.setZValue(1)
        self.items, self.labels = [b, line, arrow], []
        for i, e in enumerate(edges):  # pills stacked to the left of the badge
            pill, w = make_pill(pill_text(e.name, e.tid), icons.get(e.tid, (False, False)),
                                view._layout[0].nodes[e.dst].category, view, e.tid)
            pill.setPos(badge.left() - 8 - w / 2, badge.center().y() - i * (PILL_H + 4))
            pill.setToolTip(f"Transition ID {e.tid}: {e.name} (from any status)")
            self.items.append(pill); self.labels.append(pill)
        for it in self.items:
            sc.addItem(it)

    def set_opacity(self, v):
        for it in self.items:
            it.setOpacity(v)

    def set_labels(self, on, tid=None):
        for e, it in zip(self.edges, self.labels):
            it.setVisible(on and (tid is None or e.tid == tid))

    def show_direction(self, tid):
        pass

    def highlight(self, on):
        pass

    def touches(self, tid, node):
        return node == self.dst or any(e.tid == tid for e in self.edges)

    def nodes(self):
        return {self.dst}


def _reset_icon(color):
    """Counter-clockwise arrow (Fluent 'ArrowReset')."""
    pm = QPixmap(40, 40); pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor(color), 3.4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    p.drawArc(QRectF(8, 8, 24, 24), 70 * 16, 270 * 16)
    p.drawLine(QPointF(24, 6), QPointF(24.5, 14)); p.drawLine(QPointF(24.5, 14), QPointF(16, 13))
    p.end()
    return QIcon(pm)


def auto_spread(widest_row: int) -> float:
    return SPREAD_BY_ROW.get(widest_row, MAX_SPREAD)


def has_jira_positions(wf: dict) -> bool:
    """Did Jira report where its editor draws the statuses (the current workflow API does, the legacy one does not)?"""
    return bool(wf.get("statuses")) and all(s.get("layout") for s in wf["statuses"])


def jira_positions(g, wf: dict, size: dict, extra_width: int, grow: float = 1.0):
    """Centres for the boxes from the X / Y values of the Jira editor, or None when they are not all known.

    Jira's boxes are smaller than ours, so the whole drawing is enlarged just enough that no two boxes come closer than a gap that
    leaves room for lines; `extra_width` stretches it sideways as the Width choice does. The Start circle uses Jira's start position,
    or sits above the top status."""
    if not has_jira_positions(wf):
        return None
    pos = {s["id"]: s["layout"] for s in wf["statuses"]}
    if any(n.kind == "status" and n.id not in pos for n in g.nodes.values()):
        return None
    for n in g.nodes.values():
        if n.kind == "start":
            if wf.get("start_layout"):
                pos[n.id] = wf["start_layout"]
            else:
                top = min(pos.values(), key=lambda p: p[1]); pos[n.id] = (top[0], top[1] - 90)
    ids = [n.id for n in g.nodes.values() if n.id in pos]
    if len(ids) != len(g.nodes):
        return None   # a node that is not a status or the start (an "any" marker) has no place of its own
    gap_x, gap_y = 60, 52
    scale = 1.0
    while scale < 6:
        pts = {i: (pos[i][0] * scale * grow * (1 + extra_width / 100), pos[i][1] * scale * grow) for i in ids}
        ok = all(abs(pts[a][0] - pts[b][0]) >= (size[a][0] + size[b][0]) / 2 + gap_x
                 or abs(pts[a][1] - pts[b][1]) >= (size[a][1] + size[b][1]) / 2 + gap_y for k, a in enumerate(ids) for b in ids[k + 1:])
        if ok:
            return pts
        scale += 0.1
    return pts


def compute_layout(wf: dict, extra_width: int = 0, node_scale: float = 1.0, use_jira: bool = False):
    """(graph, icon flags, node rectangles, routed transitions) of a workflow. The horizontal gaps follow the diagram (compact when
    few statuses share a row) and `extra_width` (0, 25 ... 100 %) widens them further; `node_scale` makes the status boxes
    (and their text) smaller or larger."""
    g = build_graph(wf)
    crowd = len(g.edges) / max(1, len(g.nodes))   # transitions per box: many lines need wide gaps, few lines do not
    spread = max(auto_spread(max(g.layer_sizes.values(), default=1)), min(MAX_SPREAD, max(1.0, (crowd - 1) * 1.5))) * (1 + extra_width / 100)
    icons = {t["id"]: (t.get("bolt", False), t.get("tree", False)) for t in wf["transitions"]}
    fm = QFontMetricsF(_font(12 * node_scale, True))
    ns = node_scale
    size = {n.id: (44 * ns, 44 * ns) if n.kind == "start"
            else (max(120 * ns, min(240 * ns, fm.horizontalAdvance(n.label.upper()) + 40 * ns)), NODE_H * ns)
            for n in g.nodes.values()}
    def pill_w(e):
        return pill_width(pill_text(e.name, e.tid), icons.get(e.tid, (False, False)))

    sizes = [(max(pill_w(m) for m in [e, *e.also]), PILL_H * (1 + len(e.also))) for e in g.edges]
    tags = {d: [(pill_w(e), PILL_H) for e in es] for d, es in g.global_in.items()}
    if use_jira and jira_positions(g, wf, size, extra_width):
        # the statuses keep the places Jira gave them, enlarged evenly while a label cannot find room beside its line
        best = None
        for step in range(14):
            jira = jira_positions(g, wf, size, extra_width, 1 + 0.12 * step)
            for refine, wide in ((True, True), (False, False)):
                rects, routes, bad = route(g, size, sizes, tags=tags, refine=refine, wide=wide, positions=jira)
                if best is None or bad < best[2]:  # pylint: disable=unsubscriptable-object
                    best = (rects, routes, bad)
                if bad == 0:
                    break
            if best[2] == 0:
                break
        return g, icons, best[0], best[1]
    best = None
    for step in range(16):   # a label that cannot stay clear of other lines, boxes and labels asks for more room: wider, then taller
        for refine, wide in ((True, True), (False, False), (True, False), (False, True)):   # the full router has the fewest crossings; the plainer ones sometimes leave a label more room
            rects, routes, bad = route(g, size, sizes, gap_x=GAP_X * spread * (1 + 0.15 * step), tags=tags, row_boost=1 + 0.12 * step,
                                       refine=refine, wide=wide)
            if best is None or bad < best[2]:
                best = (rects, routes, bad)
            if bad == 0:
                break
        if best[2] == 0:
            break
    rects, routes, _ = best
    return g, icons, rects, routes


def render_workflow(wf: dict, extra_width: int = 0, labels: bool = False, scale: int = 100, resolution: int = 2,
                    curved: bool = False, mono: bool = False, use_jira: bool = False):
    """The workflow diagram as an image, always in the light theme whatever theme the app uses. Returns (image, display width in px).

    The diagram is drawn at its natural size and the status size is the scale: at 100 % the page shows it 1:1, at 75 % smaller,
    at 150 % larger. `resolution` (1-3) only makes the image sharper: it holds resolution x as many pixels, and the page shows
    it at the display width. extra_width is the pop-up's Width choice (0, 25 ... 100 %); `curved` draws smooth bends instead of
    right angles, `mono` draws the statuses in grey levels and `use_jira` keeps the statuses where the Jira editor has them."""
    saved = dict(T)
    T.clear(); T.update(tokens(False)); T["px"], T["scale"] = 14, 1.0
    try:
        view = FlowchartView()
        view.resize(1200, 800)
        view.labels_on, view.curved, view.mono = labels, curved, mono
        view.extra_width, view.use_jira = extra_width, use_jira
        view.set_workflow(wf)
        display = view.scene().sceneRect().width() * scale / 100
        return view.to_image(display * resolution), int(round(display))
    finally:
        T.clear(); T.update(saved)


class FlowchartView(QGraphicsView):
    selected = pyqtSignal(str, str)   # ("node" | "edge" | "", id): what the user locked the view on

    def __init__(self):
        super().__init__()
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing
                            | QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.nodes, self.edges, self.labels_on, self.curved, self.extra_width = {}, [], False, False, 0
        self.mono = False   # grey instead of the status category colours
        self.use_jira = False   # place the statuses at the X / Y values of the Jira workflow editor
        self.node_scale = 1.0   # size of the status boxes (1.0 = default)
        self._wf = self._layout = None
        self.locked = None                # ("node", status id) | ("edge", transition id) while the view is locked
        self._press = None
        self._build_overlay()
        self.setViewportMargins(0, 68, 0, 0)  # the overlay card sits above the drawing instead of covering it

    # ---- content ----
    def set_workflow(self, wf: dict):
        self._wf = wf
        self.relayout()
        self.fit()

    def set_extra_width(self, percent):
        """Widen the horizontal distance between statuses by 0, 25 ... 100 % (wider = lines stay further from other boxes)."""
        self.extra_width = percent
        if self._wf:
            self.relayout()
            self.fit()

    def set_use_jira(self, on):
        self.use_jira = on
        if self._wf:
            self.relayout()
            self.fit()

    def relayout(self):
        self._layout = compute_layout(self._wf, self.extra_width, self.node_scale, self.use_jira)
        self.draw()

    def set_curved(self, on):
        """Switch between right-angle and curved transition lines (the routing itself is reused)."""
        self.curved = on
        if self._layout:
            self.draw()

    def draw(self):
        sc = self.scene(); sc.clear(); self.nodes, self.edges = {}, []
        sc.setBackgroundBrush(QColor(T["card"]))
        g, icons, rects, routes = self._layout
        for e, pts, lab, turned in routes:
            self.edges.append(EdgeItem(self, e, pts, lab, turned, icons))
        for n in g.nodes.values():
            item = NodeItem(self, n, QRectF(*rects[n.id]))
            sc.addItem(item); self.nodes[n.id] = item
        for dst, edges in g.global_in.items():
            self.edges.append(AnyTag(self, edges, QRectF(*rects[dst]), icons))
        sc.setSceneRect(sc.itemsBoundingRect().adjusted(-50, -50, 50, 50))
        self.set_labels(self.labels_on)
        self._apply_lock()

    # ---- selection ----
    def _build_overlay(self):
        """Card in the top-left corner of the chart: always shows the Reset button and what the view is locked on."""
        self.overlay = QFrame(self); self.overlay.setObjectName("card")
        shadow = QGraphicsDropShadowEffect(blurRadius=16, yOffset=2); shadow.setColor(QColor(0, 0, 0, 38))
        self.overlay.setGraphicsEffect(shadow)
        row = QHBoxLayout(self.overlay); row.setContentsMargins(8, 8, 14, 8); row.setSpacing(10)
        self.reset_btn = QPushButton("Reset view"); self.reset_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reset_btn.setToolTip("Clear the selection and fit the whole workflow (Esc)")
        self.reset_btn.setShortcut(QKeySequence("Ctrl+0"))
        self.reset_btn.clicked.connect(self.reset_view)
        self.hint = QLabel(); self.hint.setProperty("role", "caption")
        row.addWidget(self.reset_btn); row.addWidget(self.hint)
        self._update_overlay()

    def _update_overlay(self):
        locked = self.locked is not None
        self.reset_btn.setProperty("primary", locked)
        self.reset_btn.setIcon(_reset_icon(T["on_brand"] if locked else T["fg1"]))
        self.reset_btn.style().unpolish(self.reset_btn); self.reset_btn.style().polish(self.reset_btn)
        if not locked:
            self.hint.setText("Click a status or a transition to lock the view")
        elif self.locked[0] == "node":
            n = self._layout[0].nodes[self.locked[1]]
            self.hint.setText(f"Locked on status  {n.label}  ·  {n.sub}")
        else:
            m = next((m for e in self.edges for m in getattr(e, "members", getattr(e, "edges", [])) if m.tid == self.locked[1]), None)
            self.hint.setText(f"Locked on transition  {(m.name if m else '') or 'Transition'}  ·  ID {self.locked[1]}")
        self.overlay.adjustSize(); self.overlay.move(14, 8); self.overlay.raise_()

    def select_node(self, nid):
        self.lock("node", nid)

    def select_edge(self, tid):
        self.lock("edge", tid)

    def lock(self, kind, ident):
        """Lock the view on a status or transition (hover no longer changes it) and tell the tables."""
        if self.locked == (kind, ident):
            return
        self.locked = (kind, ident)
        self._apply_lock()
        self.selected.emit(kind, ident)

    def clear_selection(self):
        if self.locked is None:
            return
        self.locked = None
        self._apply_lock()
        self.selected.emit("", "")

    def reset_view(self):
        """Unlock, clear the selection and fit the whole workflow."""
        self.clear_selection()
        self.fit()

    def _apply_lock(self):
        if self.locked is None:
            self.focus()
        elif self.locked[0] == "node":
            self.focus(node=self.locked[1])
        else:
            self.focus(tid=self.locked[1])
        self._update_overlay()

    def set_labels(self, on):
        """Show the transition name / ID pills on every line (off: only on the lines being hovered or selected)."""
        self.labels_on = on
        for e in self.edges:
            e.set_labels(on)

    def focus(self, node=None, tid=None):
        """Dim everything unrelated to a status (node id) or a transition (transition id); no arguments resets."""
        if node is None and tid is None:
            for it in self.nodes.values():
                it.setOpacity(1); it.set_active(False)
            for e in self.edges:
                e.set_opacity(1); e.set_labels(self.labels_on); e.show_direction(None); e.highlight(False)
            return
        keep_edges = [e for e in self.edges if e.touches(tid, node)]
        keep_nodes = {node} | {x for e in keep_edges for x in e.nodes()}
        for nid, it in self.nodes.items():
            it.setOpacity(1 if nid in keep_nodes else 0.25); it.set_active(nid == node)
        for e in self.edges:
            kept = e in keep_edges
            e.set_labels(self.labels_on or kept, tid if kept else None)
            e.show_direction(tid if kept else None)
            e.set_opacity(1 if kept else 0.15)
            e.highlight(kept)

    # ---- view ----
    def zoom(self, f):
        self.scale(f, f)

    def reset_zoom(self):
        self.resetTransform()

    def fit(self):
        if self.scene().items():
            self.fitInView(self.scene().sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
            if self.transform().m11() > 1.2:  # do not blow up small workflows
                self.resetTransform(); self.centerOn(self.scene().sceneRect().center())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.overlay.move(14, 8)

    def mousePressEvent(self, e):
        self._press = e.pos()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        click = self._press is not None and (e.pos() - self._press).manhattanLength() < 4
        super().mouseReleaseEvent(e)
        if click and self.itemAt(e.pos()) is None:  # a click on empty canvas lets go of the selection
            self.clear_selection()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape and self.locked is not None:
            self.reset_view()
        else:
            super().keyPressEvent(e)

    def wheelEvent(self, e):
        self.zoom(1.15 if e.angleDelta().y() > 0 else 1 / 1.15)

    def to_image(self, width_px=None, scale=2):
        """The whole diagram as an image: `width_px` wide, or `scale` times its natural size."""
        r = self.scene().sceneRect()
        if width_px:
            scale = width_px / r.width()
        img = QImage(max(1, int(r.width() * scale)), max(1, int(r.height() * scale)), QImage.Format.Format_ARGB32)
        img.fill(QColor(T["card"]))
        p = QPainter(img); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.scene().render(p, QRectF(img.rect()), r)
        p.end()
        return img

    def save_png(self, path: str, scale=2) -> bool:
        r = self.scene().sceneRect()
        img = QImage(int(r.width() * scale), int(r.height() * scale), QImage.Format.Format_ARGB32)
        img.fill(QColor(T["card"]))
        p = QPainter(img); p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        self.scene().render(p, QRectF(img.rect()), r)
        p.end()
        return img.save(path)
