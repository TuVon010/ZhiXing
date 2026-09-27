"""Application services for generic Agent workflow commands."""

from backend.app.agent.graph import ingest
from backend.app.agent.repository import reconcile_external_action, requeue_failed_run
from backend.app.persistence.store import uid


def command_run(db, run_id: str, operation: str) -> dict:
    row = db.get(run_id)
    if row["kind"] != "run":
        raise ValueError("不是运行记录")
    if operation == "cancel":
        db.update(run_id, status="cancelled")
        db.audit(run_id, "WORKFLOW_CANCELLED", status="cancelled")
        for approval in db.list("approval", status="pending", limit=10000):
            if approval["body"]["run_id"] == run_id:
                db.update(approval["id"], status="cancelled")
    elif operation == "retry" and row["status"] == "failed":
        requeue_failed_run(db, run_id)
        db.audit(run_id, "WORKFLOW_RETRY_REQUESTED")
    elif operation == "replay":
        message = {**row["body"]["message"], "message_id": uid()}
        return {"id": ingest(message, replay=True)}
    else:
        raise ValueError("当前状态不支持该操作")
    return {"ok": True}


def reconcile_action(db, run_id: str, decision) -> dict:
    row = db.get(run_id)
    if row["kind"] != "run" or row["status"] in {"running", "queued", "waiting_approval"}:
        raise ValueError("只能核对已停止的运行")
    body = row["body"]
    outcome = body["outcomes"].get(decision.action_id, {})
    if outcome.get("status") not in {"unknown", "failed"}:
        raise ValueError("该动作不需要核对")
    action = next(item for item in body["actions"] if item["id"] == decision.action_id)
    from backend.app.agent.tools import EXTERNAL

    if action["tool"] not in EXTERNAL:
        raise ValueError("仅用于外部写入结果核对")
    result = {
        "status": "completed" if decision.executed else "not_executed",
        "data": {"human_verified": True, "evidence": decision.evidence},
    }
    reconcile_external_action(db, run_id, body, decision.action_id, result)
    db.audit(run_id, "EXTERNAL_RECONCILED", **decision.model_dump())
    if decision.executed:
        return {"ok": True}
    fresh_action = {**action, "id": "0", "depends_on": []}
    message = {
        **body["message"],
        "message_id": uid(),
        "text": f"人工核对确认未执行，重新申请：{action['tool']}",
    }
    retry = ingest(
        message,
        explicit_plan={"summary": "人工核对后重新申请", "actions": [fresh_action]},
    )
    return {"retry_run_id": retry}
