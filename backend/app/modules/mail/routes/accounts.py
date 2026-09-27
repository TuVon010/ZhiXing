"""HTTP adapter for mailbox account and dashboard use cases."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.schemas import MailAccount
from backend.app.modules.mail.services import accounts as account_service
from backend.app.modules.mail.services.dashboard import build_home
from backend.app.modules.mail.services.work_items import update_reminder


router = APIRouter()
database = get_store


@router.get("/mail/accounts")
def accounts(db=Depends(database)):
    return account_service.list_accounts(db)


@router.get("/mail/home")
def home(account_id: str | None = None, include_test: bool = False, db=Depends(database)):
    return build_home(db, account_id, include_test)


@router.post("/mail/accounts")
def create_account(body: MailAccount, db=Depends(database)):
    return account_service.save_mail_account(db, body)


@router.post("/mail/test-scenarios")
def test_scenarios(db=Depends(database)):
    from backend.app.modules.mail.scenarios import seed
    return seed(db)


@router.post("/mail/accounts/{ident}")
def update_account(ident: str, body: MailAccount, db=Depends(database)):
    return account_service.save_mail_account(db, body, ident)


@router.post("/mail/accounts/{ident}/test")
def connection_test(ident: str, db=Depends(database)):
    return account_service.request_connection_test(db, ident)


@router.post("/mail/accounts/{ident}/sync")
def sync(ident: str, db=Depends(database)):
    return account_service.request_sync(db, ident)


class Toggle(BaseModel):
    enabled: bool


class ReminderAction(BaseModel):
    minutes: int = Field(default=30, ge=5, le=10080)


@router.post("/mail/reminders/{ident}/{operation}")
def reminder_action(ident: str, operation: str, body: ReminderAction, db=Depends(database)):
    return update_reminder(db, ident, operation, body.minutes)


@router.post("/mail/accounts/{ident}/enabled")
def enable(ident: str, body: Toggle, db=Depends(database)):
    return account_service.set_enabled(db, ident, body.enabled)


@router.get("/mail/accounts/{ident}/status")
def account_status(ident: str, db=Depends(database)):
    return account_service.get_account_status(db, ident)


@router.post("/mail/accounts/{ident}/backlog/{choice}")
def backlog(ident: str, choice: str, db=Depends(database)):
    return account_service.choose_backlog(db, ident, choice)
