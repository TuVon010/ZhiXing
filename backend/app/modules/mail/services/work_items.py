"""Application services for user-managed mail follow-ups, todos and reminders."""

from datetime import datetime, timezone, timedelta

from backend.app.modules.mail.repository import require, rows
from backend.app.persistence.store import now


def list_followups(db, account_id: str | None = None) -> dict:
    account_ids = [account_id] if account_id else [row["id"] for row in rows(db, "mail_account", limit=1000)]
    items = []
    for selected_id in account_ids:
        require(db, selected_id, "mail_account")
        for kind in ("todo", "reminder", "calendar", "mail_followup"):
            items.extend(
                row for row in db.list(kind, limit=200, scope=f"web:mail:{selected_id}")
                if row["status"] not in {"trashed", "deleted"}
            )
        for kind in ("todo", "calendar"):
            items.extend(
                row for row in rows(db, kind, [selected_id], limit=200)
                if row["body"].get("source") == "perception"
                and row["status"] not in {"trashed", "deleted"}
            )
    return {"items": sorted(items, key=lambda row: row["updated_at"], reverse=True)}


def update_followup(db, item_id: str, operation: str) -> dict:
    if operation not in {"resolve", "reopen"}:
        raise ValueError("无效跟进操作")
    row = require(db, item_id, "mail_followup")
    status = "resolved" if operation == "resolve" else "active"
    db.update(item_id, {**row["body"], "user_action_at": now()}, status)
    db.audit("user", f"MAIL_FOLLOWUP_{operation.upper()}", followup_id=item_id)
    return db.get(item_id)


def update_todo(db, item_id: str, operation: str) -> dict:
    if operation not in {"complete", "reopen"}:
        raise ValueError("无效待办操作")
    row = require(db, item_id, "todo")
    if not row["scope"].startswith("web:mail:") or row["status"] not in {"active", "completed"}:
        raise ValueError("仅能更改当前本地工作台的已确认待办")
    require(db, row["scope"].removeprefix("web:mail:"), "mail_account")
    status = "completed" if operation == "complete" else "active"
    db.update(item_id, {**row["body"], "user_action_at": now()}, status)
    from backend.app.modules.mail.work_items import sync_todo_reminder

    sync_todo_reminder(db, item_id)
    db.audit("user", f"MAIL_TODO_{operation.upper()}", todo_id=item_id)
    return db.get(item_id)


def create_todo(db, value) -> dict:
    require(db, value.account_id, "mail_account")
    item_id = db.insert(
        "todo",
        {
            "title": value.title,
            "owner": "self",
            "deadline": value.deadline.isoformat() if value.deadline else None,
            "origin": "mail_workspace",
        },
        scope=f"web:mail:{value.account_id}",
    )
    from backend.app.modules.mail.work_items import sync_todo_reminder

    sync_todo_reminder(db, item_id)
    db.audit("system", "MAIL_FOLLOWUP_CREATED", todo_id=item_id, account_id=value.account_id)
    return db.get(item_id)


def update_reminder(db, item_id: str, operation: str, minutes: int) -> dict:
    if operation not in {"complete", "dismiss", "snooze"}:
        raise ValueError("无效提醒操作")
    row = require(db, item_id, "reminder")
    if not row["scope"].startswith("web:mail:"):
        raise ValueError("不是当前邮件工作台提醒")
    if operation == "snooze":
        due = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        db.update(item_id, {**row["body"], "due_at": due.isoformat(), "snoozed_at": now()}, "active")
    else:
        status = "completed" if operation == "complete" else "dismissed"
        db.update(item_id, {**row["body"], "user_action_at": now()}, status)
    return db.get(item_id)
