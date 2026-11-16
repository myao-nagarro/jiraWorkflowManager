"""The preview of a generated Confluence page: the page body in storage format (the very text that is published) turned into HTML that the
app's rich-text view can show, plus the page hierarchy. Layouts become tables, macros are imitated (table of contents, page properties, code
block, note, status lozenge) and the diagram image is shown from the picture the app drew."""
import html
import re
import xml.etree.ElementTree as ET
from urllib.parse import unquote, urlparse

from .confluence import REQUIRED_LABEL, page_title

AC, RI = "urn:ac", "urn:ri"
INK, MUTED, LINE, SOFT, BLUE = "#172B4D", "#6B778C", "#C1C7D0", "#F4F5F7", "#0052CC"
CODE_LINES = 40   # the raw JSON is long: the preview shows its first lines
LOZENGE = {"Green": ("#E3FCEF", "#006644"), "Blue": ("#DEEBFF", "#0747A6"), "Red": ("#FFEBE6", "#BF2600"),
           "Yellow": ("#FFF0B3", "#172B4D"), "Grey": ("#DFE1E6", "#42526E"), "Purple": ("#EAE6FF", "#403294")}
COLUMNS = {"single": [100], "two_equal": [50, 50], "two_left_sidebar": [30, 70], "two_right_sidebar": [70, 30],
           "three_equal": [34, 33, 33], "three_with_sidebars": [25, 50, 25]}

PAGE_CSS = f"""
body {{ font-family: 'Segoe UI', Arial; font-size: 14px; color: {INK}; }}
h1 {{ font-size: 22px; font-weight: 600; }} h2 {{ font-size: 20px; margin-top: 18px; }} h3 {{ font-size: 16px; margin-top: 14px; }}
h4 {{ font-size: 14px; margin-top: 12px; }} a {{ color: {BLUE}; text-decoration: none; }}
code {{ font-family: Consolas, 'Courier New'; background-color: {SOFT}; }}
"""

# the sample shown while no workflow is selected
SAMPLE = {"name": "Sample workflow", "id": "0d4e1c7a-0000-0000-0000-000000000000", "scope": "GLOBAL", "project_keys": ["ABC"],
          "schemes": [{"id": "1", "name": "Sample scheme"}], "active": True, "updated": "2026-10-26T10:00:00Z", "property_count": 0, "transition_property_count": 0, "screens": [],
          "statuses": [{"id": "1", "name": "Open", "category": "TODO", "properties": {}},
                       {"id": "3", "name": "In Progress", "category": "IN_PROGRESS", "properties": {}},
                       {"id": "6", "name": "Done", "category": "DONE", "properties": {}}],
          "transitions": [{"id": "11", "name": "Start progress", "from": ["1"], "to": "3", "type": "directed", "conditions": [], "validators": [],
                           "actions": ["Update field (field=resolution)"], "triggers": [], "properties": {}, "event": "", "screen_id": "", "screen_name": ""},
                          {"id": "21", "name": "Done", "from": ["3"], "to": "6", "type": "directed", "conditions": [], "validators": [],
                           "actions": [], "triggers": [], "properties": {}, "event": "10000", "screen_id": "", "screen_name": ""}]}


def _tag(el) -> str:
    return el.tag.rsplit("}", 1)[-1]


def _attr(el, name: str, ns: str = AC) -> str:
    return el.get(f"{{{ns}}}{name}") or el.get(name) or ""


