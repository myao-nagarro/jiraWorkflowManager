#!/usr/bin/env python3
# The names below come from the library that the exporter puts in front of this file:
# pylint: disable=undefined-variable,pointless-statement
"""__TITLE__
__CONTACT__
Headless Workflow Manager job (no GUI). Generated __GENERATED__ by __APP__ v__VERSION__.

Every run works on the current data, not on a snapshot:
    1. signs in to Jira with the account given on the command line and reads the CURRENT workflows
    2. builds one Confluence page per workflow listed in JOB["workflows"], and per workflow of the spaces in JOB["spaces"], from that data, laid out as JOB["template"] says
    3. publishes the pages under JOB["destination"] (an existing page with the same title is overwritten)
Workflow diagrams are not created or updated by the job: a page that already has a diagram keeps it. A page without one gets a
placeholder in the diagram's place when JOB["diagram_placeholder"] is on; a person adds the picture there by editing the page.

Requirements:  Python 3.9+ and `pip install requests`. Nothing else.

Usage:   python __FILE__ -user you@company.com -token <API token> [--dry-run] [--log-file run.log]
         python __FILE__ -viewlog [last|all]          show the log of the last run (or the whole log file) and exit
    The script contains no credentials. The account needs permission to read workflows in Jira (Administer Jira)
    and to create / edit pages in the destination Confluence space.
    A command line is visible to other users of the server, so run it from a protected scheduled task or script.

Schedule it, for example
    cron (Linux):     0 6 * * 1-5  /usr/bin/python3 /opt/jobs/__FILE__ -user you@company.com -token ... >> /var/log/wfm.log 2>&1
    Task Scheduler:   schtasks /create /tn "Workflow docs" /sc daily /st 06:00 /tr "python C:\\jobs\\__FILE__ -user ... -token ..."

Log:     every run writes a detailed log (steps, timings, HTTP calls, errors with their traceback) to <this script>.log
    next to the script, or to --log-file. It rotates (5 files of 1 MB). The token is never written to it.
    When something fails, run it again and look at it with:  python __FILE__ -viewlog

Exit code: 0 all pages published, 1 at least one page failed, 2 the job could not start (credentials, destination, ...).
"""
import argparse
import json
import logging
import logging.handlers
import platform
import socket
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path

# ============================================================================================================
# THE JOB - what the script publishes. Generated from the Job page; edit only if you know what you are changing.
# ============================================================================================================
JOB = __JOB__

# ============================================================================================================
# LIBRARY - the Workflow Manager code this job uses, one section per module (Jira client, workflow model, Confluence client,
# page builder). Nothing here talks to a user interface. The job logic itself starts at "THE JOB LOGIC" below.
# ============================================================================================================
__LIBRARY__

# ============================================================================================================
# THE JOB LOGIC - read the current data from Jira, build the pages with the template, publish them to Confluence.
# ============================================================================================================
log = logging.getLogger("workflow-job")


RUN_MARK = "===== RUN STARTED"   # starts every run in the log file; -viewlog uses it to find the last run


def _log_path(option):
    return Path(option) if option else Path(__file__).resolve().with_suffix(".log")


