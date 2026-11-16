"""Confluence storage-format body of a workflow page, laid out as the template says. No Qt, no network."""
import html
import re

from .api import screen_url, status_url, transition_url, workflow_edit_url
from .model import CATEGORIES
from .textutil import titlecase

PORTAL_PROPERTY = "servicedesk.customer.transition.active"   # "true": the transition is offered to customers in the service desk portal
PORTAL_COLOUR = "#E3FCEF"   # Confluence's light green highlight
TABLE_WIDTH, MIN_COLUMN = 1000, 52


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def nb(text: str) -> str:
    """Escaped text whose spaces cannot break: a rule stays on one line however narrow the column is."""
    return esc(text).replace(" ", "&#160;")


def cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def anchor(name: str) -> str:
    return f'<ac:structured-macro ac:name="anchor" ac:schema-version="1"><ac:parameter ac:name="">{esc(name)}</ac:parameter></ac:structured-macro>'


def page_link(name: str, text: str) -> str:
    """A link to an anchor on the same page."""
    return f'<ac:link ac:anchor="{esc(name)}"><ac:plain-text-link-body>{cdata(text)}</ac:plain-text-link-body></ac:link>'


def url_link(url: str, text=None) -> str:
    return f'<a href="{esc(url)}">{esc(text or url)}</a>'


def lozenge(text: str, colour="Green") -> str:
    return ('<ac:structured-macro ac:name="status" ac:schema-version="1">'
            f'<ac:parameter ac:name="colour">{colour}</ac:parameter><ac:parameter ac:name="title">{esc(text)}</ac:parameter></ac:structured-macro>')


def code_macro(text, language="none", numbered=False, collapse=False) -> str:
    return ('<ac:structured-macro ac:name="code" ac:schema-version="1">'
            f'<ac:parameter ac:name="language">{language}</ac:parameter>'
            f'<ac:parameter ac:name="linenumbers">{"true" if numbered else "false"}</ac:parameter>'
            f'<ac:parameter ac:name="collapse">{"true" if collapse else "false"}</ac:parameter>'
            f'<ac:plain-text-body>{cdata(text)}</ac:plain-text-body></ac:structured-macro>')


# ---------------------------------------------------------------- tables
def _lines(cell: str) -> list:
    """The visible text lines of a cell's HTML (to size the column)."""
    t = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", cell, flags=re.S)
    t = re.sub(r"<li>", "\n• ", t)
    t = re.sub(r"</p>|<br\s*/?>|</li>|</ul>", "\n", t)
    t = re.sub(r'<ac:structured-macro ac:name="status".*?<ac:parameter ac:name="title">(.*?)</ac:parameter>.*?</ac:structured-macro>', r"", t, flags=re.S)
    t = re.sub(r"<ac:structured-macro.*?</ac:structured-macro>", "", t, flags=re.S)   # anchors take no room
    t = html.unescape(re.sub(r"<[^>]+>", "", t)).replace(" ", " ")
    return [ln for ln in (x.strip() for x in t.split("\n")) if ln]


def _html(cell) -> str:
    """The HTML of a table cell: a string, a (html, rowspan) pair, or None for a cell covered by a rowspan above it."""
    return "" if cell is None else cell[0] if isinstance(cell, tuple) else cell


def column_widths(headers, rows) -> list:
    """Pixel widths that follow the text of each column; a column without any value shrinks to the minimum."""
    want, empty = [], []
    for c, h in enumerate(headers):
        longest = max([len(ln) for r in rows for ln in _lines(_html(r[c]))] + [0])
        blank = longest == 0 or all(_lines(_html(r[c])) in ([], ["—"]) for r in rows)
        empty.append(blank)
        # the header is bold, so it needs a little more than its letters; long text is capped so one column cannot take the page
        want.append(MIN_COLUMN if blank else max(len(h) * 8.5 + 20, min(max(longest, 3), 60) * 7 + 22))
    total = sum(want)   # columns keep the width of their text; only a table wider than the page is scaled down
    scale = min(1.0, TABLE_WIDTH / total) if total else 1.0
    return [int(max(MIN_COLUMN, w * scale)) for w in want]


def table(headers, rows, highlight=()) -> str:
    """A table with a bold header row and columns sized by their text. Rows hold ready-made HTML cells; `highlight` lists row
    numbers that get Confluence's light green highlight."""
    cols = "".join(f'<col style="width: {w}px;" />' for w in column_widths(headers, rows))
    head = "".join(f"<th><p><strong>{esc(titlecase(h))}</strong></p></th>" for h in headers)
    mark = f' data-highlight-colour="{PORTAL_COLOUR}" style="background-color: {PORTAL_COLOUR};"'
    def td(i, c):
        if c is None:   # covered by a cell above that spans several rows
            return ""
        html_, span = c if isinstance(c, tuple) else (c, 1)
        spans = f' rowspan="{span}"' if span > 1 else ""
        return f"<td{spans}{mark if i in highlight else ''}>{html_}</td>"

    body = "".join("<tr>" + "".join(td(i, c) for c in r) + "</tr>" for i, r in enumerate(rows))
    return f'<table data-layout="full-width"><colgroup>{cols}</colgroup><tbody><tr>{head}</tr>{body}</tbody></table>'


