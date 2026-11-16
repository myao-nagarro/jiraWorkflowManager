"""A publish job: a snapshot of what was published (workflows, template, destination) and the generator of the portable,
GUI-less Python script that repeats it. No Qt, no network."""
import ast
import json
import pprint
import re
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from . import APP_NAME, __version__
from .confluence import REQUIRED_LABEL, clean_label

# the modules the script needs (the GUI and the preview are not part of it)
# (no Qt: the job does not draw workflow diagrams)
BUNDLED = ["__init__", "textutil", "api", "model", "screens", "template", "confluence", "page"]
# the sources sit next to this file, or in this folder inside the single-file executable (see build.py)
SOURCE_DIR = "jira_workflows"


def _source_dir() -> Path:
    here = Path(__file__).parent
    if (here / "runner_template.py").exists():
        return here
    return Path(getattr(sys, "_MEIPASS", here)) / SOURCE_DIR


def make_job(site: str, template, parent: dict, parent_url: str, workflows: list, spaces: list = ()) -> dict:
    """The job in the form the script and the Job page use. `parent` is {} while the destination is not known yet (a job started from the
    Workflows page): the Publish page sets it."""
    job = {
        "created": datetime.now().astimezone().isoformat(timespec="seconds"),
        "app_version": __version__,
        "script": {"title": "", "contact": ""},   # text of the comment at the top of the script; empty title: the job name
        "site": site,
        "workflows": [{"id": w["id"], "name": w["name"]} for w in workflows],
        "spaces": [{"key": s["key"], "name": s["name"]} for s in spaces],   # every workflow of these spaces is in the job's scope
        "diagram_placeholder": True,   # a page without a diagram gets a placeholder for it (the job cannot draw one)
        "destination": {"url": parent_url, "kind": parent.get("kind", "page"), "title": parent.get("title", ""),
                        "space_key": parent.get("spaceKey", ""), "space_name": parent.get("spaceName", "")},
    }
    apply_template(job, template)
    job["name"] = job_name(job)
    return job


def apply_template(job: dict, template) -> None:
    """Take the template, and the title prefix / labels / report that go with it, into the job."""
    d = job["destination"]
    d["title_prefix_on"], d["title_prefix"] = template.title_prefix_on, template.title_prefix
    d["labels"] = [clean_label(x) for x in template.labels if clean_label(x)]
    d["parent_report"] = bool(template.parent_report and d.get("kind") == "page")
    job["template"] = asdict(template)


def job_name(job: dict) -> str:
    """'Publish 3 workflow(s) and 2 space(s) to <parent>': the title of the script when the user gave none."""
    scope = f"{len(job['workflows'])} workflow(s)" + (f" and {len(job['spaces'])} space(s)" if job.get("spaces") else "")
    return f"Publish {scope} to {job['destination'].get('title') or 'Confluence'}"


def can_export(job: dict) -> str:
    """'' when the job can be exported, else the reason it cannot."""
    if not job["destination"].get("url"):
        return "The job has no destination yet. Open the Publish page, enter the parent page or folder and click Create Job."
    if not job["workflows"] and not job.get("spaces"):
        return "The job has nothing to publish. Select workflows or spaces first."
    return ""


def save_job(job: dict, path: Path) -> bool:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(job, indent=2, ensure_ascii=False), "utf-8")
        return True
    except OSError:
        return False


def load_job(path: Path):
    try:
        job = json.loads(Path(path).read_text("utf-8"))
        return job if isinstance(job, dict) and job.get("workflows") and job.get("destination") else None
    except (OSError, ValueError):
        return None


def script_name(job: dict) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (job["destination"].get("title") or "confluence").lower()).strip("_")[:40] or "confluence"
    return f"publish_workflows_{slug}.py"


def _section(name: str, source: str, imports: dict) -> str:
    """One module as a section of the script: its docstring becomes a comment, and the imports of sibling modules are dropped
    (everything lives in the same file)."""
    tree = ast.parse(source)
    lines = source.splitlines()
    drop = set()
    doc = ""
    if tree.body and isinstance(tree.body[0], ast.Expr) and isinstance(getattr(tree.body[0], "value", None), ast.Constant) \
            and isinstance(tree.body[0].value.value, str):
        first = tree.body[0]
        doc = ast.get_docstring(tree) or ""
        drop.update(range(first.lineno, first.end_lineno + 1))
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.level > 0:   # a sibling module: it is in this file
            drop.update(range(node.lineno, node.end_lineno + 1))
        elif isinstance(node, (ast.Import, ast.ImportFrom)):      # a library: collected into one import block
            drop.update(range(node.lineno, node.end_lineno + 1))
            for a in node.names:
                if isinstance(node, ast.Import):
                    imports.setdefault(None, set()).add(a.name)
                else:
                    imports.setdefault(node.module, set()).add(a.name)
    body = "\n".join(ln for i, ln in enumerate(lines, 1) if i not in drop).strip("\n")
    banner = f"# ---- {name}.py " + "-" * max(4, 100 - len(name) - 12)
    comment = "".join(f"# {ln}".rstrip() + "\n" for ln in doc.splitlines())
    return f"{banner}\n{comment}\n{body}\n"


