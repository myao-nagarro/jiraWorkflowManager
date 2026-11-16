"""Turns the raw REST payloads into one flat catalog of workflows and statuses (no Qt, no network)."""
from collections import defaultdict

CATEGORIES = {"TODO": ("To Do", 0), "IN_PROGRESS": ("In Progress", 1), "DONE": ("Done", 2)}
_CATEGORY_ALIASES = {"NEW": "TODO", "TO DO": "TODO", "INDETERMINATE": "IN_PROGRESS", "IN PROGRESS": "IN_PROGRESS",
                     "COMPLETE": "DONE"}
DEFAULT_WORKFLOW = "jira"  # a workflow scheme without a default workflow falls back to this system workflow


def category_key(value) -> str:
    """Accepts 'TODO', {'key': 'new'}, {'name': 'To Do'} ... and returns TODO | IN_PROGRESS | DONE."""
    if isinstance(value, dict):
        value = value.get("key") or value.get("name") or ""
    v = str(value or "").strip().upper().replace("-", "_")
    v = _CATEGORY_ALIASES.get(v, v)
    return v if v in CATEGORIES else "TODO"


def _ref(x):
    return (x.get("statusReference") or x.get("id")) if isinstance(x, dict) else x


def _pretty_rule(key: str) -> str:
    return (key or "").split(":")[-1].replace("-", " ").replace("_", " ").strip().capitalize()


def _rule_texts(node) -> list:
    """Readable names of every rule inside a rule list / condition group, e.g. 'Check permission validator (permissionKey=CREATE_ISSUES)'."""
    out = []
    if isinstance(node, dict):
        key = node.get("ruleKey") or node.get("type")
        if key:
            params = node.get("parameters") or node.get("configuration") or {}
            detail = ", ".join(f"{k}={v}" for k, v in params.items() if v not in ("", None, [], {}))[:70] if isinstance(params, dict) else ""
            out.append(_pretty_rule(key) + (f" ({detail})" if detail else ""))
        for k, v in node.items():
            if k in ("conditions", "conditionGroups", "validators", "postFunctions", "conditionsTree") or isinstance(v, (list, dict)) and k not in ("parameters", "configuration"):
                out += _rule_texts(v)
    elif isinstance(node, list):
        for v in node:
            out += _rule_texts(v)
    return out


RESOLUTION_PROPERTY = "servicedesk.customer.transition.resolution"   # the resolution a portal customer sets with a transition


def transition_resolutions(t: dict) -> list:
    """IDs of the resolutions a transition sets: a post function "Update issue field: resolution" with a value, and the Jira Service
    Management portal property. (An empty value means the transition clears the resolution.)"""
    ids = set()
    for rule in t.get("actions") or []:
        p = rule.get("parameters") or {} if isinstance(rule, dict) else {}
        if isinstance(rule, dict) and rule.get("ruleKey") == "system:update-field" and p.get("field") == "resolution" and str(p.get("value") or "").strip():
            ids.add(str(p["value"]).strip())
    v = str((t.get("properties") or {}).get(RESOLUTION_PROPERTY) or "").strip()
    if v:
        ids.add(v)
    return sorted(ids)


def normalize_status(raw: dict) -> dict:
    key = category_key(raw.get("statusCategory"))
    scope = raw.get("scope") or {}
    return {"id": str(raw.get("id", "")), "name": raw.get("name", ""), "category": key,
            "description": raw.get("description", ""), "ref": raw.get("statusReference"),
            "scope": scope.get("type", "GLOBAL"), "project_id": str((scope.get("project") or {}).get("id") or "")}


