"""Persistence queries and atomic updates for mail-Agent sessions and drafts."""

import json

from sqlalchemy import text


def load_thread_messages(db, account_id: str, thread_id: str) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT * FROM records
                WHERE kind='mail_message' AND scope=:account_id
                  AND json_extract(body,'$.thread_id')=:thread_id
                  AND status IN ('active','archived','legacy')
                ORDER BY json_extract(body,'$.received_at'),id"""),
            {"account_id": account_id, "thread_id": thread_id},
        ).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def invalidate_draft_run(connection, run_id: str) -> None:
    connection.execute(
        text("""UPDATE records SET status='rejected'
            WHERE kind='approval' AND json_extract(body,'$.run_id')=:run_id
              AND status='pending'"""),
        {"run_id": run_id},
    )
    connection.execute(
        text("UPDATE jobs SET status='done' WHERE run_id=:run_id"), {"run_id": run_id}
    )