def _library(header: str) -> str:
    """The modules as sections; `header` is the script text above them, whose imports are not repeated."""
    src = _source_dir()
    imports = {}
    sections = [_section(name, (src / f"{name}.py").read_text("utf-8-sig"), imports) for name in BUNDLED]
    block = [f"import {m}" for m in sorted(imports.pop(None, ()))]
    block += [f"from {m} import {', '.join(sorted(names))}" for m, names in sorted(imports.items())]
    block = [ln for ln in block if "\n" + ln + "\n" not in header]
    return "# imports used by the sections below\n" + "\n".join(block) + "\n\n\n" + "\n\n".join(sections)


def _comment_text(text: str) -> str:
    """User text for the comment at the top of the script: one line that cannot end the docstring or start an escape sequence."""
    one_line = " ".join(str(text or "").split())
    return one_line.replace(chr(92), "/").replace(chr(34) * 3, chr(39) * 3)


def generate_script(job: dict) -> str:
    """The Python source of the job: one readable file. It holds no credentials: the user passes -user and -token when running it."""
    text = (_source_dir() / "runner_template.py").read_text("utf-8")
    custom = job.get("script") or {}
    contact = _comment_text(custom.get("contact", ""))
    values = {"__TITLE__": _comment_text(custom.get("title", "")) or _comment_text(job["name"]),
              "__CONTACT__": f"Contact: {contact}\n" if contact else "", "__GENERATED__": datetime.now().strftime("%Y-%m-%d %H:%M"), "__APP__": APP_NAME,
              "__VERSION__": __version__, "__FILE__": script_name(job), "__JOB__": pprint.pformat(job, width=110, sort_dicts=False),
              "__LIBRARY__": _library(text)}
    for key, value in values.items():
        text = text.replace(key, value)
    return text


DIAGRAM_NOTICE = ("The job does not create or update workflow diagrams. A page that already has a diagram keeps it. A page without one gets "
                  "a placeholder where the template puts the diagram (if you keep the placeholder on), and you can add the picture to it by "
                  "editing the page.")


# ---- what the job will do, as plain text for the Job page ------------------------------------------------------
def describe_destination(job: dict) -> list:
    d = job["destination"]
    if not d.get("url"):
        return [("Site", job["site"]), ("Destination", "not set yet")]
    return [("Site", job["site"]), (d["kind"].capitalize(), d["title"] or d["url"]),
            ("Space", f"{d['space_name']} ({d['space_key']})" if d.get("space_name") else d.get("space_key") or "—"),
            ("Address", d["url"]),
            ("Page titles", (d["title_prefix"] + " " if d["title_prefix_on"] and d["title_prefix"] else "") + "<workflow name>"),
            ("Labels", ", ".join([REQUIRED_LABEL] + d["labels"])),
            ("Content properties report", "added to the parent page" if d["parent_report"] else "not added"),
            ("Existing pages", "overwritten when the title matches (Confluence keeps the history)")]


def diagram_position(t) -> str:
    """Where the template puts the workflow diagram, in words: the column and the place in it."""
    from .template import layout_info
    _, _, captions = layout_info(t.layout)
    for caption, col in zip(captions, t.columns):
        if "flow" in col:
            return f"{caption}, element {col.index('flow') + 1} of {len(col)}"
    return "not in the layout"


def describe_template(job: dict) -> list:
    """(label, value) rows for the Confluence template configuration of the job."""
    from .template import ELEMENTS, Template, layout_info
    t = Template.from_dict(job["template"])
    _, _, captions = layout_info(t.layout)
    rows = [("Layout", layout_info(t.layout)[0] + (", full width page" if t.full_width else ", fixed width page"))]
    for caption, col in zip(captions, t.columns):
        rows.append((f"Column: {caption}", " → ".join(ELEMENTS[e] for e in col) or "empty"))
    if t.details and t.details_full_width:
        rows.append(("Full width band", ELEMENTS["details"]))
    if not t.flow:
        rows.append(("Workflow diagram", "off"))
    else:
        rows.append(("Workflow diagram", "not drawn by the job; an existing diagram on the page is kept; "
                     + ("a page without one gets a placeholder at the position below" if job.get("diagram_placeholder", True)
                        else "a page without one is published without a diagram")))
        rows.append(("Diagram position", diagram_position(t)))
    if t.code_block:
        rows.append(("Raw JSON", "code block" + (", collapsed" if t.code_collapse else "") + (", line numbers" if t.code_lines else "")))
    rows.append(("Table of contents", "on" if t.toc else "off"))
    return rows
