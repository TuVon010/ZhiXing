"""Application services for mailbox account commands and status queries."""

from backend.app.modules.mail.repositories.dashboard import load_job_counts
from backend.app.modules.mail.repository import enqueue, public_account, require, rows, save_account
from backend.app.persistence.store import now


def list_accounts(db) -> dict:
    return {"items": [public_account(row) for row in rows(db, "mail_account", limit=1000)]}


def save_mail_account(db, body, account_id: str | None = None) -> dict:
    saved = save_account(db, body, account_id)
    if saved["body"].get("enabled") and saved["body"].get("auto_import_enabled"):
        from backend.app.workers.mail_jobs import queue_rolling_import

        queue_rolling_import(db, saved["id"], "settings_updated" if account_id else "account_created")
    return saved


def request_connection_test(db, account_id: str) -> dict:
    if require(db, account_id, "mail_account")["body"].get("test_account"):
        raise ValueError("测试邮箱只有本地合成邮件，不连接服务器")
    return {"job_id": enqueue(db, "connection_test", {}, account_id, priority=0)}


def request_sync(db, account_id: str) -> dict:
    account = require(db, account_id, "mail_account")["body"]
    if account.get("test_account"):
        raise ValueError("测试邮箱只有本地合成邮件，不连接服务器")
    rolling = None
    if account.get("auto_import_enabled"):
        from backend.app.workers.mail_jobs import queue_rolling_import

        rolling = queue_rolling_import(db, account_id, "manual_sync")
    return {
        "job_id": enqueue(db, "sync", {}, account_id, priority=10),
        "rolling_import": rolling,
    }


def set_enabled(db, account_id: str, enabled: bool) -> dict:
    row = require(db, account_id, "mail_account")
    if row["body"].get("test_account") and enabled:
        raise ValueError("测试邮箱不可启用真实收取")
    db.update(account_id, {**row["body"], "enabled": enabled})
    if enabled and row["body"].get("auto_import_enabled"):
        from backend.app.workers.mail_jobs import queue_rolling_import

        queue_rolling_import(db, account_id, "account_enabled")
    return public_account(db.get(account_id))


def get_account_status(db, account_id: str) -> dict:
    account = require(db, account_id, "mail_account")
    try:
        cursor = db.get(f"mail-cursor:{account_id}:INBOX")
    except KeyError:
        cursor = None
    return {
        "account": public_account(account),
        "cursor": cursor,
        "jobs": load_job_counts(db, account_id),
    }


def choose_backlog(db, account_id: str, choice: str) -> dict:
    account = require(db, account_id, "mail_account")["body"]
    if account.get("test_account"):
        raise ValueError("测试邮箱没有远端积压")
    if choice not in {"continue", "from_now"}:
        raise ValueError("无效的积压处理方式")
    if choice == "from_now":
        return {"job_id": enqueue(db, "baseline", {}, account_id, priority=5)}
    key = f"mail-cursor:{account_id}:INBOX"
    row = db.get(key)
    body = {**row["body"], "paused": False, "catchup": None, "last_poll": now()}
    db.update(key, body, "active")
    db.audit("system", "MAIL_BACKLOG_CHOICE", account_id=account_id, choice=choice)
    return {"ok": True}
