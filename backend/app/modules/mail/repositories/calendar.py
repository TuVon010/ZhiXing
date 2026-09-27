"""SQLite queries for calendar conflict detection and perception candidates."""

import json

from sqlalchemy import text


def calendar_rows_for_conflicts(db, account_id: str) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT id,body FROM records
                WHERE kind='calendar' AND scope IN (:account_id,:workspace)
                  AND status IN ('active','candidate')
                  AND json_extract(body,'$.start') IS NOT NULL"""),
            {"account_id": account_id, "workspace": f"web:mail:{account_id}"},
        ).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def active_sourced_calendars(db, account_id: str) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT id,body FROM records
                WHERE kind='calendar' AND status='active' AND scope=:scope
                  AND json_extract(body,'$.source_message') IS NOT NULL"""),
            {"scope": f"web:mail:{account_id}"},
        ).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def perception_candidates(db, account_id: str) -> list[dict]:
    with db.engine.connect() as connection:
        rows = connection.execute(
            text("""SELECT id,body,created_at FROM records
                WHERE kind='calendar' AND status='candidate'
                  AND scope IN (:account_id,:workspace)
                  AND json_extract(body,'$.source')='perception'
                ORDER BY json_extract(body,'$.start') ASC LIMIT 50"""),
            {"account_id": account_id, "workspace": f"web:mail:{account_id}"},
        ).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in rows]


def move_to_workspace_scope(db, calendar_id: str, account_id: str) -> None:
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE records SET scope=:scope WHERE id=:calendar_id"),
            {"scope": f"web:mail:{account_id}", "calendar_id": calendar_id},
        )
