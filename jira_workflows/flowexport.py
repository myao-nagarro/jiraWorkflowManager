"""Workflow diagram as a draw.io (diagrams.net) document, laid out exactly like the app's flowchart."""
from xml.sax.saxutils import escape, quoteattr

from .flowchart import MONO, compute_layout

# light-theme colours, so the file looks the same whatever theme the app uses
FILL = {"TODO": ("#F0F0F0", "#8A8A8A"), "IN_PROGRESS": ("#EBF3FC", "#0F6CBD"), "DONE": ("#F1FAF1", "#107C10")}
LINE = "#707070"


def to_drawio(wf: dict, extra_width: int = 0, labels: bool = False, node_scale: int = 100, curved: bool = False, mono: bool = False, use_jira: bool = False) -> str:
    """An uncompressed draw.io (mxfile) document with the statuses and the transitions of a workflow."""
    g, _, rects, routes = compute_layout(wf, extra_width, node_scale / 100, use_jira)
    cells, ids = [], {}

    def new_id(prefix="c"):
        ids[prefix] = ids.get(prefix, 1) + 1
        return f"{prefix}{ids[prefix]}"

    def pill(text, x, y, w, fill, stroke, bold=False):
        cells.append(f'<mxCell id="{new_id("l")}" value={quoteattr(escape(text))} style="rounded=1;arcSize=50;whiteSpace=nowrap;html=1;'
                     f'fillColor={fill};strokeColor={stroke};strokeWidth=2;fontSize=11;{"fontStyle=1;" if bold else ""}" vertex="1" parent="1">'
                     f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="26" as="geometry"/></mxCell>')

    for n in g.nodes.values():
        x, y, w, h = rects[n.id]
        if n.kind == "start":
            style = "ellipse;whiteSpace=wrap;html=1;fillColor=#242424;strokeColor=#242424;fontColor=#FFFFFF;fontSize=9;fontStyle=1;"
            value = "START"
        else:
            fill, stroke = MONO[n.category] if mono else FILL[n.category]
            style = f"rounded=1;arcSize=8;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={stroke};strokeWidth=2;fontSize=12;"
            value = f"<b>{escape(n.label.upper())}</b><br/><font style=\"font-size:10px\" color=\"#616161\">ID {escape(n.id)}</font>"
        cells.append(f'<mxCell id="n_{escape(n.id)}" value={quoteattr(value)} style="{style}" vertex="1" parent="1">'
                     f'<mxGeometry x="{x:.0f}" y="{y:.0f}" width="{w:.0f}" height="{h:.0f}" as="geometry"/></mxCell>')

    for e, pts, _pos, _turned in routes:
        members = [e, *e.also]
        start_arrow = "startArrow=open;startFill=0;" if e.also else ""
        label = "" if not labels else " / ".join(f"{m.name or 'Transition'} · ID {m.tid}" for m in members)
        mid = "".join(f'<mxPoint x="{x:.0f}" y="{y:.0f}"/>' for x, y in pts[1:-1])
        cells.append(f'<mxCell id="{new_id("e")}" value={quoteattr(escape(label))} style="endArrow=open;endFill=0;{start_arrow}html=1;rounded=0;{'curved=1;' if curved else ''}'
                     f'strokeColor={LINE};strokeWidth=2;fontSize=11;labelBackgroundColor=#FFFFFF;edgeStyle=none;" edge="1" parent="1">'
                     f'<mxGeometry relative="1" as="geometry"><mxPoint x="{pts[0][0]:.0f}" y="{pts[0][1]:.0f}" as="sourcePoint"/>'
                     f'<mxPoint x="{pts[-1][0]:.0f}" y="{pts[-1][1]:.0f}" as="targetPoint"/>'
                     + (f'<Array as="points">{mid}</Array>' if mid else "") + "</mxGeometry></mxCell>")

    for dst, edges in g.global_in.items():  # "from any status": a dark Any badge on the target status
        x, y, w, h = rects[dst]
        pill("Any", x + w - 53, y - 41, 46, "#242424", "#242424", bold=True)
        cells[-1] = cells[-1].replace("fillColor=#242424;strokeColor=#242424;", "fillColor=#242424;strokeColor=#242424;fontColor=#FFFFFF;")
        cells.append(f'<mxCell id="{new_id("e")}" value="" style="endArrow=open;endFill=0;html=1;strokeColor={LINE};strokeWidth=2;" edge="1" parent="1">'
                     f'<mxGeometry relative="1" as="geometry"><mxPoint x="{x + w - 30:.0f}" y="{y - 15:.0f}" as="sourcePoint"/>'
                     f'<mxPoint x="{x + w - 30:.0f}" y="{y:.0f}" as="targetPoint"/></mxGeometry></mxCell>')
        if labels:
            for i, e in enumerate(edges):
                pill(f"{e.name or 'Transition'} · ID {e.tid}", x + w - 70 - 190, y - 41 - i * 30, 190, "#FFFFFF", LINE)

    name = wf.get("name") or "Workflow"
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<mxfile host="app.diagrams.net"><diagram name={quoteattr(name[:60])} id="workflow">'
            '<mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" '
            'fold="1" page="0" pageScale="1" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
            + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
