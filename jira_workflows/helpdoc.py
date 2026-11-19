"""The help topics: README.md split into topics (a topic for each `##` and `###` heading), with search. No Qt here."""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Topic:
    """One help topic: a section of the README."""
    title: str
    level: int                       # 2 for "##", 3 for "###"
    body: str                        # Markdown, without the heading line
    parent: str = ""                 # title of the "##" topic that holds a "###" topic
    slug: str = ""
    children: list = field(default_factory=list)

    @property
    def path(self) -> str:
        """Breadcrumb: the parent topic, then this one."""
        return f"{self.parent} › {self.title}" if self.parent else self.title


def slugify(title: str) -> str:
    """The anchor GitHub gives a heading: lowercase, punctuation dropped, spaces to hyphens (the README's links use it)."""
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", title.lower()).strip())


def readme_path():
    """README.md next to the program (bundled in the executable, or the source folder), or None."""
    for base in (Path(getattr(sys, "_MEIPASS", "")), Path(__file__).resolve().parent.parent, Path(sys.argv[0]).resolve().parent):
        f = base / "README.md"
        if f.is_file():
            return f
    return None


def load_text() -> str:
    f = readme_path()
    try:
        return f.read_text("utf-8") if f else ""
    except OSError:
        return ""


def parse(text: str) -> list:
    """All topics in reading order: an "Overview" first (the README's introduction), then each `##` and `###` section."""
    topics, cur, fence, intro, parent = [], None, False, [], ""
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"(#{1,3}) +(.+?) *#*$", line)
        if m and len(m.group(1)) >= 2:
            level, title = len(m.group(1)), m.group(2).strip()
            if level == 2:
                parent = title
            cur = Topic(title, level, "", parent if level == 3 else "", slugify(title))
            topics.append(cur)
        elif cur is None:
            if not (m and len(m.group(1)) == 1):
                intro.append(line)
        else:
            cur.body += line + "\n"
    for t in topics:
        t.body = t.body.strip("\n")
    overview = "\n".join(intro).strip()
    if overview:
        topics.insert(0, Topic("Overview", 2, overview, "", "overview"))
    for t in topics:
        if t.level == 3:
            holder = next((p for p in topics if p.level == 2 and p.title == t.parent), None)
            if holder:
                holder.children.append(t)
    return topics


def search(topics, query: str) -> list:
    """The topics that contain every word of the query (in the title or the text); a title match comes first."""
    words = [w for w in query.casefold().split() if w]
    if not words:
        return list(topics)
    hits = []
    for t in topics:
        title, text = t.title.casefold(), t.body.casefold()
        if all(w in title or w in text for w in words):
            hits.append((0 if all(w in title for w in words) else 1, t))
    return [t for _, t in sorted(hits, key=lambda x: x[0])]
