"""Application facade for search, drafts and bounded Agent sessions."""

from backend.app.modules.mail.repository import enqueue, require, rows, validate_accounts
from backend.app.persistence.store import now


def request_search(db, body) -> dict:
    validate_accounts(db, body.account_ids)
    return {
        "job_id": enqueue(
            db,
            "search",
            body.model_dump(mode="json"),
            scope=f"search:{now()}",
            priority=0,
        )
    }


def request_reindex(db, account_id: str) -> dict:
    require(db, account_id, "mail_account")
    return {"job_id": enqueue(db, "reindex", {}, account_id, priority=50)}


def list_drafts(db, account_id: str | None = None) -> dict:
    return {
        "items": [
            row for row in rows(db, "mail_draft", [account_id] if account_id else None)
            if row["status"] != "trashed"
        ]
    }


def list_sessions(db) -> dict:
    visible = [row for row in rows(db, "assistant_session") if row["status"] != "trashed"]
    return {"items": sorted(visible, key=lambda row: row["body"].get("last_turn_at") or row["created_at"], reverse=True)}


def rename_session(db, session_id: str, title: str) -> dict:
    title = title.strip()
    if not title:
        raise ValueError("请输入会话名称")
    row = require(db, session_id, "assistant_session")
    if row["status"] == "trashed":
        raise ValueError("回收站中的会话不能重命名，请先恢复")
    body = {**row["body"], "title": title}
    db.update(session_id, body)
    db.audit(session_id, "MAIL_ASSISTANT_SESSION_RENAMED", session_id=session_id)
    return db.get(session_id)


def get_session(db, session_id: str) -> dict:
    session = require(db, session_id, "assistant_session")
    if session["status"] == "trashed":
        raise ValueError("会话在回收站中，请先恢复")
    turns = rows(db, "assistant_turn", [session_id])
    return {
        "session": session,
        "turns": list(reversed(turns)),
    }


def get_turn(db, turn_id: str) -> dict:
    from backend.app.observability.billing import summarize

    row = require(db, turn_id, "assistant_turn")
    calls = db.for_run("model_call", turn_id)
    return {
        "turn": row,
        "audit": db.for_run("audit", turn_id),
        "model_calls": calls,
        "billing": summarize(calls),
    }


def cancel_turn(db, turn_id: str) -> dict:
    require(db, turn_id, "assistant_turn")
    db.update(turn_id, status="cancelled")
    return {"ok": True}
