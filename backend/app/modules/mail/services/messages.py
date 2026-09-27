"""Application services for inbox queries and local message state changes."""

from backend.app.modules.mail.repositories.messages import list_inbox, list_mailbox, move_chunks_to_thread
from backend.app.modules.mail.repository import enqueue, require, rows
from backend.app.persistence.store import now


SUMMARY_EXCLUDED_FIELDS = {"raw_text", "headers", "attachments"}


def list_messages(
    db,
    *,
    account_id: str | None = None,
    status: str | None = None,
    sort: str = "smart",
    view: str = "all",
    category: str = "",
    include_test: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Return inbox summaries after validating all user-controlled query options."""
    if sort not in {"smart", "latest", "oldest"} or view not in {
        "all", "today", "unread", "actionable", "pending"
    }:
        raise ValueError("无效的邮件排序或筛选")
    if account_id:
        require(db, account_id, "mail_account")
    if status == "inbox":
        items = list_inbox(
            db,
            account_id=account_id,
            include_test=include_test,
            view=view,
            category=category,
            sort=sort,
            limit=limit,
            offset=offset,
        )
    elif status in {"archived", "trashed", "filtered", "review", "filterbox"}:
        statuses = ("filtered", "review") if status == "filterbox" else (status,)
        items = list_mailbox(
            db,
            statuses=statuses,
            account_id=account_id,
            include_test=include_test,
            limit=limit,
            offset=offset,
        )
    else:
        items = rows(db, "mail_message", [account_id] if account_id else None, status, limit, offset)
    summaries = [
        {
            **row,
            "body": {
                key: value for key, value in row["body"].items()
                if key not in SUMMARY_EXCLUDED_FIELDS
            },
        }
        for row in items
    ]
    return {"items": summaries, "limit": limit, "offset": offset}


def set_read_state(db, message_id: str, read: bool) -> dict:
    row = require(db, message_id, "mail_message")
    body = {**row["body"]}
    if read:
        body["read_at"] = now()
    else:
        body.pop("read_at", None)
    db.update(message_id, body, row["status"])
    return {"id": message_id, "read": read}


def change_local_state(db, message_id: str, status: str) -> dict:
    row = require(db, message_id, "mail_message")
    if status not in {"active", "archived", "filtered", "review"}:
        raise ValueError("无效本地状态")
    db.update(message_id, status=status)
    if status in {"active", "archived"}:
        enqueue(db, "index", {"message_id": message_id}, row["scope"], priority=30)
    db.audit("system", "MAIL_LOCAL_STATE", message_id=message_id, old=row["status"], new=status)
    return db.get(message_id)


def attach_to_thread(db, message_id: str, target_thread_id: str) -> dict:
    row = require(db, message_id, "mail_message")
    require(db, target_thread_id, "mail_thread", [row["scope"]])
    db.update(message_id, {**row["body"], "thread_id": target_thread_id})
    move_chunks_to_thread(db, message_id, target_thread_id)
    db.audit("system", "MAIL_THREAD_LINK", message_id=message_id, target=target_thread_id)
    return db.get(message_id)
