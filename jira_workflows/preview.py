"""A mock-up of the generated Confluence page that follows the template settings. It uses the first selected workflow (names,
IDs, statuses, transitions, JSON and the real diagram) when there is one, and sample content otherwise."""
import json

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from .template import ELEMENTS, layout_info
from .textutil import titlecase

INK, MUTED, BAR, LINE, SOFT = "#172B4D", "#6B778C", "#C1C7D0", "#DFE1E6", "#F4F5F7"
BLUE, GREEN, RED = "#0052CC", "#00875A", "#DE350B"
FRACTIONS = {"Narrow": 0.3, "Wide": 0.7, "Equal": 1.0, "Full width": 1.0}
SPACING = 12
CAT = {"TODO": "To Do", "IN_PROGRESS": "In Progress", "DONE": "Done"}
SAMPLE = {"name": "Sample workflow", "id": "0d4e1c7a-…", "scope": "GLOBAL", "project_keys": ["ABC"], "schemes": [{"name": "Sample scheme"}],
          "active": True, "updated": "2026-10-26T10:00:00Z", "property_count": 0,
          "statuses": [{"id": "1", "name": "Open", "category": "TODO", "properties": {}},
                       {"id": "3", "name": "In Progress", "category": "IN_PROGRESS", "properties": {}},
                       {"id": "6", "name": "Done", "category": "DONE", "properties": {}}],
          "transitions": [{"id": "11", "name": "Start progress", "from": ["1"], "to": "3", "type": "directed", "conditions": [], "validators": [],
                           "actions": ["Update field (field=resolution)"], "triggers": [], "properties": {}, "event": ""},
                          {"id": "21", "name": "Done", "from": ["3"], "to": "6", "type": "directed", "conditions": [], "validators": [],
                           "actions": [], "triggers": [], "properties": {}, "event": "10000"}]}