def lines(items) -> str:
    """Several short texts in one cell, one per line."""
    return "<br/>".join(esc(i) for i in items) if items else "—"


def heading(level: int, text: str) -> str:
    return f"<h{level}>{esc(titlecase(text))}</h{level}>"


def rules_cell(t: dict) -> str:
    """Conditions, validators and post functions as bulleted lists under a bold label; no rule text wraps."""
    out = ""
    for title, items in (("Conditions", t.get("conditions")), ("Validators", t.get("validators")), ("Post functions", t.get("actions"))):
        if items:
            out += f"<p><strong>{title}</strong></p><ul>" + "".join(f"<li>{nb(i)}</li>" for i in items) + "</ul>"
    return out or "—"


def is_portal(t: dict) -> bool:
    """Does the service desk portal offer this transition to customers?"""
    return str((t.get("properties") or {}).get(PORTAL_PROPERTY, "")).strip().lower() == "true"


# ---------------------------------------------------------------- elements
def info_xml(wf: dict, site: str) -> str:
    rows = [("Name", esc(wf["name"])), ("Workflow ID", esc(wf["id"])), ("Scope", esc(wf.get("scope", ""))),
            ("Spaces", esc(", ".join(wf.get("project_keys") or []) or "—")),
            ("Workflow schemes", esc(", ".join(s["name"] for s in wf.get("schemes") or []) or "—")),
            ("State", "Active" if wf.get("active") else "Inactive"), ("Last updated", esc(wf.get("updated", ""))),
            ("Statuses", str(len(wf["statuses"]))), ("Transitions", str(len(wf["transitions"]))),
            ("Workflow URL", url_link(workflow_edit_url(site, wf), "Open the workflow in Jira"))]
    body = "".join(f"<tr><th><p><strong>{esc(k)}</strong></p></th><td>{v}</td></tr>" for k, v in rows)
    # inside the "Content properties" macro (storage name: details), so the parent page's report can list it
    return (heading(2, "Workflow Information") + '<ac:structured-macro ac:name="details" ac:schema-version="1">'
            f"<ac:rich-text-body><table><tbody>{body}</tbody></table></ac:rich-text-body></ac:structured-macro>")


def flow_xml(wf: dict, tpl, assets: dict) -> str:
    out = heading(2, "Workflow Diagram")
    if assets.get("flow_html"):   # kept as it is on the page (a job does not draw diagrams)
        return out + assets["flow_html"]
    if assets.get("png_name"):
        width = f' ac:width="{int(assets["png_width"])}"' if assets.get("png_width") else ""   # the page shows the image at the canvas width
        out += (f'<p><ac:image ac:alt="{esc(wf["name"])} workflow diagram"{width}><ri:attachment ri:filename="{esc(assets["png_name"])}"/></ac:image></p>')
    if tpl.flow_format == "drawio" and assets.get("drawio_name"):
        out += ('<p>Editable diagram: <ac:link><ri:attachment ri:filename="' + esc(assets["drawio_name"]) + '"/><ac:plain-text-link-body>'
                "<![CDATA[Open the draw.io diagram (.drawio)]]></ac:plain-text-link-body></ac:link></p>")
    return out


def diagram_placeholder() -> str:
    """What a job puts under the "Workflow Diagram" heading of a page that has no diagram: an info panel that marks the place. A person
    edits the page and puts the picture there; the job keeps whatever diagram the page has on its later runs."""
    return ('<ac:structured-macro ac:name="info" ac:schema-version="1"><ac:parameter ac:name="title">Workflow diagram placeholder</ac:parameter>'
            "<ac:rich-text-body><p>The job does not draw the workflow diagram. Edit this page and add the diagram picture here "
            "(Insert, then Files and images). A page that has a diagram keeps it when the job runs again.</p></ac:rich-text-body>"
            "</ac:structured-macro>")


def _props(d: dict) -> str:
    return lines([f"{k} = {v}" if v != "" else k for k, v in sorted((d or {}).items())])


