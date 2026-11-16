"""Transition screens as documentation: name, ID, link into Jira, tabs and the fields on each tab."""
from .api import screen_url


def field_type(f: dict) -> str:
    """A readable field type: Jira's own display name when it has one, else derived from the field's schema."""
    if f.get("typeDisplayName"):
        return f["typeDisplayName"]
    schema = f.get("schema") or {}
    custom = schema.get("custom")
    if custom:
        return custom.split(":")[-1].replace("-", " ").replace("_", " ").strip().capitalize() + " (custom)"
    kind = schema.get("type", "")
    return f"{kind} of {schema['items']}" if kind == "array" and schema.get("items") else kind


def field_catalog(client) -> dict:
    """{field id: {name, type, description}} for every field of the site (system and custom)."""
    out = {}
    for f in client.fields():
        out[f["id"]] = {"name": f.get("name", f["id"]), "type": field_type(f), "description": f.get("description", "")}
    for f in client.field_search():   # richer: display name of the type and the description
        known = out.setdefault(f["id"], {"name": f.get("name", f["id"]), "type": "", "description": ""})
        known["type"] = field_type(f) or known["type"]
        known["description"] = f.get("description") or known["description"]
    return out


def load_details(client, screens, fields: dict, site: str) -> dict:
    """screens: [(id, name)]. Returns {screen id: {id, name, url, tabs: [{id, name, fields: [{id, name, type, description}]}]}}."""
    out = {}
    for sid, name in screens:
        tabs = []
        for tab in client.screen_tabs(sid):
            rows = []
            for f in client.screen_tab_fields(sid, tab["id"]):
                info = fields.get(f["id"], {})
                rows.append({"id": f["id"], "name": f.get("name") or info.get("name", f["id"]), "type": info.get("type", ""),
                             "description": info.get("description", "")})
            tabs.append({"id": str(tab["id"]), "name": tab.get("name", ""), "fields": rows})
        out[str(sid)] = {"id": str(sid), "name": name, "url": screen_url(site, sid), "tabs": tabs}
    return out