class TemplatePreview(QWidget):
    """Draws the page the way Confluence will lay it out: columns, element order, macros and the diagram format."""

    def __init__(self):
        super().__init__()
        self.t, self.wf, self.image, self.screens, self.site, self.note, self.image_px = None, None, None, {}, "", "", 0
        self._k = 1.0  # text scale: narrow columns get smaller text so titles stay readable
        self.setMinimumWidth(300)

    def set_template(self, template, workflow=None, image: QImage = None, screens=None, site="", note="", image_px=0):
        """screens: {screen id: details} already loaded from Jira; note: why details are missing (loading / not signed in)."""
        self.t, self.wf, self.image = template, workflow or SAMPLE, image
        self.screens, self.site, self.note, self.image_px = screens or {}, site, note, image_px
        self._relayout()
        self.update()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._relayout()

    def _relayout(self):
        if self.t is not None:
            self.setMinimumHeight(self._render(None) + 4)

    def paintEvent(self, e):
        if self.t is not None:
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._render(p)

    # ---- drawing primitives (every one also works with p=None, which only measures) ----
    def _font(self, size, bold=False, mono=False):
        f = QFont()
        f.setFamilies(["Consolas", "Courier New"] if mono else ["Segoe UI Variable Text", "Segoe UI", "Arial"])
        f.setPixelSize(max(6, int(size * self._k)))
        f.setBold(bold)
        return f

    def _text(self, p, x, y, w, text, size=10, bold=False, color=INK, mono=False) -> int:
        f = self._font(size, bold, mono)
        fm = QFontMetrics(f)
        if p:
            p.setFont(f); p.setPen(QColor(color))
            p.drawText(QRectF(x, y, w, fm.height()), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                       fm.elidedText(str(text), Qt.TextElideMode.ElideRight, max(1, int(w))))
        return fm.height()

    @staticmethod
    def _rect(p, x, y, w, h, fill=None, border=None, radius=3, dashed=False, width=1.0):
        if not p:
            return
        pen = QPen(QColor(border), width) if border else QPen(Qt.PenStyle.NoPen)
        if dashed:
            pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(QColor(fill) if fill else Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(x, y, w, h), radius, radius)

    def _heading(self, p, x, y, w, text, level=2) -> int:
        size = {2: 12.5, 3: 11, 4: 10}.get(level, 10)
        return self._text(p, x, y, w, titlecase(text), size, bold=True) + 6

    # ---- page ----
    def _render(self, p) -> int:
        t = self.t
        pad = 12
        page_w = self.width() - 8
        inset = 0 if t.full_width else page_w * 0.09
        cx, cw = 4 + inset + pad, page_w - 2 * inset - 2 * pad
        height = self._content(None, cx, 4 + pad, cw) + 2 * pad
        if p:
            self._rect(p, 4 + inset, 4, page_w - 2 * inset, height, fill="#FFFFFF", border=LINE, radius=4)
            self._content(p, cx, 4 + pad, cw)
        return int(height + 8)

    def _content(self, p, x, y, w) -> float:
        t, y0 = self.t, y
        caps = layout_info(t.layout)[2]
        total = sum(FRACTIONS.get(c, 1.0) for c in caps)
        avail = w - SPACING * (len(caps) - 1)
        widths = [avail * FRACTIONS.get(c, 1.0) / total for c in caps]
        heights = []   # column heights first, so the dashed column outlines can share one height
        for ci, cap in enumerate(caps):
            h = 20
            for e in t.columns[ci]:
                if t.enabled(e):
                    h += self._element(None, e, 0, 0, widths[ci] - 12) + 10
            heights.append(h + 4)
        row_h = max(heights) if heights else 40
        cx = x
        for ci, cap in enumerate(caps):
            if p:
                self._rect(p, cx, y, widths[ci], row_h, border=LINE, dashed=True, radius=4)
                self._text(p, cx + 6, y + 3, widths[ci] - 12, f"Column {ci + 1} · {cap}", 8, color=MUTED)
            ey = y + 20
            for e in t.columns[ci]:
                if t.enabled(e):
                    ey += self._element(p, e, cx + 6, ey, widths[ci] - 12) + 10
            cx += widths[ci] + SPACING
        y += row_h + 10
        if t.details and t.details_full_width:   # the statuses and transitions in their own full-width band
            eh = self._element(None, "details", x + 6, y + 20, w - 12)
            if p:
                self._rect(p, x, y, w, eh + 28, border=LINE, dashed=True, radius=4)
                self._text(p, x + 6, y + 3, w - 12, "Full width · Workflow Details", 8, color=MUTED)
                self._element(p, "details", x + 6, y + 20, w - 12)
            y += eh + 28 + 10
        return y + 4 - y0

    # ---- elements: each returns its height ----
    def _element(self, p, name, x, y, w) -> float:
        k0, self._k = self._k, max(0.7, min(1.0, w / 250))
        try:
            return getattr(self, f"_el_{name}")(p, x, y, w)
        finally:
            self._k = k0

    def _el_toc(self, p, x, y, w) -> float:
        entries = [ELEMENTS[e] for e in ("info", "flow", "details") if self.t.enabled(e)] + (
            ["Raw JSON"] if self.t.code_block else [])
        h = 10 + 14 + 13 * len(entries) + 6
        self._rect(p, x, y, w, h, fill=SOFT, border=LINE)
        self._text(p, x + 8, y + 5, w - 16, "Table of Contents", 9, bold=True)
        ey = y + 24
        for text in entries:
            self._text(p, x + 10, ey, w - 20, text, 8.5, color=BLUE)
            ey += 13
        return h

    def _el_info(self, p, x, y, w) -> float:
        wf = self.wf
        h = self._heading(p, x, y, w, "Workflow information")
        spaces = ", ".join(wf.get("project_keys") or []) or "—"
        schemes = ", ".join(s["name"] for s in wf.get("schemes") or []) or "—"
        rows = [("Name", wf["name"]), ("ID", wf.get("id", "")), ("Scope", wf.get("scope", "")), ("Spaces", spaces), ("Schemes", schemes),
                ("State", "Active" if wf.get("active") else "Inactive")]
        self._rect(p, x, y + h, w, len(rows) * 17 + 14, border=BAR, radius=3, dashed=True)
        self._text(p, x + 4, y + h + 1, w - 8, "Content Properties", 7.5, color=MUTED)
        ty = y + h + 13
        kw = w * 0.32
        for k, v in rows:
            self._rect(p, x + 4, ty, kw, 16, fill=SOFT, border=LINE, radius=0)
            self._rect(p, x + 4 + kw, ty, w - 8 - kw, 16, border=LINE, radius=0)
            self._text(p, x + 8, ty + 1, kw - 8, k, 8.5, bold=True)
            self._text(p, x + 8 + kw, ty + 1, w - 16 - kw, v, 8.5, color=MUTED)
            ty += 17
        return h + 14 + len(rows) * 17 + 2

    @staticmethod
    def _fit(heads, rows):
        """Column shares from the text in each column; a column with no value at all gets the minimum."""
        weights = []
        for c, h in enumerate(heads):
            vals = [str(r[c]) for r in rows]
            if all(v in ("", "—") for v in vals):
                weights.append(0.35)
            else:
                weights.append(max(len(h) * 0.9 + 2, min(max(len(v) for v in vals), 34)))
        total = sum(weights)
        return [wt / total for wt in weights]

    def _table(self, p, x, y, w, heads, rows, fracs=None, links=(), highlight=(), tags=None) -> float:
        """A table with a bold header row; the columns in `links` are blue like page links; `highlight` rows are light green;
        `tags` maps a row to a small green tag drawn under the text of its first cell."""
        tags = tags or {}
        fracs = fracs or self._fit(heads, rows)
        cx = x
        for hd, f in zip(heads, fracs):
            cw = w * f
            self._rect(p, cx, y, cw, 17, fill=SOFT, border=LINE, radius=0)
            self._text(p, cx + 4, y + 1, cw - 6, titlecase(hd), 8, bold=True)
            cx += cw
        ty = y + 17
        for n, r in enumerate(rows):
            cx, rh = x, 29 if n in tags else 16
            for i, (val, f) in enumerate(zip(r, fracs)):
                cw = w * f
                self._rect(p, cx, ty, cw, rh, fill="#E3FCEF" if n in highlight else None, border=LINE, radius=0)
                self._text(p, cx + 4, ty + 1, cw - 6, val, 8, color=BLUE if i in links and val != "—" else INK)
                if i == 0 and n in tags:   # the tag under the name
                    tw = min(cw - 8, 8 + 5.2 * len(tags[n]))
                    self._rect(p, cx + 4, ty + 15, tw, 11, fill="#ABF5D1", radius=2)
                    self._text(p, cx + 7, ty + 14.5, tw - 4, tags[n].upper(), 6.5, bold=True, color="#006644")
                cx += cw
            ty += rh
        return 17 + sum(29 if n in tags else 16 for n in range(len(rows)))

    @staticmethod
    def _short(items, limit=2):
        return ", ".join(items[:limit]) + (f" +{len(items) - limit}" if len(items) > limit else "") if items else "—"

    def _sub(self, p, x, y, w, text) -> float:
        """A heading inside the element (Confluence heading level 3)."""
        return self._heading(p, x, y, w, text, level=3)

    def _el_details(self, p, x, y, w) -> float:
        """Statuses (with links to their transitions), the transitions (with a link to their screen), then the transition screens."""
        wf = self.wf
        names = {s["id"]: s["name"] for s in wf["statuses"]}
        h = self._heading(p, x, y, w, "Workflow Details")
        h += self._sub(p, x, y + h, w, "Statuses")
        rows = []
        for s in wf["statuses"][:4]:
            inc = [t["name"] for t in wf["transitions"] if t["to"] == s["id"]]
            out = [t["name"] for t in wf["transitions"] if s["id"] in t["from"] or (not t["from"] and t.get("type") == "global")]
            props = ", ".join(f"{k}={v}" for k, v in (s.get("properties") or {}).items()) or "—"
            rows.append((s["name"], f"{s['id']}  Edit Status", CAT.get(s["category"], ""), props, self._short(inc), self._short(out)))
        h += self._table(p, x, y + h, w, ["Status", "ID", "Category", "Properties", "Incoming", "Outgoing"], rows, links=(1, 4, 5))
        if len(wf["statuses"]) > 4:
            h += self._text(p, x, y + h + 1, w, f"… {len(wf['statuses']) - 4} more statuses", 8, color=MUTED) + 2
        h += 6
        h += self._sub(p, x, y + h, w, "Transitions")
        rows, marked = [], []
        for n, t in enumerate(wf["transitions"][:4]):
            src = ", ".join(names.get(f, f) for f in t["from"]) or ("Start" if t.get("type") == "initial" else "Any status")
            rules = [f"• {r}" for key in ("conditions", "validators", "actions") for r in (t.get(key) or [])[:1]]
            props = ", ".join(f"{k}={v}" for k, v in (t.get("properties") or {}).items()) or "—"
            portal = str((t.get("properties") or {}).get("servicedesk.customer.transition.active", "")).lower() == "true"
            if portal:
                marked.append(n)
            rows.append((t["name"], f"{t['id']}  Edit Transition", f"{src} → {names.get(t['to'], t['to'])}",
                         t.get("screen_name") or "—", "  ".join(rules) or "—", self._short(t.get("triggers") or [], 1), props, t.get("event") or "—"))
        if any(str((t.get("properties") or {}).get("servicedesk.customer.transition.active", "")).lower() == "true" for t in wf["transitions"]):
            h += self._text(p, x, y + h, w, "Note: transitions highlighted in green are shown to customers in the service desk portal.", 8, color=MUTED) + 3
        h += self._table(p, x, y + h, w, ["Transition", "ID", "Path", "Screen", "Rules", "Triggers", "Properties", "Event"],
                         rows, links=(1, 3), highlight=marked, tags={n: "Customer portal" for n in marked})
        if len(wf["transitions"]) > 4:
            h += self._text(p, x, y + h + 1, w, f"… {len(wf['transitions']) - 4} more transitions", 8, color=MUTED) + 2
        h += 6
        h += self._sub(p, x, y + h, w, "Transition screens")
        h += self._screens(p, x, y + h, w)
        h += self._text(p, x, y + h + 3, w, "Statuses link to their transitions; transitions link to their screen.", 8, color=MUTED) + 4
        return h

    def _screens(self, p, x, y, w) -> float:
        """One table: screen (name, ID, link), tab and field (ID, type, description); a repeated screen or tab is left blank."""
        shown = self.wf.get("screens") or []
        if not shown:
            return self._text(p, x, y, w, "No transition screens are used by this workflow.", 8.5, color=MUTED) + 4
        rows, more = [], 0
        for sid, name in shown[:2]:
            d = self.screens.get(str(sid))
            if not d:
                rows.append((name, f"{sid}  Open Screen", "—", self.note or "Tabs and fields load when you are signed in.", "—", "—", "—"))
                continue
            first = True
            for tab in d["tabs"][:2]:
                for k, f in enumerate(tab["fields"][:3] or [None]):
                    fld = (f["name"], f["id"], f["type"] or "—", f["description"] or "—") if f else ("No fields", "—", "—", "—")
                    rows.append((name if first else "", f"{sid}  Open Screen" if first else "",
                                 tab["name"] if k == 0 else "", *fld))
                    first = False
                more += max(0, len(tab["fields"]) - 3)
        h = self._table(p, x, y, w, ["Screen", "ID", "Tab", "Field", "Field ID", "Field type", "Description"], rows, links=(1,))
        if more:
            h += self._text(p, x, y + h + 1, w, f"… {more} more fields", 8, color=MUTED) + 2
        if len(shown) > 2:
            h += self._text(p, x, y + h, w, f"… {len(shown) - 2} more screens", 8, color=MUTED) + 2
        return h + 4

    def _code(self, p, x, y, w, lines, numbers=False) -> float:
        h = len(lines) * 11 + 10
        self._rect(p, x, y, w, h, fill=SOFT, border=LINE)
        for i, ln in enumerate(lines):
            ly, tx = y + 5 + i * 11, x + 8
            if numbers:
                self._text(p, tx, ly, 18, str(i + 1), 7.5, color=MUTED, mono=True); tx += 22
            self._text(p, tx, ly, w - (tx - x) - 6, ln, 8, mono=True)
        return h

    def _el_json(self, p, x, y, w) -> float:
        t = self.t
        h = self._heading(p, x, y, w, "Raw JSON")
        if t.code_collapse:
            self._rect(p, x, y + h, w, 22, fill=SOFT, border=LINE)
            self._text(p, x + 8, y + h + 3, w - 16, "▸  Expand source", 9, color=BLUE)
            return h + 22
        brief = {k: self.wf.get(k) for k in ("id", "name", "scope", "updated")}
        brief["statuses"] = [s["name"] for s in self.wf["statuses"][:3]]
        lines = json.dumps(brief, indent=2, ensure_ascii=False).splitlines()[:9]
        return h + self._code(p, x, y + h, w, lines, numbers=t.code_lines)

    def _el_flow(self, p, x, y, w) -> float:
        t = self.t
        h = self._heading(p, x, y, w, "Workflow diagram")
        if t.shows_png:
            if self.image is not None and not self.image.isNull():
                # the page shows the image at its canvas width, so a small diagram stays small (the page content is about 1000 px wide)
                shown_w = min(w, w * (self.image_px or 1000) / 1000)
                iw = shown_w
                ih = iw * self.image.height() / max(1, self.image.width())
                if ih > 300:
                    ih, iw = 300.0, 300.0 * self.image.width() / max(1, self.image.height())
                self._rect(p, x, y + h, w, ih + 6, fill="#FFFFFF", border=LINE)
                if p:
                    p.drawImage(QRectF(x + (w - iw) / 2, y + h + 3, iw, ih), self.image)
                h += ih + 9
            else:
                h += 70
                self._rect(p, x, y + h - 70, w, 70, fill="#FFFFFF", border=LINE)
            extra = f"width +{t.flow_width}%" if t.flow_width else "default width"
            shown = f"shown {self.image_px} px wide" if self.image_px else "natural size"
            cap = (f"PNG image · {shown} · {t.png_scale}x sharpness · {extra}" if t.flow_format == "png" else f"PNG preview · {shown} · {extra}")
            cap += (f" · status size {t.flow_node_scale}%" + (" · curved lines" if t.flow_line_style == "curved" else "")
                    + (" · monochrome" if t.flow_color_style == "mono" else "") + (" · Jira status positions" if t.flow_jira_pos else ""))
            cap += " · with transition labels" if t.flow_labels else " · without transition labels"
            h += self._text(p, x, y + h, w, cap, 8, color=MUTED) + 4
        if t.flow_format == "drawio":
            h += self._text(p, x, y + h, w, "📎  workflow.drawio  (editable in draw.io)", 9, color=BLUE) + 2
        return h
