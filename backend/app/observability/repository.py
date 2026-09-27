"""Read-only SQL projections for live refresh, billing and operational stats."""

import json

from sqlalchemy import text


def change_revision(db) -> str:
    """Build a cheap revision signature for the server-sent event stream."""
    with db.engine.connect() as connection:
        records = connection.execute(
            text(
                """SELECT max(updated_at),count(*) FROM records WHERE kind IN
                ('run','assistant_turn','mail_message','todo','calendar','reminder',
                 'mail_followup','notification','mail_draft')"""
            )
        ).first()
        jobs = connection.execute(
            text("SELECT max(updated_at),count(*) FROM mail_jobs")
        ).first()
    return json.dumps([list(records), list(jobs)])


def record_counts(db) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT kind,status,count(*) AS count FROM records GROUP BY kind,status")
        ).mappings().all()
    return [dict(row) for row in rows]


def model_call_rows(db):
    """Yield decoded model-call rows without exposing SQL to pricing routes."""
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("SELECT body,status FROM records WHERE kind='model_call'")
        ).mappings().all()
    return [
        {"body": json.loads(row["body"]), "status": row["status"]}
        for row in rows
    ]


def activity_rows(db, account_id: str | None, limit: int, offset: int) -> tuple[int, list[dict]]:
    condition = "kind IN ('run','assistant_turn') AND coalesce(json_extract(body,'$.ui_hidden'),0)=0"
    if account_id:
        condition += " AND EXISTS (SELECT 1 FROM json_each(CASE WHEN kind='run' THEN json_extract(body,'$.message.metadata.mail_accounts') ELSE json_extract(body,'$.account_ids') END) WHERE value=:account)"
    else:
        condition += " AND (kind='assistant_turn' OR json_array_length(json_extract(body,'$.message.metadata.mail_accounts'))>0)"
    params = {"account": account_id, "limit": limit, "offset": offset}
    with db.engine.connect() as connection:
        total = connection.execute(
            text(f"SELECT COUNT(*) FROM records WHERE {condition}"), params
        ).scalar_one()
        rows = connection.execute(
            text(f"SELECT * FROM records WHERE {condition} ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset"),
            params,
        ).mappings().all()
    return total, [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def jobs_for_message(db, message_id: str) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT id,kind,status,result,attempts,created_at,updated_at
                FROM mail_jobs WHERE json_extract(payload,'$.message_id')=:message_id
                ORDER BY created_at,id"""),
            {"message_id": message_id},
        ).mappings().all()
    return [
        {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}
        for row in rows
    ]


def run_ids_for_turn(db, turn_id: str) -> list[str]:
    with db.engine.connect() as connection:
        return list(
            connection.execute(
                text("""SELECT id FROM records WHERE kind='run'
                    AND json_extract(body,'$.message.metadata.assistant_turn')=:turn_id
                    ORDER BY created_at,id"""),
                {"turn_id": turn_id},
            ).scalars().all()
        )


def notification_rows(db, account_id: str | None, limit: int, offset: int) -> dict:
    condition = "n.kind='notification' AND n.status!='trashed'"
    if account_id:
        condition += " AND (n.scope=:scope OR EXISTS (SELECT 1 FROM records r,json_each(json_extract(r.body,'$.message.metadata.mail_accounts')) account WHERE r.id=json_extract(n.body,'$.run_id') AND account.value=:account))"
    else:
        condition += " AND n.scope LIKE 'web:mail:%'"
    params = {
        "scope": f"web:mail:{account_id}",
        "account": account_id,
        "limit": limit,
        "offset": offset,
    }
    with db.engine.connect() as connection:
        total = connection.execute(
            text(f"SELECT COUNT(*) FROM records n WHERE {condition}"), params
        ).scalar_one()
        unread = connection.execute(
            text(f"SELECT COUNT(*) FROM records n WHERE {condition} AND n.status IN ('pending','local')"),
            params,
        ).scalar_one()
        rows = connection.execute(
            text(f"SELECT n.* FROM records n WHERE {condition} ORDER BY n.created_at DESC,n.id LIMIT :limit OFFSET :offset"),
            params,
        ).mappings().all()
    return {
        "items": [{**dict(row), "body": json.loads(row["body"])} for row in rows],
        "total": total,
        "unread": unread,
    }
