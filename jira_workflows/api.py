"""Jira Cloud REST client for workflows, workflow schemes, statuses and projects."""
import re
import time
from urllib.parse import quote, urlparse

import requests


class JiraError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


# ---- sign in with a service account: OAuth 2.0, client credentials grant ----------------------------
TOKEN_URL = "https://api.atlassian.com/oauth/token"
GATEWAY = "https://api.atlassian.com/ex"   # an OAuth token is used on <gateway>/<product>/<cloud id>/<the usual REST path>


class OAuthError(requests.RequestException):
    """The token endpoint refused the client ID / client secret. The message is meant for the user."""
    friendly = True

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class OAuthBearer(requests.auth.AuthBase):
    """Bearer token of a service account. The token is requested when it is first needed and again a little before it expires, so a long
    session (or a job that runs for a long time) keeps working."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id, self.client_secret = client_id.strip(), client_secret.strip()
        self.token, self.expires = "", 0.0

    def fetch(self):
        try:
            r = requests.post(TOKEN_URL, json={"grant_type": "client_credentials", "audience": "api.atlassian.com",
                                               "client_id": self.client_id, "client_secret": self.client_secret},
                              headers={"Accept": "application/json"}, timeout=30)
        except requests.RequestException as e:
            raise OAuthError(f"Network error while signing in: {e}") from e
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not r.ok or not data.get("access_token"):
            why = data.get("error_description") or data.get("error") or r.text[:160] or f"HTTP {r.status_code}"
            raise OAuthError(f"Sign in failed ({r.status_code}): {why}. Check the client ID and the client secret of the service account.",
                             r.status_code)
        self.token = data["access_token"]
        self.expires = time.time() + int(data.get("expires_in") or 3600) - 60

    def __call__(self, request):
        if not self.token or time.time() >= self.expires:
            self.fetch()
        request.headers["Authorization"] = f"Bearer {self.token}"
        return request


def lookup_cloud_id(site: str) -> str:
    """The cloud ID of a Jira Cloud site (an OAuth token is used with it, not with the site address)."""
    try:
        r = requests.get(f"{site}/_edge/tenant_info", timeout=30)
        cloud_id = (r.json().get("cloudId") if r.ok else "") or ""
    except (requests.RequestException, ValueError) as e:
        raise JiraError(f"Could not reach {site}: {e}") from e
    if not cloud_id:
        raise JiraError(f"Could not find the Atlassian cloud ID of {site}. Check the site URL.")
    return cloud_id


def normalize_site(url: str) -> str:
    url = url.strip().rstrip("/")
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}"


# ---- links into the Jira admin UI (kept together so they are easy to adjust) ----------------
def workflow_edit_url(site: str, wf: dict) -> str:
    if wf.get("project_keys") and wf.get("scope") == "PROJECT":  # team-managed: edited in the space settings
        return f"{site}/jira/software/projects/{quote(wf['project_keys'][0])}/settings/workflows"
    return f"{site}/secure/admin/workflows/ViewWorkflowSteps.jspa?workflowMode=live&workflowName={quote(wf['name'], safe='')}"


def transition_url(site: str, wf: dict, transition_id) -> str:
    """The transition's page in the (classic) workflow editor, opened on its screen tab."""
    return (f"{site}/secure/admin/workflows/ViewWorkflowTransition.jspa?workflowMode=live"
            f"&workflowName={quote(wf['name'], safe='')}&workflowTransition={quote(str(transition_id), safe='')}&descriptorTab=screen")


def status_url(site: str, wf: dict, status_id=None) -> str:
    """The workflow in the (new) workflow editor with this status selected."""
    return f"{site}/jira/settings/issues/workflows/{quote(str(wf['id']), safe='')}?selectedStatus={quote(str(status_id), safe='')}"


def screen_url(site: str, screen_id) -> str:
    return f"{site}/secure/admin/ConfigureFieldScreen.jspa?id={screen_id}"


def scheme_edit_url(site: str, scheme_id) -> str:
    return f"{site}/secure/admin/EditWorkflowScheme.jspa?schemeId={scheme_id}"


