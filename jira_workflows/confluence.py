"""Confluence Cloud client for publishing workflow documentation (adapted from the Automation Exporter)."""
import re
import time
from urllib.parse import parse_qs, urlparse

import requests

from . import APP_NAME


class ConfluenceError(Exception):
    pass


class ConflictError(ConfluenceError):
    """A page with this title already exists in the space."""


REQUIRED_LABEL = "jira-workflow"  # added to every published page and used by the parent page's report
# keys of the "Workflow information" table, as listed by the parent page's report
REPORT_HEADINGS = "Workflow ID,Scope,Spaces,Workflow schemes,State,Last updated,Workflow URL"


def page_title(name: str, prefix: str = "", prefix_on: bool = False) -> str:
    """The Confluence page title: [custom prefix] workflow name."""
    parts = []
    if prefix_on and prefix.strip():
        parts.append(prefix.strip())
    parts.append(name.strip())
    return " ".join(parts)[:250]


def report_macro(parent_id) -> str:
    """The "Content properties report" macro: lists the labelled child pages with their workflow information."""
    return ('<ac:structured-macro ac:name="detailssummary" ac:schema-version="2">'
            f'<ac:parameter ac:name="cql">label = "{REQUIRED_LABEL}" and parent = {parent_id}</ac:parameter>'
            f'<ac:parameter ac:name="headings">{REPORT_HEADINGS}</ac:parameter>'
            '<ac:parameter ac:name="pageSize">100</ac:parameter></ac:structured-macro>')


def parse_target(url: str):
    """Split a Confluence page/folder URL into (site base URL, 'page' | 'folder', content id)."""
    url = (url or "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    u = urlparse(url)
    if not u.netloc:
        raise ConfluenceError("Enter the URL of a Confluence parent page or folder.")
    base = f"{u.scheme}://{u.netloc}"
    m = re.search(r"/wiki/spaces/[^/]+/(pages|folder)/(\d+)", u.path)
    if m:
        return base, ("folder" if m.group(1) == "folder" else "page"), m.group(2)
    q = parse_qs(u.query)
    if q.get("pageId"):
        return base, "page", q["pageId"][0]
    raise ConfluenceError("That URL is not a Confluence page or folder. Expected …/wiki/spaces/<KEY>/pages/<id>/… "
                          "or …/wiki/spaces/<KEY>/folder/<id>. Open the page or folder and copy its full address.")


def client_for(jira, base: str) -> "ConfluenceClient":
    """The Confluence client that signs in the way the Jira client does. A service account belongs to one site: it cannot publish to
    the Confluence of another one."""
    if not getattr(jira, "cloud_id", ""):
        return ConfluenceClient(base, jira.session.auth)
    if urlparse(base).netloc.lower() != urlparse(jira.site).netloc.lower():
        raise ConfluenceError(f"The service account you signed in with belongs to {urlparse(jira.site).netloc}. "
                              f"It cannot publish to {urlparse(base).netloc}.")
    return ConfluenceClient(base, jira.session.auth, api_base=f"https://api.atlassian.com/ex/confluence/{jira.cloud_id}")


def clean_label(text: str) -> str:
    """Confluence labels are lower case, without spaces."""
    t = re.sub(r"\s+", "-", (text or "").strip().lower())
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9_\-:.]", "", t)).strip("-")[:255]


def _err(r) -> str:
    try:
        errs = r.json().get("errors") or []
        if errs:
            return "; ".join(str(e.get("title") or e.get("detail") or e) for e in errs)
        return r.json().get("message") or r.text[:300]
    except ValueError:
        return r.text[:300] or f"HTTP {r.status_code}"


