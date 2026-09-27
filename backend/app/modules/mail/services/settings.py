"""Application services for filters, memory candidates and migration review."""

from backend.app.modules.mail.repositories.settings import resume_legacy_run, save_filter_rules
from backend.app.modules.mail.repository import require, rows


ALLOWED_AUTO_FILTER_CATEGORIES = {"ad", "subscription", "transaction", "todo", "manual"}


def get_filter_rules(db, account_id: str, default: dict) -> dict:
    require(db, account_id, "mail_account")
    try:
        return db.get(f"mail-filter:{account_id}")["body"]
    except KeyError:
        return {**default, "version": 0}


def update_filter_rules(db, account_id: str, rules: dict) -> dict:
    require(db, account_id, "mail_account")
    if set(rules["auto_filter_categories"]) - ALLOWED_AUTO_FILTER_CATEGORIES:
        raise ValueError("未知分类")
    return save_filter_rules(db, account_id, rules)


def create_memory_candidate(db, body) -> dict:
    if body.account_id != "global":
        require(db, body.account_id, "mail_account")
    item_id = db.insert("memory", body.model_dump(), status="candidate", scope=body.account_id)
    return db.get(item_id)


def migration_status(db) -> dict:
    return {
        "runs": rows(db, "run", status="migration_review"),
        "unassigned": rows(db, "mail_message", ["legacy-unassigned"]),
    }


def resume_legacy(db, run_id: str) -> dict:
    row = require(db, run_id, "run")
    if row["status"] != "migration_review":
        raise ValueError("运行不需要迁移复核")
    previous = row["body"].get("migration_previous_status", "queued")
    resume_legacy_run(db, run_id, previous)
    db.audit(run_id, "MIGRATION_REVIEWED")
    return db.get(run_id)
