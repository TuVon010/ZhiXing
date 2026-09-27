"""Persistence helpers for perception queue coordination and candidate migration."""

from sqlalchemy import text


def pending_perception_job(db, account_id: str, message_id: str) -> str | None:
    """Return an existing queued/running perception job for idempotent requests."""
    with db.engine.connect() as connection:
        row = connection.execute(
            text(
                """SELECT id FROM mail_jobs
                WHERE kind='perception' AND account_id=:account_id
                  AND json_extract(payload,'$.message_id')=:message_id
                  AND status IN ('queued','running')
                ORDER BY created_at DESC LIMIT 1"""
            ),
            {"account_id": account_id, "message_id": message_id},
        ).first()
    return row[0] if row else None


def move_candidate_scope(db, item_id: str, account_id: str) -> None:
    """Migrate an old account-scoped candidate into the mail workspace scope."""
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE records SET scope=:scope WHERE id=:item_id"),
            {"scope": f"web:mail:{account_id}", "item_id": item_id},
        )
