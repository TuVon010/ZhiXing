"""Mail-message persistence queries that require SQLite-specific expressions."""

import json
from datetime import datetime, time, timezone, timedelta

from sqlalchemy import text


def list_inbox(
    db,
    *,
    account_id: str | None,
    include_test: bool,
    view: str,
    category: str,
    sort: str,
    limit: int,
    offset: int,
) -> list[dict]:
    """Query inbox summaries with server-enforced scope, filters and ordering."""
    # A mailbox is a state projection.  Keeping this predicate strict prevents
    # archived/review messages from silently leaking back into the inbox.
    query = "SELECT * FROM records WHERE kind='mail_message' AND status='active'"
    params: dict = {"limit": limit, "offset": offset}
    if account_id:
        query += " AND scope=:account_id"
        params["account_id"] = account_id
    elif not include_test:
        query += " AND scope IN (SELECT id FROM records WHERE kind='mail_account' AND coalesce(json_extract(body,'$.test_account'),0)=0)"

    if view == "today":
        shanghai = timezone(timedelta(hours=8))
        start = datetime.combine(datetime.now(shanghai).date(), time.min, shanghai)
        query += " AND json_extract(body,'$.received_at')>=:start"
        params["start"] = start.astimezone(timezone.utc).isoformat()
    elif view == "unread":
        query += " AND json_extract(body,'$.read_at') IS NULL"
    elif view == "pending":
        query += " AND json_type(body,'$.perception') IS NULL"
    elif view == "actionable":
        query += """ AND (
            json_extract(body,'$.perception.priority')='high'
            OR json_extract(body,'$.perception.needs_reply')=1
            OR json_array_length(coalesce(json_extract(body,'$.perception.todos'),'[]'))>0
            OR json_array_length(coalesce(json_extract(body,'$.perception.calendar_events'),'[]'))>0
        )"""
    if category:
        query += " AND json_extract(body,'$.perception.category')=:category"
        params["category"] = category

    received = "coalesce(json_extract(body,'$.received_at'),created_at)"
    if sort == "latest":
        order = f"{received} DESC,id"
    elif sort == "oldest":
        order = f"{received} ASC,id"
    else:
        order = f"""(
            CASE WHEN json_extract(body,'$.perception.priority')='high' THEN 50 ELSE 0 END
            + CASE WHEN json_extract(body,'$.perception.needs_reply')=1 THEN 30 ELSE 0 END
            + CASE WHEN json_extract(body,'$.read_at') IS NULL THEN 5 ELSE 0 END
        ) DESC,{received} DESC,id"""

    with db.engine.connect() as connection:
        found = connection.execute(
            text(f"{query} ORDER BY {order} LIMIT :limit OFFSET :offset"), params
        ).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in found]


def list_mailbox(
    db,
    *,
    statuses: tuple[str, ...],
    account_id: str | None,
    include_test: bool,
    limit: int,
    offset: int,
) -> list[dict]:
    """List one logical mailbox while preserving account and test-data scope."""
    placeholders = ",".join(f":status_{index}" for index in range(len(statuses)))
    query = f"SELECT * FROM records WHERE kind='mail_message' AND status IN ({placeholders})"
    params: dict = {f"status_{index}": value for index, value in enumerate(statuses)}
    params.update({"limit": limit, "offset": offset})
    if account_id:
        query += " AND scope=:account_id"
        params["account_id"] = account_id
    elif not include_test:
        query += " AND scope IN (SELECT id FROM records WHERE kind='mail_account' AND coalesce(json_extract(body,'$.test_account'),0)=0)"
    received = "coalesce(json_extract(body,'$.received_at'),created_at)"
    query += f" ORDER BY {received} DESC,id LIMIT :limit OFFSET :offset"
    with db.engine.connect() as connection:
        found = connection.execute(text(query), params).mappings().all()
    return [{**dict(row), "body": json.loads(row["body"])} for row in found]


def move_chunks_to_thread(db, message_id: str, thread_id: str) -> None:
    """Keep retrieval chunks aligned with a user-corrected thread relation."""
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE mail_chunks SET thread_id=:thread_id WHERE message_id=:message_id"),
            {"thread_id": thread_id, "message_id": message_id},
        )