def details_xml(wf: dict, screens: dict, site: str) -> str:
    """Statuses (with links to their transitions), the transitions (with a link to their screen), then the transition screens."""
    names = {s["id"]: s["name"] for s in wf["statuses"]}
    trans = wf["transitions"]
    by_id = {t["id"]: t for t in trans}
    def t_anchor(t):
        return f"transition-{t['id']}"

    global_ = [t for t in trans if not t["from"] and t["type"] != "initial"]

    rows = []
    for s in sorted(wf["statuses"], key=lambda s: (CATEGORIES[s["category"]][1], s["name"].lower())):
        incoming = [t for t in trans if t["to"] == s["id"]]
        # a transition "from any status" can be taken from every status, so it is an outgoing transition of each one
        outgoing = list(dict.fromkeys(t["id"] for t in trans if s["id"] in t["from"] or t in global_))
        rows.append([esc(s["name"]), esc(s["id"]) + "<br/>" + url_link(status_url(site, wf, s.get("ref") or s["id"]), "Edit Status"),
                     esc(CATEGORIES[s["category"]][0]), _props(s.get("properties")),
                     "<br/>".join(page_link(t_anchor(t), f"{t['name']} (ID {t['id']})") for t in incoming) or "—",
                     "<br/>".join(page_link(t_anchor(by_id[i]), f"{by_id[i]['name']} (ID {i})") for i in outgoing) or "—"])
    out = heading(2, "Workflow Details") + heading(3, "Statuses")
    out += table(["Status", "ID", "Status Category", "Properties", "Incoming Transitions", "Outgoing Transitions"], rows)

    rows, shown = [], []
    for n, t in enumerate(trans):
        src = ", ".join(names.get(f, f) for f in t["from"]) or ("Start" if t["type"] == "initial" else "Any status")
        screen = (page_link(f"screen-{t['screen_id']}", t.get("screen_name") or f"Screen {t['screen_id']}") if t.get("screen_id") else "—")
        portal = is_portal(t)
        if portal:
            shown.append(n)
        # the customer portal tag sits under the transition name
        rows.append([anchor(t_anchor(t)) + esc(t["name"]) + ("<br/>" + lozenge("Customer portal") if portal else ""),
                     esc(t["id"]) + "<br/>" + url_link(transition_url(site, wf, t["id"]), "Edit Transition"),
                     esc(f"{src} → {names.get(t['to'], t['to'])}"), screen, rules_cell(t), lines(t.get("triggers", [])),
                     _props(t.get("properties")), esc(t.get("event") or "—")])
    out += heading(3, "Transitions")
    if shown:
        out += ('<ac:structured-macro ac:name="note" ac:schema-version="1"><ac:rich-text-body><p>Transitions highlighted in green are shown to '
                "customers in the service desk portal (transition property <code>servicedesk.customer.transition.active = true</code>).</p>"
                "</ac:rich-text-body></ac:structured-macro>")
    out += table(["Transition", "ID", "Path", "Screen", "Rules", "Triggers", "Transition Properties", "Event"], rows, highlight=shown)

    out += heading(3, "Transition Screens")
    if not wf.get("screens"):
        return out + "<p>No transition screens are used by this workflow.</p>"
    # one table: the screen (name, ID, link) and the tab span the rows of the fields they hold
    rows = []
    for sid, name in wf["screens"]:
        d = screens.get(str(sid))
        url = d["url"] if d else screen_url(site, sid)
        screen = [f"<strong>{anchor(f'screen-{sid}')}{esc(name)}</strong>", esc(sid) + "<br/>" + url_link(url, "Open Screen")]
        fields = []   # (tab cell, field cells)
        for tab in (d or {}).get("tabs", []):
            for k, f in enumerate(tab["fields"] or [None]):
                cells = ([esc(f["name"]), esc(f["id"]), esc(f["type"] or "—"), esc(f["description"] or "—")] if f else ["No fields", "—", "—", "—"])
                fields.append((tab["name"], k == 0, len(tab["fields"]) or 1, cells))
        if not fields:
            note = "The tabs and fields of this screen could not be read from Jira." if d is None else "No tabs"
            fields = [("—", True, 1, [f"<em>{note}</em>", "—", "—", "—"])]
        for n, (tab, first, span, cells) in enumerate(fields):
            head = [(c, len(fields)) for c in screen] if n == 0 else [None, None]
            rows.append(head + [(esc(tab), span) if first else None] + cells)
    out += table(["Screen", "ID", "Tab", "Field", "Field ID", "Field Type", "Field Description"], rows)
    return out


def build_storage(wf: dict, raw_json: str, assets: dict, tpl, screens: dict, site: str) -> str:
    """Storage-format body laid out as the template says (columns, element order, macros)."""
    parts = {
        "toc": '<ac:structured-macro ac:name="toc" ac:schema-version="1"><ac:parameter ac:name="minLevel">1</ac:parameter>'
               '<ac:parameter ac:name="maxLevel">3</ac:parameter></ac:structured-macro>',
        "info": info_xml(wf, site),
        "flow": flow_xml(wf, tpl, assets),
        "details": details_xml(wf, screens, site),
        "json": heading(2, "Raw JSON") + code_macro(raw_json, "json", tpl.code_lines, tpl.code_collapse),
    }
    cells = "".join("<ac:layout-cell>" + "".join(parts[e] for e in col if tpl.enabled(e)) + "</ac:layout-cell>" for col in tpl.columns)
    full = ""
    if tpl.details and tpl.details_full_width:  # the workflow details get a section of their own, across the whole page
        full = f'<ac:layout-section ac:type="single"><ac:layout-cell>{parts["details"]}</ac:layout-cell></ac:layout-section>'
    return f'<ac:layout><ac:layout-section ac:type="{tpl.layout}">{cells}</ac:layout-section>{full}</ac:layout>'