class ConfluenceClient:
    def __init__(self, base: str, auth, api_base: str = ""):
        self.base = base.rstrip("/")                 # the site address people use
        self.api_base = (api_base or base).rstrip("/")   # where the requests go (the Atlassian gateway for a service account)
        self.s = requests.Session()
        self.s.auth = auth
        self.s.headers.update({"Accept": "application/json"})

    def _call(self, method, path, **kw):
        try:
            return self.s.request(method, f"{self.api_base}{path}", timeout=60, **kw)
        except requests.RequestException as e:
            raise ConfluenceError(str(e) if getattr(e, "friendly", False) else f"Network error: {e}") from e

    def _web(self, url: str) -> str:
        """A link from a response, as an address people can open (not the gateway address)."""
        return self.base + url[len(self.api_base):] if self.api_base != self.base and url.startswith(self.api_base) else url

    def resolve_parent(self, kind: str, content_id: str) -> dict:
        r = self._call("GET", f"/wiki/api/v2/{'folders' if kind == 'folder' else 'pages'}/{content_id}")
        if r.status_code in (401, 403):
            raise ConfluenceError(f"Confluence rejected the credentials, or you cannot view that parent. ({_err(r)})")
        if r.status_code == 404:
            raise ConfluenceError(f"The {kind} was not found, or you do not have permission to see it.")
        if not r.ok:
            raise ConfluenceError(f"Could not read the parent {kind}: {_err(r)}")
        d = r.json()
        space = self._call("GET", f"/wiki/api/v2/spaces/{d['spaceId']}")
        sp = space.json() if space.ok else {}
        return {"kind": kind, "id": str(d["id"]), "title": d.get("title", ""), "spaceId": str(d["spaceId"]),
                "spaceName": sp.get("name", ""), "spaceKey": sp.get("key", "")}

    def create_page(self, space_id, parent_id, title, body) -> dict:
        r = self._call("POST", "/wiki/api/v2/pages", json={
            "spaceId": space_id, "status": "current", "title": title, "parentId": parent_id,
            "body": {"representation": "storage", "value": body}})
        if not r.ok:
            msg = _err(r)
            if r.status_code in (400, 409) and "already exists" in msg.lower():
                raise ConflictError(msg)
            raise ConfluenceError(f"Could not create the page ({r.status_code}): {msg}")
        d = r.json()
        links = d.get("_links", {})
        return {"id": str(d["id"]), "url": self._web(links.get("base", f"{self.base}/wiki") + links.get("webui", ""))}

    def find_page(self, space_id, title):
        """Existing current page with this title in the space, or None."""
        r = self._call("GET", "/wiki/api/v2/pages", params={"space-id": space_id, "title": title, "status": "current", "limit": 25})
        if not r.ok:
            raise ConfluenceError(f"Could not look up existing pages ({r.status_code}): {_err(r)}")
        for p in r.json().get("results", []):
            # never touch a page in another space, even if the server ignored the space filter
            if str(p.get("spaceId")) == str(space_id) and p.get("title", "").casefold() == title.casefold():
                return {"id": str(p["id"]), "version": (p.get("version") or {}).get("number"), "parentId": str(p.get("parentId") or "")}
        return None

    def update_page(self, page_id, title, body, version=None, parent_id=None) -> dict:
        """Overwrite an existing page (Confluence keeps the old content in the page history), optionally moving it under a parent."""
        if version is None:
            r = self._call("GET", f"/wiki/api/v2/pages/{page_id}")
            if not r.ok:
                raise ConfluenceError(f"Could not read the existing page ({r.status_code}): {_err(r)}")
            version = r.json()["version"]["number"]

        def put(parent):
            payload = {"id": page_id, "status": "current", "title": title, "body": {"representation": "storage", "value": body},
                       "version": {"number": version + 1, "message": f"Updated by {APP_NAME}"}}
            if parent:
                payload["parentId"] = str(parent)
            return self._call("PUT", f"/wiki/api/v2/pages/{page_id}", json=payload)

        r, move_error = put(parent_id), ""
        if not r.ok and parent_id and "parent-child loop" in _err(r).lower():
            move_error = "the parent is inside this page, so Confluence cannot move it there"
            r = put(None)
        if not r.ok:
            raise ConfluenceError(f"Could not overwrite the page ({r.status_code}): {_err(r)}")
        links = r.json().get("_links", {})
        return {"id": str(page_id), "move_error": move_error,
                "url": self._web(links.get("base", f"{self.base}/wiki") + links.get("webui", f"/pages/viewpage.action?pageId={page_id}"))}

    def set_property(self, page_id, key, value):
        """Create or update a page content property (used to switch a page to full width)."""
        r = self._call("GET", f"/wiki/api/v2/pages/{page_id}/properties", params={"key": key})
        found = r.json().get("results", []) if r.ok else []
        if found:
            r = self._call("PUT", f"/wiki/api/v2/pages/{page_id}/properties/{found[0]['id']}", json={
                "key": key, "value": value, "version": {"number": found[0]["version"]["number"] + 1}})
        else:
            r = self._call("POST", f"/wiki/api/v2/pages/{page_id}/properties", json={"key": key, "value": value})
        if not r.ok:
            raise ConfluenceError(f"Could not set {key} ({r.status_code}): {_err(r)}")

    def full_width(self, page_id, on=True):
        for key in ("content-appearance-published", "content-appearance-draft"):
            self.set_property(page_id, key, "full-width" if on else "fixed-width")

    def attach(self, page_id, filename, data: bytes, mime="image/png"):
        """Upload an attachment; a file with the same name on the page gets a new version.

        Confluence Cloud sometimes answers a new-version upload with a 500 (a rolled-back transaction). The upload is then retried
        once, and if it still fails the old attachment is deleted (it goes to the trash) and the file is uploaded as a new one."""
        base = f"/wiki/rest/api/content/{page_id}/child/attachment"
        r = self._call("GET", base, params={"filename": filename})
        existing = r.json().get("results", []) if r.ok else []

        def upload(path):
            return self._call("POST", path, headers={"X-Atlassian-Token": "no-check"}, files={"file": (filename, data, mime)},
                              data={"minorEdit": "true"})

        r = upload(f"{base}/{existing[0]['id']}/data" if existing else base)
        if not r.ok and existing and r.status_code >= 500:
            time.sleep(1.5)
            r = upload(f"{base}/{existing[0]['id']}/data")
            if not r.ok:
                gone = self._call("DELETE", f"/wiki/rest/api/content/{existing[0]['id']}")
                if gone.ok or gone.status_code == 404:
                    r = upload(base)
        if not r.ok:
            raise ConfluenceError(f"Could not attach {filename} ({r.status_code}): {_err(r)}")

    def diagram_section(self, page_id) -> str:
        """The storage markup under the "Workflow Diagram" heading of an existing page (the image / draw.io link), or ''."""
        r = self._call("GET", f"/wiki/api/v2/pages/{page_id}", params={"body-format": "storage"})
        if not r.ok:
            return ""
        body = ((r.json().get("body") or {}).get("storage") or {}).get("value", "")
        m = re.search(r"<h2[^>]*>\s*Workflow Diagram\s*</h2>(.*?)(?=<h[1-6][\s>]|</ac:layout-cell>|$)", body, re.S)
        # a picture added by hand (an attachment, a link to an image) or a draw.io macro counts as a diagram; the placeholder panel does not
        return m.group(1) if m and any(x in m.group(1) for x in ("ri:attachment", "ri:url", 'ac:name="drawio"')) else ""

    def add_report_to_parent(self, parent_id) -> str:
        """Append the report macro to the parent page unless it already has one. Returns 'added' or 'present'."""
        r = self._call("GET", f"/wiki/api/v2/pages/{parent_id}", params={"body-format": "storage"})
        if not r.ok:
            raise ConfluenceError(f"Could not read the parent page ({r.status_code}): {_err(r)}")
        d = r.json()
        body = d["body"]["storage"]["value"]
        if 'ac:name="detailssummary"' in body and REQUIRED_LABEL in body:
            return "present"
        macro, end = report_macro(parent_id), "</ac:layout>"
        stripped = body.rstrip()
        if stripped.endswith(end):  # pages built with layouts: add the macro in a new full-width section
            body = stripped[:-len(end)] + '<ac:layout-section ac:type="single"><ac:layout-cell>' + macro + "</ac:layout-cell></ac:layout-section>" + end
        else:
            body += macro
        r = self._call("PUT", f"/wiki/api/v2/pages/{parent_id}", json={
            "id": str(parent_id), "status": "current", "title": d["title"], "body": {"representation": "storage", "value": body},
            "version": {"number": d["version"]["number"] + 1, "message": f"Added the {REQUIRED_LABEL} workflows report"}})
        if not r.ok:
            raise ConfluenceError(f"Could not update the parent page ({r.status_code}): {_err(r)}")
        return "added"

    def list_labels(self, limit=250, pages=4) -> list:
        """Names of existing labels on the site (for choosing instead of typing)."""
        names, path, params = [], "/wiki/api/v2/labels", {"limit": limit}
        for _ in range(pages):
            r = self._call("GET", path, params=params)
            if not r.ok:
                raise ConfluenceError(f"Could not list labels ({r.status_code}): {_err(r)}")
            d = r.json()
            names += [x["name"] for x in d.get("results", []) if x.get("prefix", "global") == "global"]
            nxt = (d.get("_links") or {}).get("next")
            if not nxt:
                break
            path, params = nxt, None
        return sorted(dict.fromkeys(names))

    def add_labels(self, page_id, labels):
        if not labels:
            return
        r = self._call("POST", f"/wiki/rest/api/content/{page_id}/label", json=[{"prefix": "global", "name": n} for n in labels])
        if not r.ok:
            raise ConfluenceError(f"Could not add labels ({r.status_code}): {_err(r)}")
