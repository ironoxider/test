"""To-do items from Todoist on the help desk board.

Uses Todoist's API (v1) with a personal API token (Todoist → Settings → Integrations → Developer).
The board refreshes every few seconds, so the list is fetched at most once a minute and kept in
memory; ticking an item on the board completes it in Todoist straight away.
"""

import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

from flask import current_app

import app_settings

API = "https://api.todoist.com/api/v1"
DEFAULTS = {"todoist_enabled": False, "todoist_project": "", "todoist_show": "today",
            "todoist_filter": "", "todoist_limit": 50}
SHOW_CHOICES = {"today": "Due today, overdue, or with no date",
                "due": "Due today or overdue only",
                "all": "Everything",
                "filter": "A Todoist filter"}
REFRESH_SECONDS = 60
ERROR_RETRY_SECONDS = 120
TIMEOUT = 8

_cache = {"key": None, "at": 0.0, "tasks": [], "error": None, "projects": {}}
_lock = threading.Lock()


class TodoistError(Exception):
    pass


def config():
    data = app_settings.load(current_app.config["SETTINGS_PATH"])
    return {k: data.get(k, v) for k, v in DEFAULTS.items()} | {"token": data.get("todoist_token") or ""}


def _request(token, method, path, params=None):
    url = f"{API}{path}" + (f"?{urllib.parse.urlencode(params)}" if params else "")
    req = urllib.request.Request(url, method=method, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise TodoistError("Todoist didn't accept the API token (check it in Help Desk → To-do list).")
        if e.code == 404:
            raise TodoistError("Todoist couldn't find that (was the project or task deleted?).")
        if e.code == 400:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise TodoistError(f"Todoist didn't understand the request{': ' + detail if detail else ''}.")
        raise TodoistError(f"Todoist answered with error {e.code}.")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TodoistError(f"Couldn't reach Todoist ({getattr(e, 'reason', e)}).")
    return json.loads(body) if body.strip() else None


def _all_pages(token, path, params=None, most=400):
    """Lists in API v1 come in pages: {"results": [...], "next_cursor": ...}."""
    params, items = dict(params or {}), []
    while True:
        data = _request(token, "GET", path, params | {"limit": 200})
        if isinstance(data, list):  # tolerate a plain list
            return items + data
        items += data.get("results") or []
        cursor = data.get("next_cursor")
        if not cursor or len(items) >= most:
            return items
        params["cursor"] = cursor


def projects(token):
    return [{"id": str(p["id"]), "name": p.get("name") or "(no name)"}
            for p in _all_pages(token, "/projects") if not p.get("is_archived")]


def fetch(cfg):
    """The tasks to show, sorted, for the settings in `cfg` (raises TodoistError)."""
    token, show, project = cfg["token"], cfg["todoist_show"], str(cfg["todoist_project"] or "")
    if show == "filter" and (cfg["todoist_filter"] or "").strip():
        raw = _all_pages(token, "/tasks/filter", {"query": cfg["todoist_filter"].strip()})
    elif show == "due":
        raw = _all_pages(token, "/tasks/filter", {"query": "today | overdue"})
    else:
        raw = _all_pages(token, "/tasks", {"project_id": project} if project else None)
    if project and show != "filter":
        raw = [t for t in raw if str(t.get("project_id")) == project]
    today = date.today()
    tasks = [simplify(t, today) for t in raw if not t.get("checked") and not t.get("is_deleted")]
    if show == "today":
        tasks = [t for t in tasks if t["due"] is None or t["due"] <= today]
    tasks.sort(key=lambda t: (t["due"] is None, t["due"] or today, -t["priority"], t["order"]))
    return tasks


def simplify(task, today):
    due = None
    when = (task.get("due") or {}).get("date") or ""
    try:
        due = date.fromisoformat(when[:10]) if when else None
    except ValueError:
        pass
    time_text = ""
    if "T" in when:
        try:
            moment = datetime.fromisoformat(when.replace("Z", "+00:00"))
            moment = moment.astimezone() if moment.tzinfo else moment  # a fixed-time task is in UTC
            time_text = moment.strftime("%I:%M %p").lstrip("0").lower()
        except ValueError:
            pass
    return {"id": str(task.get("id")), "content": plain(task.get("content") or ""),
            "priority": int(task.get("priority") or 1), "due": due, "time": time_text,
            "due_text": due_text(due, today), "overdue": bool(due and due < today),
            "recurring": bool((task.get("due") or {}).get("is_recurring")),
            "project_id": str(task.get("project_id") or ""), "labels": task.get("labels") or [],
            "order": task.get("child_order") or task.get("order") or 0,
            "url": f"https://app.todoist.com/app/task/{task.get('id')}"}


def plain(text):
    """Todoist content is Markdown: show [text](link) as text and drop ** and __."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    return re.sub(r"(\*\*|__)", "", text).strip()


def due_text(due, today):
    if not due:
        return ""
    days = (due - today).days
    if days < -1:
        return f"{-days} days overdue"
    return {-1: "Yesterday", 0: "Today", 1: "Tomorrow"}.get(days) or f"{due:%a %b} {due.day}"


def board_tasks():
    """What the board shows: {"enabled", "tasks", "more", "error"}; fetched at most once a minute."""
    cfg = config()
    if not cfg["todoist_enabled"] or not cfg["token"]:
        return {"enabled": False, "tasks": [], "more": 0, "error": None}
    key = (cfg["token"], cfg["todoist_show"], str(cfg["todoist_project"]), cfg["todoist_filter"])
    with _lock:
        age = time.time() - _cache["at"]
        stale = _cache["key"] != key or age > (ERROR_RETRY_SECONDS if _cache["error"] else REFRESH_SECONDS)
        if stale:
            try:
                tasks, error = fetch(cfg), None
            except TodoistError as e:
                tasks, error = (_cache["tasks"] if _cache["key"] == key else []), str(e)
            _cache.update(key=key, at=time.time(), tasks=tasks, error=error)
        tasks, error = list(_cache["tasks"]), _cache["error"]
    limit = max(1, int(cfg["todoist_limit"] or 50))
    return {"enabled": True, "tasks": tasks[:limit], "more": max(0, len(tasks) - limit), "error": error}


def complete(task_id):
    cfg = config()
    if not cfg["token"]:
        raise TodoistError("Todoist isn't set up.")
    if not re.fullmatch(r"[\w-]{1,64}", task_id or ""):
        raise TodoistError("That isn't a Todoist task.")
    _request(cfg["token"], "POST", f"/tasks/{task_id}/close")
    with _lock:
        _cache["tasks"] = [t for t in _cache["tasks"] if t["id"] != task_id]


def forget():
    """Drop the cached list (settings changed)."""
    with _lock:
        _cache.update(key=None, at=0.0, tasks=[], error=None)
