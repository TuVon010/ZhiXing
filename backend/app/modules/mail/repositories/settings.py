"""Transactional persistence operations for settings and legacy recovery."""

from sqlalchemy import text


def save_filter_rules(db, account_id: str, rules: dict) -> dict:
    """Version filter changes under an immediate SQLite write transaction."""
    key = f"mail-filter:{account_id}"
    with db.engine.connect() as connection:
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        try:
            old = db.get(key, connection)
        except KeyError:
            old = None
        value = {**rules, "version": old["body"].get("version", 0) + 1 if old else 1}
        if old:
            db.update(key, value, conn=connection)
        else:
            db.insert("setting", value, id=key, conn=connection)
        connection.commit()
    return value


def resume_legacy_run(db, run_id: str, previous_status: str) -> None:
    """Restore a reviewed legacy workflow and its durable job atomically."""
    run_status = "waiting_approval" if previous_status == "waiting_approval" else "queued"
    job_status = "waiting" if previous_status == "waiting_approval" else "queued"
    with db.engine.begin() as connection:
        db.update(run_id, status=run_status, conn=connection)
        connection.execute(
            text("UPDATE jobs SET status=:status WHERE run_id=:run_id"),
            {"status": job_status, "run_id": run_id},
        )