class _Converter:
    """Walks the parsed storage body once and writes HTML."""

    def __init__(self, root, images: dict, max_width: int):
        self.images, self.max_width = images, max_width
        self.cell_width = max_width   # the room for a picture in the layout cell being written
        self.headings = [(int(_tag(h)[1]), "".join(h.itertext()).strip()) for h in root.iter() if re.fullmatch(r"h[1-6]", _tag(h))]
        self.seen = 0   # headings written so far (their anchors are h0, h1 ...)

    def children(self, el) -> str:
        out = self.text(el.text)
        for ch in el:
            out += self.conv(ch) + self.text(ch.tail)
        return out

    @staticmethod
    def text(value) -> str:
        """Text for the view. The page keeps rule texts on one line with no-break spaces; the narrow preview lets them wrap."""
        return html.escape((value or "").replace(" ", " "))

    def conv(self, el) -> str:
        tag = _tag(el)
        handler = getattr(self, "t_" + tag.replace("-", "_"), None)
        if handler:
            return handler(el)
        if tag in ("p", "ul", "ol", "li", "tr", "br", "em", "strong", "code", "span", "h1", "h2", "h3", "h4", "h5", "h6"):
            tag = {"strong": "b", "em": "i"}.get(tag, tag)
            if tag == "br":
                return "<br>"
            return f"<{tag}>{self.children(el)}</{tag}>" if not re.fullmatch(r"h[1-6]", tag) else self.heading(el, tag)
        return self.children(el)

    def heading(self, el, tag) -> str:
        anchor = f"h{self.seen}"; self.seen += 1
        return f'<{tag}><a name="{anchor}"></a>{self.children(el)}</{tag}>'

    # ---- elements of the page
    def t_root(self, el):
        return self.children(el)

    def t_a(self, el):
        href = el.get("href") or "#"
        return f'<a href="{html.escape(href, quote=True)}">{self.children(el)}</a>'

    def t_layout(self, el):
        return self.children(el)

    def t_layout_section(self, el):
        widths = COLUMNS.get(_attr(el, "type"), [100])
        cells, out = [c for c in el if _tag(c) == "layout-cell"], ""
        for i, c in enumerate(cells):
            pct = widths[i] if i < len(widths) else 100 // max(1, len(cells))
            self.cell_width = max(120, int(self.max_width * pct / 100) - 28)
            out += f'<td width="{pct}%" valign="top">{self.children(c)}</td>'
        self.cell_width = self.max_width
        return f'<table width="100%" cellspacing="0" cellpadding="8" border="0"><tr>{out}</tr></table>'

    def t_table(self, el):
        cols = [float(m) for col in el.iter() if _tag(col) == "col" for m in re.findall(r"width:\s*([\d.]+)px", col.get("style") or "")]
        total = sum(cols) or 1
        body = self.children(el.find("tbody") if el.find("tbody") is not None else el)
        if cols:   # the widths of the <col> elements become percentages on the cells of the first row
            first = re.search(r"<tr>.*?</tr>", body, flags=re.S)
            if first:
                cells = iter([f"{c / total * 100:.1f}%" for c in cols])
                row = re.sub(r"<(th|td)( [^>]*)?>", lambda m: f'<{m.group(1)}{m.group(2) or ""} width="{next(cells, "10%")}">', first.group(0))
                body = body.replace(first.group(0), row, 1)
        return (f'<table width="100%" border="1" cellspacing="0" cellpadding="6" style="border-collapse: collapse; border-color: {LINE};">'
                f"{body}</table>")

    def t_th(self, el):
        return f'<th bgcolor="{SOFT}" align="left">{self.children(el)}</th>'

    def t_td(self, el):
        colour = el.get("data-highlight-colour") or (re.search(r"background-color:\s*(#[0-9A-Fa-f]{6})", el.get("style") or "") or [None, None])[1]
        span = f' rowspan="{el.get("rowspan")}"' if el.get("rowspan") else ""
        return f'<td valign="top"{span}{f" bgcolor={chr(34)}{colour}{chr(34)}" if colour else ""}>{self.children(el)}</td>'

    def t_colgroup(self, el):
        return ""

    def t_col(self, el):
        return ""

    def t_parameter(self, el):
        return ""

    def t_link(self, el):
        text = "".join(t.text or "" for t in el.iter() if _tag(t) == "plain-text-link-body").strip()
        anchor = _attr(el, "anchor")
        if anchor:
            return f'<a href="#{html.escape(anchor, quote=True)}">{html.escape(text)}</a>'
        return f'<span style="color:{BLUE}">{html.escape(text)}</span>'

    def t_image(self, el):
        att = next((c for c in el.iter() if _tag(c) == "attachment"), None)
        name = unquote(_attr(att, "filename", RI)) if att is not None else ""
        if name in self.images:
            width = min(int(_attr(el, "width") or self.max_width), self.cell_width)   # never wider than its column
            return f'<p><img src="{html.escape(name, quote=True)}" width="{width}"></p>'
        return (f'<table width="100%" cellpadding="14" cellspacing="0" border="1" style="border-collapse: collapse; border-color: {LINE};">'
                f'<tr><td bgcolor="{SOFT}" align="center"><span style="color:{MUTED}">The workflow diagram appears here ({html.escape(name or "image")})</span>'
                "</td></tr></table>")

    def t_structured_macro(self, el):   # pylint: disable=too-many-return-statements
        name = _attr(el, "name")
        params = {_attr(p, "name"): (p.text or "") for p in el if _tag(p) == "parameter"}
        body = next((b for b in el if _tag(b) in ("rich-text-body", "plain-text-body")), None)
        if name == "toc":
            return self.toc(int(params.get("maxLevel") or 3))
        if name == "anchor":
            return f'<a name="{html.escape(params.get("") or "", quote=True)}"></a>'
        if name == "status":
            fill, ink = LOZENGE.get(params.get("colour") or "Grey", LOZENGE["Grey"])
            return f'<span style="background-color:{fill}; color:{ink}; font-weight:600;">&nbsp;{html.escape((params.get("title") or "").upper())}&nbsp;</span>'
        if name == "code":
            return self.code(params, body.text or "" if body is not None else "")
        if name == "note":
            return (f'<table width="100%" cellpadding="8" cellspacing="0" border="0"><tr><td bgcolor="#FFFAE6" width="4%">&#9888;</td>'
                    f'<td bgcolor="#FFFAE6">{self.children(body) if body is not None else ""}</td></tr></table>')
        if name == "details":
            return f'<p style="color:{MUTED}"><i>Page properties</i></p>{self.children(body) if body is not None else ""}'
        if name == "detailssummary":
            return f'<p style="color:{MUTED}"><i>Content properties report</i></p>'
        return self.children(body) if body is not None else ""

    def toc(self, max_level: int) -> str:
        items = "".join(f'<li style="margin-left:{(lvl - 1) * 14}px"><a href="#h{i}">{html.escape(text)}</a></li>'
                        for i, (lvl, text) in enumerate(self.headings) if lvl <= max_level)
        return f'<table cellpadding="8" cellspacing="0" border="0"><tr><td bgcolor="{SOFT}"><b>Contents</b><ul>{items}</ul></td></tr></table>'

    def code(self, params: dict, text: str) -> str:
        if params.get("collapse") == "true":
            return (f'<table width="100%" cellpadding="8" cellspacing="0" border="1" style="border-collapse: collapse; border-color: {LINE};">'
                    f'<tr><td bgcolor="{SOFT}"><span style="color:{BLUE}">&#9656; Expand source</span></td></tr></table>')
        lines = text.split("\n")
        shown = lines[:CODE_LINES]
        if params.get("linenumbers") == "true":
            shown = [f"{i + 1:>3}  {ln}" for i, ln in enumerate(shown)]
        more = f"\n… {len(lines) - CODE_LINES:,} more lines" if len(lines) > CODE_LINES else ""
        return (f'<table width="100%" cellpadding="8" cellspacing="0" border="1" style="border-collapse: collapse; border-color: {LINE};">'
                f'<tr><td bgcolor="{SOFT}"><pre style="font-family: Consolas, Courier New; font-size: 12px; white-space: pre-wrap;">{html.escape(chr(10).join(shown) + more)}</pre>'
                "</td></tr></table>")


