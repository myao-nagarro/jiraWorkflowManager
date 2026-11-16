"""Audit log of every change the app makes on the Jira Cloud site (one JSON object per line)."""
import json
from datetime import datetime
from pathlib import Path

LOG_PATH = Path.home() / "JiraWorkflows" / "change_log.jsonl"


def log_change(site: str, user: str, action: str, target: str, target_id: str, result: str, details: str = ""):
    entry = {"time": datetime.now().astimezone().isoformat(timespec="seconds"), "site": site, "user": user,
             "action": action, "target": target, "target_id": str(target_id), "result": result, "details": details}
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # never let a full disk or a locked file break the operation being logged


def read_log() -> list:
    """All entries, newest first."""
    try:
        lines = LOG_PATH.read_text("utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]
