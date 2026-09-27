"""SQLite query repository for the local record-management workspace."""

import json

from sqlalchemy import text


def overview_counts(db) -> dict:
    with db.engine.connect() as connection:
        counts = {
            kind: count
            for kind, count in connection.execute(
                text("SELECT kind,COUNT(*) FROM records WHERE status!='trashed' GROUP BY kind")
            )
        }
        old_messages = connection.execute(
            text("""SELECT COUNT(*) FROM records WHERE kind='message'
                AND json_extract(body,'$.source') IN ('email','demo')""")
        ).scalar_one()
        old_runs = connection.execute(
            text("""SELECT COUNT(*) FROM records WHERE kind='run'
                AND json_extract(body,'$.message.source') IN ('email','demo')""")
        ).scalar_one()
        old_pending = connection.execute(
            text("""SELECT COUNT(*) FROM records WHERE kind='run'
                AND status IN ('queued','running','waiting_approval')
                AND json_extract(body,'$.message.source') IN ('email','demo')""")
        ).scalar_one()
        current_mail = connection.execute(
            text("SELECT COUNT(*) FROM records WHERE kind='mail_message' AND status!='trashed'")
        ).scalar_one()
        trash = connection.execute(
            text("SELECT COUNT(*) FROM records WHERE status='trashed'")
        ).scalar_one()
    return {
        "current_mail_messages": current_mail,
        "legacy_messages": old_messages,
        "legacy_runs": old_runs,
        "legacy_pending_runs": old_pending,
        "trash": trash,
        "counts": counts,
    }


def list_rows(db, kind: str, account_id: str, trashed: bool, limit: int, offset: int) -> tuple[int, list[dict]]:
    if kind == "legacy_message":
        clause = "kind='message' AND json_extract(body,'$.source') IN ('email','demo')"
        params: dict = {}
    else:
        clause = "kind=:kind"
        params = {"kind": kind}
        if kind in {"todo", "calendar", "reminder", "notification"}:
            clause += " AND scope LIKE 'web:mail:%'"
        elif kind == "run":
            clause += " AND json_array_length(json_extract(body,'$.message.metadata.mail_accounts'))>0"
        elif kind == "memory":
            clause += " AND (scope='global' OR scope IN (SELECT id FROM records WHERE kind='mail_account'))"
        if account_id:
            if kind in {"todo", "calendar", "reminder", "notification"}:
                clause += " AND scope IN (:account,:workspace)"
                params.update(account=account_id, workspace=f"web:mail:{account_id}")
            elif kind == "assistant_session":
                clause += " AND EXISTS (SELECT 1 FROM json_each(json_extract(body,'$.account_ids')) WHERE value=:account)"
                params["account"] = account_id
            elif kind in {"run", "assistant_turn"}:
                clause += " AND EXISTS (SELECT 1 FROM json_each(CASE WHEN kind='run' THEN json_extract(body,'$.message.metadata.mail_accounts') ELSE json_extract(body,'$.account_ids') END) WHERE value=:account)"
                params["account"] = account_id
            elif kind == "memory":
                clause += " AND scope IN (:account,:global_scope)"
                params.update(account=account_id, global_scope="global")
            else:
                clause += " AND scope=:account"
                params["account"] = account_id
    if kind != "legacy_message":
        if kind in {"todo", "calendar", "reminder"}:
            clause += " AND status IN ('trashed','deleted')" if trashed else " AND status NOT IN ('trashed','deleted')"
        else:
            clause += " AND status='trashed'" if trashed else " AND status!='trashed'"
    params.update(limit=limit, offset=offset)
    with db.engine.connect() as connection:
        total = connection.execute(
            text(f"SELECT COUNT(*) FROM records WHERE {clause}"), params
        ).scalar_one()
        rows = connection.execute(
            text(f"""SELECT id,kind,status,scope,body,created_at FROM records
                WHERE {clause} ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset"""),
            params,
        ).mappings().all()
    return total, [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def has_active_turn(db, session_id: str) -> bool:
    with db.engine.connect() as connection:
        row = connection.execute(
            text("""SELECT 1 FROM records WHERE kind='assistant_turn' AND scope=:session_id
                AND status IN ('queued','running') LIMIT 1"""),
            {"session_id": session_id},
        ).first()
    return row is not None


def cancel_linked_reminders(db, scope: str, source_field: str, item_id: str) -> None:
    with db.engine.begin() as connection:
        connection.execute(
            text("""UPDATE records SET status='cancelled'
                WHERE kind='reminder' AND scope=:scope AND status='active'
                  AND json_extract(body,:path)=:item_id"""),
            {"scope": scope, "path": f"$.{source_field}", "item_id": item_id},
        )