def _setup_logging(path):
    """Console: the progress (INFO). Log file: everything (DEBUG), including the HTTP calls of requests / urllib3 and the traceback of
    every error. The file rotates, so it cannot grow without limit. Returns the log file, or None when it cannot be written."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s"))
    root.addHandler(console)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    except OSError as e:
        log.warning("The log file %s cannot be written (%s); logging to the screen only.", path, e)
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s"))
    root.addHandler(handler)
    logging.getLogger("urllib3").setLevel(logging.DEBUG)   # "GET /rest/api/3/... 200" lines: which call failed, and how
    return path


def _log_run_header(args, log_file):
    log.info("%s %s =====", RUN_MARK, uuid.uuid4().hex[:8])
    log.info("Job '%s' · script version %s · Python %s · %s · host %s", JOB["name"], JOB.get("app_version", "?"), platform.python_version(),
             platform.platform(), socket.gethostname())
    log.debug("Arguments: user=%s dry_run=%s log_file=%s (the token is never logged)", args.user, args.dry_run, log_file)
    log.debug("Script: %s · working directory: %s", Path(__file__).resolve(), Path.cwd())
    log.info("Log file: %s", log_file or "(none)")


def _view_log(path, which):
    """-viewlog: print the log of the last run (or the whole file) and nothing else."""
    if not path.exists():
        print(f"No log yet: {path} does not exist. Run the job first.")
        return 0
    text = path.read_text(encoding="utf-8", errors="replace")
    if which != "all" and RUN_MARK in text:
        start = text.rfind(RUN_MARK)
        text = text[text.rfind("\n", 0, start) + 1:]
    print(f"# {path} ({'last run' if which != 'all' else 'whole file'})")
    print(text.rstrip("\n"))
    return 0


def _fetch(jira):
    """The same read as the Workflows page: (catalog, raw workflows)."""
    log.info("Reading spaces, statuses, workflow schemes and workflows from %s", jira.site)
    projects, statuses, schemes = jira.projects(), jira.statuses(), jira.workflow_schemes()
    screens, work_types = jira.screens(), jira.issue_types()
    assigned = jira.project_schemes([p["id"] for p in projects])
    workflows, wf_statuses = jira.workflows()
    return build_catalog(workflows, statuses, wf_statuses, schemes, assigned, projects, screens, work_types), workflows


def _raw_json(raw_workflows, wf):
    for raw in raw_workflows:
        ident = raw.get("id")
        if (ident.get("entityId") if isinstance(ident, dict) else ident) == wf["id"]:
            return json.dumps(raw, indent=2, ensure_ascii=False)
    return json.dumps({k: v for k, v in wf.items() if k != "legacy_schemes"}, indent=2, ensure_ascii=False, default=str)


def _select(catalog, wanted, spaces=()):
    """The workflows of the job: the listed ones, found by ID (the name is the fallback when an ID changed), and every workflow that is
    used in one of the listed spaces (read now, so a workflow added to a space later is published too). Missing ones are reported."""
    by_id = {w["id"]: w for w in catalog["workflows"]}
    by_name = {w["name"].casefold(): w for w in catalog["workflows"]}
    found, seen = [], set()
    for w in wanted:
        wf = by_id.get(w["id"]) or by_name.get(w["name"].casefold())
        if wf is None:
            log.warning("Workflow '%s' (%s) no longer exists in Jira: skipped", w["name"], w["id"])
        elif wf["id"] not in seen:
            seen.add(wf["id"]); found.append(wf)
    missing = len(wanted) - len(found)
    known = {sp["key"] for sp in catalog.get("spaces", [])}
    for sp in spaces:
        if sp["key"] not in known:
            log.warning("Space '%s' (%s) no longer exists in Jira: skipped", sp["name"], sp["key"])
            missing += 1
            continue
        members = [w for w in catalog["workflows"] if sp["key"] in w.get("project_keys", [])]
        log.info("Space %s (%s): %d workflow(s)", sp["name"], sp["key"], len(members))
        for wf in members:
            if wf["id"] not in seen:
                seen.add(wf["id"]); found.append(wf)
    return found, missing


def _publish_one(wf, ctx):
    tpl, jira, conf, parent, dest = ctx["tpl"], ctx["jira"], ctx["conf"], ctx["parent"], JOB["destination"]
    title = page_title(wf["name"], dest.get("title_prefix", ""), dest.get("title_prefix_on", False))

    screens = {}
    if tpl.details and wf.get("screens"):
        try:
            if ctx["fields"] is None:
                ctx["fields"] = field_catalog(jira)
            screens = load_details(jira, wf["screens"], ctx["fields"], jira.site)
        except JiraError as e:
            log.warning("  transition screens of '%s' could not be read: %s", wf["name"], e)

    def body_for(existing):
        """The page body from the data just read. The diagram is not drawn: an existing page keeps the one it has."""
        assets, page_tpl = {}, tpl
        if tpl.flow:
            kept = conf.diagram_section(existing["id"]) if existing else ""
            if kept:
                assets["flow_html"] = kept
            elif JOB.get("diagram_placeholder"):   # the diagram keeps the place the template gives it; a person adds the picture
                assets["flow_html"] = diagram_placeholder()
            else:
                page_tpl = replace(tpl, flow=False).normalize()
        return build_storage(wf, _raw_json(ctx["raw"], wf), assets, page_tpl, screens, jira.site), page_tpl

    space, parent_id = parent["spaceId"], parent["id"]
    page = overwrote = None
    for candidate in (title, f"{title[:240]} ({wf['id'][-8:]})"):
        existing = conf.find_page(space, candidate)
        if existing and existing["id"] == parent_id:
            continue
        body, page_tpl = body_for(existing)
        if ctx["dry_run"]:
            log.info("  [dry run] would %s '%s' (%d characters)", "overwrite" if existing else "create", candidate, len(body))
            return None
        try:
            if existing:
                page, overwrote = conf.update_page(existing["id"], candidate, body, existing["version"], parent_id=parent_id), True
            else:
                page, overwrote = conf.create_page(space, parent_id, candidate, body), False
        except ConflictError:
            continue
        title = candidate
        break
    else:
        raise ConfluenceError("The page title is already in use and cannot be overwritten.")
    try:
        conf.full_width(page["id"], page_tpl.full_width)
    except ConfluenceError as e:
        log.warning("  page width not applied: %s", e)
    try:
        conf.add_labels(page["id"], ctx["labels"])
    except ConfluenceError as e:
        log.warning("  labels not added: %s", e)
    log.info("  %s '%s' -> %s", "overwrote" if overwrote else "created", title, page["url"])
    return page


def run(args):

    dest, tpl = JOB["destination"], Template.from_dict(JOB["template"])
    log.info("Job '%s' · %d workflow(s) + %d space(s) · site %s · user %s%s", JOB["name"], len(JOB["workflows"]), len(JOB.get("spaces", [])),
             JOB["site"], args.user, " · DRY RUN" if args.dry_run else "")
    try:
        jira = JiraClient(JOB["site"], args.user, args.token)
        jira.login()
        catalog, raw = _fetch(jira)
        base, kind, cid = parse_target(dest["url"])
        conf = ConfluenceClient(base, jira.session.auth)
        parent = conf.resolve_parent(kind, cid)
    except (JiraError, ConfluenceError) as e:
        log.error("Cannot start: %s", e)
        log.debug("details of the failure", exc_info=True)
        return 2
    log.info("Destination: %s '%s' in space %s", parent["kind"], parent["title"], parent["spaceKey"] or parent["spaceId"])

    flows, missing = _select(catalog, JOB["workflows"], JOB.get("spaces", []))
    if not flows and not missing:
        log.warning("Nothing to publish: the job lists no workflow and its spaces have none")
    labels = list(dict.fromkeys([REQUIRED_LABEL] + [clean_label(x) for x in dest.get("labels", []) if clean_label(x)]))
    ctx = {"jira": jira, "conf": conf, "parent": parent, "tpl": tpl, "raw": raw, "labels": labels, "fields": None, "dry_run": args.dry_run}
    ok, bad, started = 0, missing, time.perf_counter()
    log.debug("%d workflow(s) to publish, %d listed workflow(s) or space(s) no longer exist in Jira", len(flows), missing)
    for i, wf in enumerate(flows, 1):
        log.info("[%d/%d] %s", i, len(flows), wf["name"])
        step = time.perf_counter()
        try:
            _publish_one(wf, ctx)
            ok += 1
            log.debug("  '%s' took %.1f s", wf["name"], time.perf_counter() - step)
        except ConfluenceError as e:
            bad += 1; log.error("  failed: %s", e); log.debug("  details of the failure", exc_info=True)
        except Exception as e:  # one workflow must never stop the batch
            bad += 1; log.error("  failed: %s: %s (the traceback is in the log file)", type(e).__name__, e); log.debug("  details of the failure", exc_info=True)
    if ok and not args.dry_run and dest.get("parent_report") and parent["kind"] == "page":
        try:
            log.info("Content properties report on the parent page: %s", conf.add_report_to_parent(parent["id"]))
        except ConfluenceError as e:
            log.warning("Content properties report not added: %s", e)
    log.info("Finished in %.1f s: %d page(s) %s, %d failed or skipped", time.perf_counter() - started, ok,
             "checked" if args.dry_run else "published", bad)
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=JOB["name"] + " - publishes the configured workflows to Confluence.")
    ap.add_argument("-user", "--user", help="Jira / Confluence account (email); required to run the job")
    ap.add_argument("-token", "--token", help="Atlassian API token of that account; required to run the job")
    ap.add_argument("--dry-run", action="store_true", help="read Jira and build the pages, but change nothing in Confluence")
    ap.add_argument("--log-file", help="the log file (default: <this script>.log next to the script)")
    ap.add_argument("-viewlog", "--viewlog", nargs="?", const="last", choices=["last", "all"],
                    help="show the log of the last run (default) or the whole log file, then exit")
    args = ap.parse_args(argv)
    if args.viewlog:
        return _view_log(_log_path(args.log_file), args.viewlog)
    if not args.user or not args.token:
        ap.error("-user and -token are required to run the job (or use -viewlog to read the log)")
    log_file = _setup_logging(_log_path(args.log_file))
    _log_run_header(args, log_file)
    try:
        code = run(args)
    except KeyboardInterrupt:
        log.warning("Interrupted")
        return 130
    except Exception as e:   # anything unexpected is in the log file with its traceback
        log.critical("The job stopped unexpectedly: %s: %s", type(e).__name__, e, exc_info=True)
        return 2
    log.info("Exit code %d", code)
    return code


if __name__ == "__main__":
    sys.exit(main())