def normalize_workflow(raw: dict, by_id: dict, by_ref: dict) -> dict:
    """Common shape for the current (UUID-referenced statuses) and legacy workflow payloads."""
    ident = raw.get("id")
    name = ident.get("name") if isinstance(ident, dict) else raw.get("name", "")
    wid = ident.get("entityId") if isinstance(ident, dict) else ident

    def resolve(ref):
        ref = str(ref)
        return by_ref[ref]["id"] if ref in by_ref else ref

    statuses = []
    for s in raw.get("statuses", []):
        sid = resolve(_ref(s))
        known = by_id.get(sid) or {"id": sid, "name": s.get("name", sid), "category": "TODO"}
        props = {str(k): "" if v is None else str(v) for k, v in (s.get("properties") or {}).items()}  # workflow-specific
        lay = s.get("layout") or {}   # where the Jira workflow editor drew the status
        pos = (float(lay["x"]), float(lay["y"])) if isinstance(lay.get("x"), (int, float)) and isinstance(lay.get("y"), (int, float)) else None
        statuses.append({"id": known["id"], "name": known["name"] or s.get("name", sid), "category": known["category"],
                         "properties": props, "layout": pos})

    transitions = []
    for t in raw.get("transitions", []):
        to = resolve(_ref(t.get("toStatusReference") or t.get("to")))
        ttype = str(t.get("type") or "directed").lower()
        rules = t.get("rules") or {}  # legacy payloads nest the rules
        screen = t.get("screen") or {}  # legacy: {"id", "name"}
        screen_rule = t.get("transitionScreen") or {}  # current API: a rule with the screen ID as parameter
        screen_id = str(screen.get("id") or (screen_rule.get("parameters") or {}).get("screenId") or "")
        transitions.append({"id": str(t.get("id", "")), "name": t.get("name", ""), "type": ttype, "to": to,
                            "conditions": _rule_texts(t.get("conditions") or rules.get("conditionsTree")),
                            "validators": _rule_texts(t.get("validators") or rules.get("validators")),
                            "actions": _rule_texts(t.get("actions") or rules.get("postFunctions")),
                            "triggers": _rule_texts(t.get("triggers")),
                            "properties": {str(k): "" if v is None else str(v) for k, v in (t.get("properties") or {}).items()},
                            "event": str(t.get("customIssueEventId") or t.get("eventId") or ""),
                            "from": list(dict.fromkeys([resolve(_ref(f)) for f in t.get("from") or []]
                                                       + [resolve(l["fromStatusReference"]) for l in t.get("links") or []
                                                          if l.get("fromStatusReference")])),
                            "screen_id": screen_id, "screen_name": screen.get("name", ""), "resolutions": transition_resolutions(t),
                            "bolt": bool(t.get("actions") or t.get("triggers") or rules.get("postFunctions")),
                            "tree": bool(t.get("validators") or t.get("conditions") or rules.get("conditionsTree")
                                         or rules.get("validators"))})

    scope = raw.get("scope") or {}
    sl = raw.get("startPointLayout") or {}
    start_layout = (float(sl["x"]), float(sl["y"])) if isinstance(sl.get("x"), (int, float)) and isinstance(sl.get("y"), (int, float)) else None
    return {"key": name, "start_layout": start_layout, "id": str(wid or name), "name": name, "description": raw.get("description", ""),
            "updated": raw.get("updated") or "", "created": raw.get("created") or "",
            "scope": scope.get("type", "GLOBAL"), "scope_project": str((scope.get("project") or {}).get("id") or ""),
            "is_active": raw.get("isActive"), "legacy_schemes": raw.get("schemes") or [],
            "statuses": statuses, "transitions": transitions,
            "property_count": sum(len(s["properties"]) for s in statuses)}


# Statuses Jira creates by itself: the system statuses (Open, In Progress, Reopened, Resolved, Closed), the CI ones (Building,
# Build Broken) and the three of a new Jira Software site (To Do, In Review, Done), identified by their fixed IDs.
BUILTIN_IDS = {"1", "3", "4", "5", "6", "400", "401", "10000", "10001", "10002"}
# Statuses that the Jira Service Management project templates create (service request, incident, change, problem, approval flows).
JSM_NAMES = {"waiting for support", "waiting for customer", "pending", "canceled", "escalated", "waiting for approval", "declined", "approved",
             "rejected", "work in progress", "under review", "under investigation", "review", "implementing", "planning", "authorize",
             "completed", "failed", "awaiting implementation", "purchased", "requested"}


def status_tags(s: dict) -> list:
    """"Built-in" for a status Jira creates in every new site, "JSM" for one that Jira Service Management created: a status with
    one of the JSM template names that is used by a Jira Service Management workflow, or by no workflow at all."""
    if s["id"] in BUILTIN_IDS:
        return ["Built-in"]
    if s["name"].strip().lower() in JSM_NAMES and s.get("scope") != "PROJECT" and (
            not s["workflows"] or any("Jira Service Management" in w for w in s["workflows"])):
        return ["JSM"]
    return []


# Resolutions that every Jira site starts with (the classic five, and the ones a new site gets), recognised by their fixed IDs.
BUILTIN_RESOLUTION_IDS = {str(i) for i in range(1, 10)} | {"10000", "10001", "10002", "10003"}


def build_resolutions(raw: list, workflows: list) -> list:
    """The resolutions with how they are used: a workflow uses a resolution when one of its transitions sets it."""
    out = []
    for r in raw:
        rid = str(r.get("id", ""))
        wfs = sorted((w for w in workflows if any(rid in t["resolutions"] for t in w["transitions"])), key=lambda w: w["name"].lower())
        spaces = sorted({k for w in wfs for k in w["project_keys"]})
        tags = (["Built-in"] if rid in BUILTIN_RESOLUTION_IDS else []) + (["Default"] if r.get("isDefault") else [])
        out.append({"id": rid, "name": r.get("name", ""), "description": r.get("description", ""), "default": bool(r.get("isDefault")),
                    "tags": tags, "workflows": [w["name"] for w in wfs], "wf_spaces": {w["name"]: w["project_keys"] for w in wfs}, "spaces": spaces})
    return sorted(out, key=lambda r: (int(r["id"]) if r["id"].isdigit() else 10**9, r["name"].lower()))


