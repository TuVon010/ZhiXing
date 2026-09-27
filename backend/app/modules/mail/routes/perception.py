"""HTTP adapter for perception, candidate review and daily digest use cases."""

from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.repository import require, rows
from backend.app.modules.mail.schemas import PerceptionFeedback
from backend.app.modules.mail.services import perception as perception_service


router = APIRouter()
database = get_store


@router.get("/mail/messages/{ident}/perception")
def get_perception(ident: str, db=Depends(database)):
    from backend.app.modules.mail.perception import get_perception as load_perception

    result = load_perception(db, ident)
    return {"perception": result, "status": "ready" if result is not None else "pending"}


@router.post("/mail/messages/{ident}/perception")
def request_perception(ident: str, db=Depends(database)):
    return perception_service.request_analysis(db, ident)


@router.post("/mail/messages/{ident}/perception/feedback")
def perception_feedback(ident: str, body: PerceptionFeedback, db=Depends(database)):
    from backend.app.modules.mail.perception import apply_feedback

    body.message_id = ident
    return apply_feedback(db, body)


@router.get("/mail/perception/todos")
def list_perception_todos(account_id: str = "", db=Depends(database)):
    return perception_service.list_todo_candidates(db, account_id)


@router.post("/mail/perception/todos/{ident}/confirm")
def confirm_perception_todo(ident: str, db=Depends(database)):
    return perception_service.confirm_todo(db, ident)


@router.post("/mail/perception/todos/{ident}/dismiss")
def dismiss_perception_todo(ident: str, db=Depends(database)):
    return perception_service.dismiss_candidate(db, ident, "todo")


@router.get("/mail/perception/calendars")
def list_perception_calendars(account_id: str = "", db=Depends(database)):
    from backend.app.modules.mail.calendar import list_calendar_candidates

    if account_id:
        return list_calendar_candidates(db, account_id)
    result = []
    for account in rows(db, "mail_account", limit=100):
        result.extend(list_calendar_candidates(db, account["id"]))
    return result


@router.post("/mail/perception/calendars/{ident}/confirm")
def confirm_perception_calendar(ident: str, db=Depends(database)):
    from backend.app.modules.mail.calendar import confirm_calendar

    row = require(db, ident, "calendar")
    account_id = row["scope"].removeprefix("web:mail:")
    require(db, account_id, "mail_account")
    return confirm_calendar(db, ident, account_id)


@router.post("/mail/perception/calendars/{ident}/dismiss")
def dismiss_perception_calendar(ident: str, db=Depends(database)):
    return perception_service.dismiss_candidate(db, ident, "calendar")


@router.get("/mail/digest")
def get_digest(account_id: str = "", date: str = "", db=Depends(database)):
    from backend.app.modules.mail.digest import get_digest as load_digest, get_latest_digest

    if account_id:
        return load_digest(db, account_id, date or None) or {"message": "该日期暂无摘要"}
    return [
        digest
        for account in rows(db, "mail_account", limit=100)
        if (digest := get_latest_digest(db, account["id"]))
    ]


@router.post("/mail/digest/generate")
def generate_digest_now(account_id: str = "", date: str = "", db=Depends(database)):
    from backend.app.modules.mail.digest import generate_and_save_digest

    if account_id:
        return generate_and_save_digest(db, account_id, date or None)
    return [
        generate_and_save_digest(db, account["id"], date or None)
        for account in rows(db, "mail_account", limit=100)
    ]
