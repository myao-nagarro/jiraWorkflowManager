"""Heading text: Title Case with the usual exceptions."""
import re

ACRONYMS = {"id", "json", "url", "csv", "png", "api", "jql", "ui", "ids", "urls"}
SMALL = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of", "on", "or", "the", "to", "per", "via"}


def titlecase(text: str) -> str:
    """'Page layout' -> 'Page Layout', 'workflow scheme' -> 'Workflow Scheme', 'Statuses and transitions' -> 'Statuses and Transitions'.

    Small words stay lower case (except at the start), acronyms are upper case, and words that already have capitals inside
    them (JSON, draw.io, PNG) are left alone."""
    words = re.split(r"(\s+)", text)
    out, first = [], True
    for w in words:
        if not w.strip():
            out.append(w)
            continue
        bare = re.sub(r"^\W+|\W+$", "", w).lower()
        if bare in ACRONYMS:
            out.append(re.sub(r"[A-Za-z]+", lambda m: m.group(0).upper() if m.group(0).lower() in ACRONYMS else m.group(0), w, count=1))
        elif any(c.isupper() for c in w[1:]) or "/" in w and not first:
            out.append(w)                                   # JSON, draw.io, CSV, URL/email: keep as written
        elif bare in SMALL and not first:
            out.append(w.lower())
        else:
            out.append(re.sub(r"[A-Za-z]", lambda m: m.group(0).upper(), w, count=1))
        first = False
    return "".join(out)