def build_catalog(raw_workflows, raw_statuses, workflow_statuses, schemes, project_scheme, projects, screens=(), issue_types=(), resolutions=()) -> dict:
    """Combine everything into {'workflows': [...], 'statuses': [...], 'spaces': [...]}.

    workflow_statuses: statuses returned next to the workflows (they carry the UUID references);
    project_scheme:    {project id: workflow scheme id};  projects: raw project list.
    """
    spaces = {str(p["id"]): {"id": str(p["id"]), "key": p["key"], "name": p["name"]} for p in projects}
    statuses = {}
    for s in (normalize_status(r) for r in list(raw_statuses) + list(workflow_statuses)):
        if s["id"]:
            statuses.setdefault(s["id"], s)
            if s["ref"] and not statuses[s["id"]]["ref"]:
                statuses[s["id"]]["ref"] = s["ref"]
    by_ref = {s["ref"]: s for s in statuses.values() if s["ref"]}

    workflows = [normalize_workflow(r, statuses, by_ref) for r in raw_workflows]
    screen_names = {str(s["id"]): s.get("name", "") for s in screens}
    for wf in workflows:
        for t in wf["transitions"]:
            if t["screen_id"] and not t["screen_name"]:
                t["screen_name"] = screen_names.get(t["screen_id"], f"Screen {t['screen_id']}")
        wf["screens"] = sorted({(t["screen_id"], t["screen_name"]) for t in wf["transitions"] if t["screen_id"]},
                               key=lambda s: s[1].lower())

    scheme_by_id = {str(s["id"]): {"id": str(s["id"]), "name": s.get("name", ""),
                                   "default": s.get("defaultWorkflow") or DEFAULT_WORKFLOW,
                                   "mapped": set((s.get("issueTypeMappings") or {}).values())}
                    for s in schemes}
    projects_by_scheme = defaultdict(list)
    for pid, sid in project_scheme.items():
        projects_by_scheme[str(sid)].append(pid)

    for wf in workflows:
        used_schemes = [s for s in scheme_by_id.values() if wf["name"] == s["default"] or wf["name"] in s["mapped"]]
        pids = {p for s in used_schemes for p in projects_by_scheme.get(s["id"], [])}
        if wf["scope"] == "PROJECT" and wf["scope_project"]:
            pids.add(wf["scope_project"])
        if not used_schemes:  # legacy payloads list the schemes themselves
            used_schemes = [{"id": str(s.get("id")), "name": s.get("name", "")} for s in wf["legacy_schemes"]]
        wf["schemes"] = [{"id": s["id"], "name": s["name"]} for s in sorted(used_schemes, key=lambda s: s["name"].lower())]
        wf["project_keys"] = sorted(spaces[p]["key"] for p in pids if p in spaces)
        wf["project_ids"] = sorted(pids)
        wf["active"] = wf["is_active"] if isinstance(wf["is_active"], bool) else bool(pids)

    usage = defaultdict(list)
    for wf in workflows:
        for s in wf["statuses"]:
            usage[s["id"]].append(wf["name"])
            statuses.setdefault(s["id"], {**s, "ref": None, "description": "", "scope": "GLOBAL", "project_id": ""})
    ordered = sorted(statuses.values(), key=lambda s: (CATEGORIES[s["category"]][1], s["name"].lower()))
    wf_keys = {wf["name"]: wf["project_keys"] for wf in workflows}
    for i, s in enumerate(ordered, 1):
        s["order"] = i
        s["workflows"] = sorted(set(usage.get(s["id"], [])), key=str.lower)
        # the spaces that use the status: those of its workflows (a team-managed status also belongs to its own space)
        s["wf_spaces"] = {name: wf_keys.get(name, []) for name in s["workflows"]}
        own = spaces.get(s.get("project_id") or "", {}).get("key")
        s["spaces"] = sorted({k for ks in s["wf_spaces"].values() for k in ks} | ({own} if own else set()))
        s["tags"] = status_tags(s)
    resolution_list = build_resolutions(resolutions, workflows)
    type_names = {str(t["id"]): t.get("name", "") for t in issue_types}
    scheme_list = []
    for s in schemes:
        sid = str(s["id"])
        default = s.get("defaultWorkflow") or DEFAULT_WORKFLOW
        by_wf = defaultdict(list)
        for type_id, wf_name in (s.get("issueTypeMappings") or {}).items():
            by_wf[wf_name].append(type_names.get(str(type_id), f"Work type {type_id}"))
        rows = []
        for name in [default] + sorted((n for n in by_wf if n != default), key=str.lower):
            types = sorted(by_wf.get(name, []), key=str.lower)
            if name == default:  # the default workflow also covers every work type that has no mapping of its own
                types.append("All other work types")
            rows.append({"workflow": name, "work_types": types})
        keys = sorted(spaces[p]["key"] for p in projects_by_scheme.get(sid, []) if p in spaces)
        scheme_list.append({"id": sid, "name": s.get("name", ""), "description": s.get("description", ""), "default": default,
                            "rows": rows, "project_keys": keys, "active": bool(keys)})
    scheme_list.sort(key=lambda s: s["name"].lower())
    return {"workflows": workflows, "statuses": ordered, "schemes": scheme_list, "resolutions": resolution_list,
            "spaces": sorted(spaces.values(), key=lambda s: s["key"].lower())}
