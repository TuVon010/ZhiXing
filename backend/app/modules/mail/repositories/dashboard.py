"""SQL read model for the mail home dashboard.

The dashboard is intentionally a query repository: it joins several aggregates
for one screen without leaking SQLite JSON expressions into the HTTP layer.
"""

import json

from sqlalchemy import text


def _records(rows):
    return [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def load_dashboard_rows(db, account_ids: list[str], start: str, recent: str) -> dict:
    """Load the bounded data set used to assemble the home read model."""
    account_slots = ",".join(f":account_{index}" for index in range(len(account_ids)))
    params = {f"account_{index}": value for index, value in enumerate(account_ids)}
    params.update(start=start, recent=recent)

    work_slots: list[str] = []
    work_params: dict[str, str] = {}
    for index, account_id in enumerate(account_ids):
        work_slots.extend((f":work_{index}", f":candidate_{index}"))
        work_params[f"work_{index}"] = f"web:mail:{account_id}"
        work_params[f"candidate_{index}"] = account_id

    with db.engine.connect() as connection:
        recent_mail = connection.execute(
            text(
                f"""SELECT * FROM records
                WHERE kind='mail_message' AND scope IN ({account_slots})
                  AND status IN ('active','review','archived')
                  AND json_extract(body,'$.received_at')>=:recent
                ORDER BY json_extract(body,'$.received_at') DESC"""
            ),
            params,
        ).mappings().all()
        today_mail = connection.execute(
            text(
                f"""SELECT id,status,body FROM records
                WHERE kind='mail_message' AND scope IN ({account_slots})
                  AND json_extract(body,'$.received_at')>=:start
                ORDER BY json_extract(body,'$.received_at') DESC"""
            ),
            params,
        ).mappings().all()
        work_items = connection.execute(
            text(
                f"""SELECT * FROM records
                WHERE scope IN ({','.join(work_slots)})
                  AND kind IN ('todo','calendar','reminder','mail_followup')
                  AND status NOT IN ('trashed','deleted','dismissed','cancelled')
                ORDER BY updated_at DESC"""
            ),
            work_params,
        ).mappings().all()

    return {
        "recent_mail": _records(recent_mail),
        "today_mail": _records(today_mail),
        "work_items": _records(work_items),
    }


def load_account_runtime(db, account_id: str) -> dict:
    """Return queue depth and latest received message for one account."""
    with db.engine.connect() as connection:
        pending_jobs = connection.execute(
            text(
                """SELECT COUNT(*) FROM mail_jobs
                WHERE account_id=:account_id AND status IN ('queued','running')"""
            ),
            {"account_id": account_id},
        ).scalar_one()
        latest_message = connection.execute(
            text(
                """SELECT json_extract(body,'$.received_at') FROM records
                WHERE kind='mail_message' AND scope=:account_id
                ORDER BY json_extract(body,'$.received_at') DESC LIMIT 1"""
            ),
            {"account_id": account_id},
        ).scalar()
    return {"pending_jobs": pending_jobs, "last_message_at": latest_message}


def load_job_counts(db, account_id: str) -> list[dict]:
    """Aggregate background jobs for the account status endpoint."""
    with db.engine.connect() as connection:
        rows = connection.execute(
            text(
                """SELECT kind,status,COUNT(*) AS count FROM mail_jobs
                WHERE account_id=:account_id GROUP BY kind,status"""
            ),
            {"account_id": account_id},
        ).mappings().all()
    return [dict(row) for row in rows]