class JiraClient:
    def __init__(self, site: str, email: str, token: str):
        self.site = normalize_site(site)   # the address people use: links, cache folder, titles
        self.api_base = self.site          # where the requests go (the Atlassian gateway for a service account)
        self.cloud_id = ""                 # set for a service account
        self.email = email.strip()
        self.session = requests.Session()
        self.session.auth = (self.email, token.strip())
        self.session.headers.update({"Accept": "application/json"})

    @classmethod
    def service_account(cls, site: str, client_id: str, client_secret: str):
        """Sign in with the OAuth 2.0 credentials of a service account. The account is shown by its client ID."""
        client = cls(site, client_id, "")
        client.cloud_id = lookup_cloud_id(client.site)
        client.api_base = f"{GATEWAY}/jira/{client.cloud_id}"
        client.session.auth = OAuthBearer(client_id, client_secret)
        return client

    # ---- low level -------------------------------------------------
    def _request(self, method, path, **kw):
        try:
            return self.session.request(method, f"{self.api_base}{path}", timeout=60, **kw)
        except OAuthError as e:
            raise JiraError(str(e), e.status) from e
        except requests.RequestException as e:
            raise JiraError(f"Network error: {e}") from e

    def _json(self, method, path, what, **kw):
        r = self._request(method, path, **kw)
        if r.status_code in (401, 403):
            raise JiraError(f"Not allowed to read {what} ({r.status_code}). Workflows need the Administer Jira permission.", r.status_code)
        if not r.ok:
            raise JiraError(f"Could not read {what} ({r.status_code}): {r.text[:160]}", r.status_code)
        return r.json()

    def _paged(self, path, what, params=None, key="values", size=50):
        out, start = [], 0
        while True:
            d = self._json("GET", path, what, params={**(params or {}), "startAt": start, "maxResults": size})
            if isinstance(d, list):  # some endpoints answer with a flat list
                return out + d
            items = d.get(key, [])
            out += items
            if not items or d.get("isLast") or start + len(items) >= d.get("total", float("inf")):
                return out
            start += len(items)

    # ---- public ----------------------------------------------------
    def login(self) -> dict:
        r = self._request("GET", "/rest/api/3/myself")
        if r.status_code in (401, 403):
            if self.cloud_id:
                raise JiraError(f"Jira rejected the service account ({r.status_code}). Check that it has access to this site and that its "
                                "OAuth credentials include the Jira API scopes.", r.status_code)
            raise JiraError("Authentication failed. Check the email and API token.", r.status_code)
        if not r.ok:
            raise JiraError(f"Could not reach Jira ({r.status_code}). Check the site URL.", r.status_code)
        return r.json()

    def projects(self) -> list:
        return self._paged("/rest/api/3/project/search", "spaces", size=100)

    def statuses(self) -> list:
        try:
            return self._paged("/rest/api/3/statuses/search", "statuses", size=200)
        except JiraError as e:
            if e.status not in (400, 404, 405, 410):
                raise
        return self._json("GET", "/rest/api/3/status", "statuses")  # legacy: one flat list

    def screens(self) -> list:
        """All screens (id, name). Optional: without it the transition tab shows screen IDs only."""
        try:
            return self._paged("/rest/api/3/screens", "screens", size=100)
        except JiraError:
            return []

    def issue_types(self) -> list:
        """Work types (id, name); optional, without it the schemes list work type IDs."""
        try:
            r = self._request("GET", "/rest/api/3/issuetype")
            return r.json() if r.ok else []
        except (JiraError, ValueError):
            return []

    def resolutions(self) -> list:
        """Every resolution (id, name, description, isDefault); optional, an empty list when Jira will not say."""
        try:
            return self._paged("/rest/api/3/resolution/search", "resolutions", size=100)
        except JiraError:
            try:
                r = self._request("GET", "/rest/api/3/resolution")   # the older endpoint: a flat list without the default flag
                return r.json() if r.ok else []
            except (JiraError, ValueError):
                return []

    def delete_workflow_scheme(self, scheme: dict):
        """Delete one workflow scheme that no space uses."""
        if scheme.get("active"):
            raise JiraError("A workflow scheme that is used by a space cannot be deleted.")
        r = self._request("DELETE", f"/rest/api/3/workflowscheme/{quote(str(scheme['id']), safe='')}")
        if r.status_code in (401, 403):
            raise JiraError("Not allowed to delete workflow schemes (Administer Jira permission required).", r.status_code)
        if r.status_code not in (200, 204):
            msg = ""
            try:
                msg = "; ".join(r.json().get("errorMessages", []))
            except ValueError:
                pass
            raise JiraError(msg or f"Jira returned {r.status_code}.", r.status_code)

    def delete_status(self, status: dict):
        """Delete one status that no workflow uses."""
        if status.get("workflows"):
            raise JiraError("A status that is used by a workflow cannot be deleted.")
        r = self._request("DELETE", "/rest/api/3/statuses", params=[("id", status["id"])])
        if r.status_code in (401, 403):
            raise JiraError("Not allowed to delete statuses (Administer Jira permission required).", r.status_code)
        if r.status_code not in (200, 204):
            msg = ""
            try:
                body = r.json()
                msg = "; ".join(body.get("errorMessages", []) or [e.get("message", "") for e in body.get("errors", [])])
            except (ValueError, AttributeError):
                pass
            raise JiraError(msg or f"Jira returned {r.status_code}.", r.status_code)

    def screen_tabs(self, screen_id) -> list:
        return self._json("GET", f"/rest/api/3/screens/{quote(str(screen_id), safe='')}/tabs", "read the screen tabs")

    def screen_tab_fields(self, screen_id, tab_id) -> list:
        return self._json("GET", f"/rest/api/3/screens/{quote(str(screen_id), safe='')}/tabs/{quote(str(tab_id), safe='')}/fields",
                          "read the screen fields")

    def fields(self) -> list:
        """Every field of the site (system and custom)."""
        return self._json("GET", "/rest/api/3/field", "read the fields")

    def field_search(self) -> list:
        """Fields with their type display name and description (best effort: [] when the call is unavailable)."""
        try:
            return self._paged("/rest/api/3/field/search", "fields", size=100)
        except JiraError:
            return []

    def workflow_schemes(self) -> list:
        return self._paged("/rest/api/3/workflowscheme", "workflow schemes")

    def project_schemes(self, project_ids, progress=None) -> dict:
        """{project id: workflow scheme id}. Team-managed spaces have no workflow scheme and are skipped."""
        out, ids = {}, list(project_ids)
        for i in range(0, len(ids), 50):
            chunk = ids[i:i + 50]
            r = self._request("GET", "/rest/api/3/workflowscheme/project", params=[("projectId", p) for p in chunk])
            if r.ok:
                for v in r.json().get("values", []):
                    sid = (v.get("workflowScheme") or {}).get("id")
                    for pid in v.get("projectIds", []):
                        out[str(pid)] = sid
            if progress:
                progress(min(i + 50, len(ids)), len(ids), "Reading workflow scheme assignments")
        return out

    def workflows(self, progress=None):
        """(raw workflows, raw statuses). Uses the current workflow API, falling back to the legacy one."""
        try:
            return self._workflows_current(progress)
        except JiraError as e:
            if e.status not in (400, 404, 405, 410):
                raise
        return self._workflows_legacy(progress)

    def delete_workflow(self, wf: dict):
        """Delete one inactive workflow. Jira refuses active, default and draft-bound workflows."""
        if wf.get("active"):
            raise JiraError("Active workflows cannot be deleted.")
        r = self._request("DELETE", f"/rest/api/3/workflow/{quote(wf['id'], safe='')}")
        if r.status_code in (401, 403):
            raise JiraError("Not allowed to delete workflows (Administer Jira permission required).", r.status_code)
        if r.status_code not in (200, 204):
            msg = ""
            try:
                msg = "; ".join(r.json().get("errorMessages", []))
            except ValueError:
                pass
            raise JiraError(msg or f"Jira returned {r.status_code}.", r.status_code)

    def _workflows_current(self, progress):
        found = self._paged("/rest/api/3/workflows/search", "workflows")
        ids = [w["id"] for w in found if w.get("id")]
        flows, statuses = [], []
        for i in range(0, len(ids), 25):
            d = self._json("POST", "/rest/api/3/workflows", "workflows", json={"workflowIds": ids[i:i + 25]})
            flows += d.get("workflows", [])
            statuses += d.get("statuses", [])
            if progress:
                progress(min(i + 25, len(ids)), len(ids), "Reading workflows")
        # carry list-level facts (e.g. isActive) over to the full definitions
        by_id = {w.get("id"): w for w in found}
        for w in flows:
            for k, v in by_id.get(w.get("id"), {}).items():
                w.setdefault(k, v)
        return flows, statuses

    def _workflows_legacy(self, progress):
        flows = self._paged("/rest/api/3/workflow/search", "workflows",
                            params={"expand": "transitions,statuses,statuses.properties,schemes"})
        if progress:
            progress(len(flows), len(flows), "Reading workflows")
        return flows, []
