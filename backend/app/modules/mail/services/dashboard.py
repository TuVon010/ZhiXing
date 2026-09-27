"""Application service for the action-oriented mail home dashboard."""

from datetime import datetime, timezone, timedelta

from backend.app.modules.mail.repositories.dashboard import (
    load_account_runtime,
    load_dashboard_rows,
)
from backend.app.modules.mail.repository import public_account, require, rows


def _empty_home() -> dict:
    return {
        "stats": {"new": 0, "important": 0, "reply": 0, "pending_analysis": 0, "overdue": 0},
        "important": [],
        "focus": [],
        "todos": [],
        "calendar": [],
        "today_calendar": [],
        "upcoming_calendar": [],
        "replies": [],
        "reminders": [],
        "review": [],
        "activity": [],
        "accounts": [],
        "account_status": [],
    }


def _attention_score(row: dict) -> int:
    perception = row["body"].get("perception") or {}
    return (
        (50 if perception.get("priority") == "high" else 0)
        + (30 if perception.get("needs_reply") else 0)
        + (20 if perception.get("todos") else 0)
        + (15 if perception.get("calendar_events") else 0)
        + (5 if not row["body"].get("read_at") else 0)
    )


def _compact(row: dict) -> dict:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "scope": row["scope"],
        "body": row["body"],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def build_home(db, account_id: str | None = None, include_test: bool = False) -> dict:
    """Build one stable home read model from repository data and domain rules."""
    accounts = rows(db, "mail_account", limit=1000)
    if account_id:
        require(db, account_id, "mail_account")
        account_ids = [account_id]
    else:
        account_ids = [
            row["id"] for row in accounts
            if include_test or not row["body"].get("test_account")
        ]
    if not account_ids:
        return _empty_home()

    shanghai = timezone(timedelta(hours=8))
    current = datetime.now(shanghai)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = start + timedelta(days=1)
    week_end = start + timedelta(days=8)
    data = load_dashboard_rows(
        db,
        account_ids,
        start.astimezone(timezone.utc).isoformat(),
        (start - timedelta(days=7)).astimezone(timezone.utc).isoformat(),
    )
    mails = data["recent_mail"]
    today_mails = data["today_mail"]
    work = data["work_items"]

    important = sorted(
        (
            mail for mail in mails
            if _attention_score(mail) >= 20 and not mail["body"].get("resolved_at")
        ),
        key=lambda mail: (_attention_score(mail), mail["body"].get("received_at", "")),
        reverse=True,
    )[:6]

    def parsed(value):
        try:
            return datetime.fromisoformat(str(value)).astimezone(shanghai)
        except (TypeError, ValueError):
            return None

    calendar = []
    for row in work:
        if row["kind"] != "calendar" or row["status"] != "active":
            continue
        event_start = parsed(row["body"].get("start"))
        event_end = parsed(row["body"].get("end")) or event_start
        if event_start and event_start < week_end and event_end and event_end >= start:
            calendar.append(
                _compact(row)
                | {
                    "start_local": event_start.isoformat(),
                    "is_today": event_start < tomorrow and event_end >= start,
                    "is_next": event_end >= current,
                }
            )
    calendar.sort(key=lambda row: row["start_local"])
    next_event = next(
        (
            row for row in calendar
            if (parsed(row["body"].get("end")) or parsed(row["body"].get("start"))) >= current
        ),
        None,
    )

    active_todos = [row for row in work if row["kind"] == "todo" and row["status"] == "active"]
    replies = [
        row for row in work
        if row["kind"] == "mail_followup" and row["status"] in {"active", "waiting"}
    ]
    focus = []
    overdue = 0
    for row in active_todos:
        due = parsed(row["body"].get("deadline"))
        urgency, rank = "later", 40
        if due and due < current:
            urgency, rank, overdue = "overdue", 0, overdue + 1
        elif due and due < tomorrow:
            urgency, rank = "today", 10
        elif due and due < week_end:
            urgency, rank = "week", 30
        focus.append(
            _compact(row)
            | {"urgency": urgency, "sort_key": (rank, due.isoformat() if due else "9999")}
        )
    for row in replies:
        waiting = row["status"] == "waiting"
        focus.append(
            _compact(row)
            | {
                "urgency": "waiting" if waiting else "reply",
                "sort_key": (35 if waiting else 20, row["created_at"]),
            }
        )
    focus.sort(key=lambda row: row["sort_key"])
    focus = [{key: value for key, value in row.items() if key != "sort_key"} for row in focus[:8]]

    analyzed_today = [mail for mail in today_mails if mail["body"].get("perception")]

    def relevant_candidate(row):
        if row["status"] != "candidate":
            return False
        field = "start" if row["kind"] == "calendar" else "deadline"
        value = parsed(row["body"].get(field))
        return not value or value >= current

    activity = []
    for mail in analyzed_today[:5]:
        perception = mail["body"].get("perception") or {}
        activity.append(
            {
                "kind": "analysis",
                "message_id": mail["id"],
                "title": mail["body"].get("subject") or "无主题",
                "summary": perception.get("summary"),
                "outcomes": {
                    "todos": len(perception.get("todos") or []),
                    "calendar": len(perception.get("calendar_events") or []),
                    "needs_reply": bool(perception.get("needs_reply")),
                },
            }
        )

    account_status = []
    for selected_id in account_ids:
        account = next(row for row in accounts if row["id"] == selected_id)
        try:
            cursor = require(db, f"mail-cursor:{selected_id}:INBOX", "mail_cursor")["body"]
        except KeyError:
            cursor = None
        runtime = load_account_runtime(db, selected_id)
        account_status.append(
            {
                "id": selected_id,
                "name": account["body"].get("name"),
                "enabled": bool(account["body"].get("enabled")),
                "auto_analyze": bool(account["body"].get("auto_analyze")),
                "poll_interval_seconds": account["body"].get("poll_interval_seconds", 15),
                "last_checked_at": cursor.get("last_poll") if cursor else None,
                **runtime,
                "attention": bool(cursor and cursor.get("paused")),
                "last_job": account["body"].get("last_job"),
                "last_error": account["body"].get("last_error"),
            }
        )

    today_active = [mail for mail in today_mails if mail["status"] in {"active", "review", "archived"}]
    return {
        "date": start.date().isoformat(),
        "generated_at": current.isoformat(),
        "brief": {
            "headline": f"今天有 {len(focus)} 项需要关注",
            "detail": f"{len(active_todos)} 项待办，{len(replies)} 项邮件跟进，未来七天 {len(calendar)} 个日程。",
            "next_event": next_event,
        },
        "stats": {
            "new": len(today_mails),
            "important": len([mail for mail in today_active if _attention_score(mail) >= 30]),
            "reply": len([mail for mail in today_active if (mail["body"].get("perception") or {}).get("needs_reply")]),
            "pending_analysis": len([mail for mail in today_active if not mail["body"].get("perception")]),
            "overdue": overdue,
            "filtered": len([mail for mail in today_mails if mail["status"] == "filtered"]),
            "analyzed": len(analyzed_today),
        },
        "important": [_compact(mail) | {"attention_score": _attention_score(mail)} for mail in important],
        "focus": focus,
        "todos": [_compact(row) for row in work if row["kind"] == "todo" and row["status"] in {"active", "completed"}],
        "calendar": calendar,
        "today_calendar": [row for row in calendar if row["is_today"]],
        "upcoming_calendar": [row for row in calendar if not row["is_today"]],
        "replies": [_compact(row) for row in replies],
        "reminders": [_compact(row) for row in work if row["kind"] == "reminder" and row["status"] == "active"],
        "review": [_compact(row) for row in work if relevant_candidate(row)],
        "activity": activity,
        "account_status": account_status,
        "accounts": [public_account(row) for row in accounts if row["id"] in account_ids],
    }
