"""Durable workflow persistence operations requiring atomic SQL updates."""

from sqlalchemy import text

from backend.app.persistence.store import encode, now
from backend.app.persistence.transactions import begin_immediate


def enqueue_workflow(connection, job_id: str, run_id: str, scope: str) -> None:
    connection.execute(
        text("INSERT INTO jobs(id,run_id,scope,status,created_at) VALUES(:id,:run_id,:scope,'queued',:created_at)"),
        {"id": job_id, "run_id": run_id, "scope": scope, "created_at": now()},
    )


def find_record_by_dedupe(db, dedupe: str) -> str | None:
    with db.engine.connect() as connection:
        row = connection.execute(
            text("SELECT id FROM records WHERE dedupe=:dedupe"), {"dedupe": dedupe}
        ).first()
    return row[0] if row else None


def resume_workflow(connection, run_id: str, resume: dict) -> None:
    connection.execute(
        text("""UPDATE jobs
            SET status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END,
                resume=:resume
            WHERE run_id=:run_id"""),
        {"run_id": run_id, "resume": encode(resume)},
    )


def mark_waiting_when_approval_pending(db, run_id: str) -> bool:
    with db.engine.begin() as connection:
        pending = connection.execute(
            text("""SELECT COUNT(*) FROM records
                WHERE kind='approval' AND status='pending'
                  AND json_extract(body,'$.run_id')=:run_id"""),
            {"run_id": run_id},
        ).scalar_one()
        if pending:
            db.update(run_id, status="waiting_approval", conn=connection)
    return bool(pending)


def claim_workflow_job(db, timestamp: float) -> dict | None:
    """Lease one job while preserving serial execution within each scope."""
    with db.engine.connect() as connection:
        begin_immediate(connection)
        connection.execute(
            text("UPDATE jobs SET status='queued' WHERE status='running' AND lease_until<:timestamp"),
            {"timestamp": timestamp},
        )
        row = connection.execute(
            text("""SELECT * FROM jobs current
                WHERE status='queued' AND NOT EXISTS(
                    SELECT 1 FROM jobs other
                    WHERE other.scope=current.scope AND other.status='running'
                )
                ORDER BY created_at LIMIT 1""")
        ).mappings().first()
        if not row:
            connection.rollback()
            return None
        job = dict(row)
        connection.execute(
            text("""UPDATE jobs SET status='running',lease_until=:lease,attempts=attempts+1
                WHERE id=:job_id"""),
            {"job_id": job["id"], "lease": timestamp + 120},
        )
        connection.commit()
    return job


def extend_workflow_lease(db, job_id: str, lease_until: float) -> None:
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET lease_until=:lease WHERE id=:job_id AND status='running'"),
            {"job_id": job_id, "lease": lease_until},
        )


def finish_workflow_job(db, job: dict, status: str) -> None:
    with db.engine.begin() as connection:
        current_resume = connection.execute(
            text("SELECT resume FROM jobs WHERE id=:job_id"), {"job_id": job["id"]}
        ).scalar_one()
        new_resume = current_resume if current_resume != job["resume"] else None
        connection.execute(
            text("""UPDATE jobs SET status=:status,lease_until=0,resume=:resume
                WHERE id=:job_id AND status='running'"""),
            {
                "job_id": job["id"],
                "status": "queued" if new_resume else status,
                "resume": new_resume,
            },
        )


def fail_workflow_job(db, job_id: str) -> None:
    with db.engine.begin() as connection:
        connection.execute(
            text("UPDATE jobs SET status='failed',lease_until=0 WHERE id=:job_id"),
            {"job_id": job_id},
        )


def find_ledger(connection, action_id: str):
    return connection.execute(
        text("SELECT * FROM ledger WHERE action_id=:action_id"), {"action_id": action_id}
    ).mappings().first()


def reserve_ledger(connection, action_id: str) -> None:
    connection.execute(
        text("INSERT INTO ledger VALUES(:action_id,'executing',NULL,:updated_at)"),
        {"action_id": action_id, "updated_at": now()},
    )


def complete_ledger(connection, action_id: str, result: dict) -> None:
    connection.execute(
        text("""UPDATE ledger SET status='completed',result=:result,updated_at=:updated_at
            WHERE action_id=:action_id"""),
        {"result": encode(result), "updated_at": now(), "action_id": action_id},
    )


def cancel_source_reminders(connection, scope: str, source_field: str, item_id: str) -> None:
    connection.execute(
        text("""UPDATE records SET status='cancelled',updated_at=:updated_at
            WHERE kind='reminder' AND scope=:scope AND status='active'
              AND json_extract(body,:path)=:item_id"""),
        {"updated_at": now(), "scope": scope, "path": f"$.{source_field}", "item_id": item_id},
    )


def requeue_failed_run(db, run_id: str) -> None:
    """Atomically return a failed run and its failed jobs to the durable queue."""
    with db.engine.begin() as connection:
        db.update(run_id, status="queued", conn=connection)
        connection.execute(
            text(
                """UPDATE jobs SET status='queued',lease_until=0
                WHERE run_id=:run_id AND status='failed'"""
            ),
            {"run_id": run_id},
        )


def reconcile_external_action(db, run_id: str, run_body: dict, action_id: str, result: dict) -> None:
    """Commit a human-verified external result and run state in one transaction."""
    with db.engine.begin() as connection:
        changed = connection.execute(
            text(
                """UPDATE ledger SET status=:status,result=:result,updated_at=:updated_at
                WHERE action_id=:action_id AND status='executing'"""
            ),
            {
                "status": result["status"],
                "result": encode(result["data"]),
                "updated_at": now(),
                "action_id": action_id,
            },
        ).rowcount
        if not changed:
            raise ValueError("动作已核对或没有外部提交记录")
        outcomes = {**run_body["outcomes"], action_id: result}
        final_status = (
            "completed"
            if all(outcome["status"] == "completed" for outcome in outcomes.values())
            else "completed_with_attention"
        )
        db.update(run_id, {**run_body, "outcomes": outcomes}, final_status, conn=connection)
