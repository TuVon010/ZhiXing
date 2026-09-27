"""HTTP adapter for todo and follow-up use cases."""

from fastapi import APIRouter, Depends

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.schemas import FollowupInput
from backend.app.modules.mail.services import work_items as work_item_service


router = APIRouter()
database = get_store


@router.get("/mail/followups")
def followups(account_id: str | None = None, db=Depends(database)):
    return work_item_service.list_followups(db, account_id)


@router.post("/mail/followups/{ident}/{operation}")
def followup_action(ident: str, operation: str, db=Depends(database)):
    return work_item_service.update_followup(db, ident, operation)


@router.post("/mail/todos/{ident}/{operation}")
def local_todo_action(ident: str, operation: str, db=Depends(database)):
    return work_item_service.update_todo(db, ident, operation)


@router.post("/mail/followups")
def create_followup(body: dict, db=Depends(database)):
    return work_item_service.create_todo(db, FollowupInput.model_validate(body))
