"""The Confluence page template for generated workflow pages: which elements appear, in which column and order, and how the
workflow diagram is shown."""
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

# (label, Confluence layout type, caption of each column)
LAYOUTS = [
    ("Single column", "single", ["Full width"]),
    ("Two columns – narrow left", "two_left_sidebar", ["Narrow", "Wide"]),
    ("Two columns – narrow right", "two_right_sidebar", ["Wide", "Narrow"]),
    ("Two columns – equal", "two_equal", ["Equal", "Equal"]),
    ("Three columns – equal", "three_equal", ["Equal", "Equal", "Equal"]),
    ("Three columns – narrow sides", "three_with_sidebars", ["Narrow", "Wide", "Narrow"]),
]
ELEMENTS = {"toc": "Table of Contents", "info": "Workflow Information", "flow": "Workflow Diagram",
            "details": "Workflow Details", "json": "Raw JSON"}
# page elements grouped by relevance (the order of the check boxes on the template page)
GROUPS = [("Navigation", ["toc"]), ("Workflow", ["info", "flow"]), ("Details", ["details"]), ("Source", ["json"])]
FLOW_FORMATS = [("PNG image", "png"), ("draw.io diagram (editable file + PNG preview)", "drawio")]
# image sharpness: the PNG holds this many times the pixels it is shown with (the page shows it at the canvas width)
PNG_QUALITY = [("Standard (1x)", "standard", 1), ("High (2x)", "high", 2), ("Extra high (3x)", "ultra", 3)]
# the status size is the scale of the generated PNG diagram (100 % = shown at its natural size)
LINE_STYLES = [("Right-angle lines", "angle"), ("Curved lines", "curved")]
COLOR_STYLES = [("Status category colours", "category"), ("Monochrome (grey levels)", "mono")]
NODE_SCALES = [("Small (75%)", 75), ("Default (100%)", 100), ("Large (125%)", 125), ("Extra large (150%)", 150)]
# the same extra width choices as the Width drop-down of the workflow pop-up
WIDTH_OPTIONS = [("Default", 0), ("+25%", 25), ("+50%", 50), ("+75%", 75), ("+100%", 100)]
ORDER = ("toc", "info", "flow", "details", "json")


def layout_info(layout: str):
    return next((x for x in LAYOUTS if x[1] == layout), LAYOUTS[1])


@dataclass
class Template:
    layout: str = "two_left_sidebar"
    full_width: bool = True
    columns: list = field(default_factory=lambda: [["info"], ["flow", "details", "json"]])
    toc: bool = False
    info: bool = True
    flow: bool = True
    details: bool = True          # statuses, transitions and transition screens documented as tables, with page links between them
    details_full_width: bool = False   # put that section in its own full-width band below the columns
    code_block: bool = True
    code_collapse: bool = False
    code_lines: bool = True
    flow_width: int = 0           # extra width of the diagram: 0, 25, 50, 75 or 100 (%)
    flow_labels: bool = False     # show the transition name / ID on the diagram
    flow_format: str = "png"
    png_quality: str = "high"
    flow_node_scale: int = 100    # status size in percent: the scale of the generated PNG diagram
    flow_line_style: str = "angle"      # "angle" (right-angle lines) or "curved"
    flow_color_style: str = "category"  # "category" colours or "mono" (grey levels)
    flow_jira_pos: bool = False   # place the statuses at the X / Y values of the Jira workflow editor
    png_preview: bool = True      # draw.io: also show a PNG preview image
    # publishing (remembered with the template)
    parent_url: str = ""
    parent_report: bool = True    # add the content properties report to the parent page (only when the parent is a page)
    title_prefix_on: bool = False
    title_prefix: str = ""
    labels: list = field(default_factory=list)   # extra labels; "jira-workflow" is always added

    # ---- derived ----
    @property
    def ncols(self) -> int:
        return len(layout_info(self.layout)[2])

    @property
    def png_scale(self) -> int:
        return next((s for _, k, s in PNG_QUALITY if k == self.png_quality), 2)

    @property
    def shows_png(self) -> bool:
        return self.flow_format == "png" or (self.flow_format == "drawio" and self.png_preview)

    def in_columns(self, element: str) -> bool:
        """Enabled and placed in one of the columns (a full-width section is not)."""
        return self.enabled(element) and not (element == "details" and self.details_full_width)

    def enabled(self, element: str) -> bool:
        return {"toc": self.toc, "info": self.info, "flow": self.flow, "details": self.details,
                "json": self.code_block}.get(element, False)

    def summary(self) -> str:
        fmt = next((l for l, k in FLOW_FORMATS if k == self.flow_format), self.flow_format).split(" (")[0]
        return (f"{layout_info(self.layout)[0]}{', full width' if self.full_width else ''} · diagram: "
                f"{fmt if self.flow else 'off'} · table of contents {'on' if self.toc else 'off'} · "
                f"raw JSON {'on' if self.code_block else 'off'}")

    # ---- consistency ----
    def normalize(self) -> "Template":
        if self.layout not in {x[1] for x in LAYOUTS}:
            self.layout = "two_left_sidebar"
        if self.flow_format not in {k for _, k in FLOW_FORMATS}:
            self.flow_format = "png"
        if self.png_quality not in {k for _, k, _ in PNG_QUALITY}:
            self.png_quality = "high"
        if self.flow_line_style not in {k for _, k in LINE_STYLES}:
            self.flow_line_style = "angle"
        if self.flow_color_style not in {k for _, k in COLOR_STYLES}:
            self.flow_color_style = "category"
        if self.flow_node_scale not in {v for _, v in NODE_SCALES}:
            self.flow_node_scale = 100
        if self.flow_width not in {v for _, v in WIDTH_OPTIONS}:
            self.flow_width = 0
        n, seen, cols = self.ncols, set(), []
        for col in self.columns if isinstance(self.columns, list) else []:
            keep = []
            for e in col if isinstance(col, list) else []:
                if e in ELEMENTS and e not in seen and self.in_columns(e):
                    keep.append(e); seen.add(e)
            cols.append(keep)
        while len(cols) > n:           # fewer columns: fold the extra ones into the last
            extra = cols.pop(); cols[-1] += extra
        while len(cols) < n:
            cols.append([])
        for e in ORDER:                # every enabled element must be placed somewhere
            if self.in_columns(e) and e not in seen:
                if e == "toc":
                    cols[0].insert(0, e)
                elif e in ("flow", "details", "json"):   # the content goes to the wide (last) column
                    cols[-1].append(e)
                else:
                    cols[0].append(e)
        self.columns = cols
        self.title_prefix = str(self.title_prefix or "")
        self.labels = list(dict.fromkeys(l for l in (self.labels if isinstance(self.labels, list) else []) if l))
        return self

    # ---- persistence ----
    @classmethod
    def from_dict(cls, d: dict) -> "Template":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in known}).normalize()

    @classmethod
    def load(cls, path: Path) -> "Template":
        try:
            return cls.from_dict(json.loads(Path(path).read_text("utf-8-sig")))
        except (OSError, ValueError, TypeError):
            return cls().normalize()

    def save(self, path: Path) -> bool:
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(json.dumps(asdict(self), indent=2), "utf-8")
            return True
        except OSError:
            return False