def storage_to_html(body: str, images: dict = None, max_width: int = 640) -> str:
    """Confluence storage format -> HTML for the rich-text view. `images`: {attachment file name: True} for the pictures that are available;
    a picture is shown at most `max_width` pixels wide."""
    try:
        root = ET.fromstring(f'<root xmlns:ac="{AC}" xmlns:ri="{RI}">{body}</root>')
    except ET.ParseError as e:
        return f"<p style='color:#C50F1F'>The page could not be shown: {html.escape(str(e))}</p>"
    return _Converter(root, images or {}, max_width).children(root)


def _row(icon: str, kind: str, text: str, depth: int, muted: str = "") -> str:
    return (f'<tr><td style="padding-left:{depth * 22 + 6}px; padding-top:3px; padding-bottom:3px;">{icon}&nbsp; <b>{kind}</b>&nbsp; {html.escape(text)}'
            f'{f"&nbsp; <span style=color:{MUTED}>{html.escape(muted)}</span>" if muted else ""}</td></tr>')


def hierarchy_html(template, workflows: list, limit: int = 6) -> str:
    """The pages that will exist: the parent, then one page for each workflow, and what the parent page gets."""
    parent = template.parent_url.strip()
    where = urlparse(parent).path.rsplit("/", 1)[-1].replace("+", " ") if parent else ""
    rows = [_row("&#128193;", "Parent", unquote(where) or "the page or folder you choose on the Publish page", 0,
                 "" if parent else "set on the Publish page")]
    if template.parent_report:
        rows.append(_row("&#128202;", "Report", "Content properties report of the pages below (added to the parent page)", 1))
    names = [w["name"] for w in workflows] or ["Sample workflow"]
    labels = ", ".join([REQUIRED_LABEL] + [x for x in template.labels if x])
    for name in names[:limit]:
        rows.append(_row("&#128196;", "Page", page_title(name, template.title_prefix, template.title_prefix_on), 1, f"labels: {labels}"))
    if len(names) > limit:
        rows.append(_row("&#8230;", "", f"and {len(names) - limit} more pages", 1))
    return (f'<table width="100%" cellspacing="0" cellpadding="0" border="1" style="border-collapse: collapse; border-color: {LINE};">'
            f'<tr><td bgcolor="{SOFT}" style="padding:6px;"><b>Page hierarchy</b></td></tr>{"".join(rows)}</table>')


def page_preview(template, body: str, title: str, images: dict, site: str = "", max_width: int = 640) -> str:
    """One page as Confluence shows it: breadcrumb, title, the converted body and the labels."""
    inner = storage_to_html(body, images, max_width)
    width = "100%" if template.full_width else "86%"
    labels = "&nbsp; ".join(f'<span style="background-color:{SOFT}; color:{INK};">&nbsp;{html.escape(x)}&nbsp;</span>'
                            for x in [REQUIRED_LABEL] + [x for x in template.labels if x])
    host = urlparse(site).netloc or "your-company.atlassian.net"
    return (f'<p style="color:{MUTED}; font-size:12px;">{html.escape(host)} &nbsp;&#8250;&nbsp; Parent &nbsp;&#8250;&nbsp; {html.escape(title)}</p>'
            f'<table width="{width}" cellpadding="0" cellspacing="0" border="0"><tr><td><h1>{html.escape(title)}</h1>{inner}'
            f'<p style="color:{MUTED}; font-size:12px;">Labels &nbsp; {labels}</p></td></tr></table>')
