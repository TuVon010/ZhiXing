"""Application services for mail perception commands and candidate review."""

from backend.app.modules.mail.repositories.perception import (
    move_candidate_scope,
    pending_perception_job,
)
from backend.app.modules.mail.repository import enqueue, require, rows


def request_analysis(db, message_id: str) -> dict:
    mail = require(db, message_id, "mail_message")
    require(db, mail["scope"], "mail_account")
    existing = pending_perception_job(db, mail["scope"], message_id)
    if existing:
        return {"job_id": existing}
    return {
        "job_id": enqueue(
            db,
            "perception",
            {"message_id": message_id, "manual": True},
            mail["scope"],
            priority=5,
        )
    }


def list_todo_candidates(db, account_id: str = "") -> list[dict]:
    from backend.app.modules.mail.perception import list_pending_todos

    if account_id:
        require(db, account_id, "mail_account")
        return list_pending_todos(db, account_id)
    result = []
    for account in rows(db, "mail_account", limit=100):
        result.extend(list_pending_todos(db, account["id"]))
    return result


def confirm_todo(db, item_id: str) -> dict:
    row = require(db, item_id, "todo")
    if row["status"] == "active" and row["body"].get("source") == "perception":
        return row
    if row["status"] != "candidate":
        raise ValueError("该待办不是候选状态")
    db.update(item_id, row["body"], "active")
    if not row["scope"].startswith("web:mail:"):
        require(db, row["scope"], "mail_account")
        move_candidate_scope(db, item_id, row["scope"])
    from backend.app.modules.mail.work_items import sync_todo_reminder

    sync_todo_reminder(db, item_id)
    return db.get(item_id)


def dismiss_candidate(db, item_id: str, kind: str) -> dict:
    row = require(db, item_id, kind)
    if row["status"] != "candidate":
        label = "待办" if kind == "todo" else "日程"
        raise ValueError(f"该{label}不是候选状态")
    db.update(item_id, row["body"], "dismissed")
    return db.get(item_id)
