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
    return {"items": [row for row in rows(db, "assistant_session") if row["status"] != "trashed"]}


def get_session(db, session_id: str) -> dict:
    turns = rows(db, "assistant_turn", [session_id])
    return {
        "session": require(db, session_id, "assistant_session"),
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
