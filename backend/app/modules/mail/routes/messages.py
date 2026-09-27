"""HTTP adapter for inbox messages and thread relations."""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.repository import require
from backend.app.modules.mail.services import messages as message_service


router = APIRouter()
database = get_store


@router.get("/mail/messages")
def messages(
    account_id: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db=Depends(database),
    sort: str = "smart",
    view: str = "all",
    category: str = "",
    include_test: bool = False,
):
    return message_service.list_messages(
        db,
        account_id=account_id,
        status=status,
        sort=sort,
        view=view,
        category=category,
        include_test=include_test,
        limit=limit,
        offset=offset,
    )


@router.get("/mail/messages/{ident}")
def message(ident: str, db=Depends(database)):
    return require(db, ident, "mail_message")


@router.post("/mail/messages/{ident}/read")
def message_read(ident: str, body: dict, db=Depends(database)):
    return message_service.set_read_state(db, ident, bool(body.get("read", True)))


class MessageState(BaseModel):
    status: str


@router.post("/mail/messages/{ident}/state")
def message_state(ident: str, body: MessageState, db=Depends(database)):
    return message_service.change_local_state(db, ident, body.status)


@router.get("/mail/threads/{ident}")
def thread(ident: str, db=Depends(database)):
    from backend.app.modules.mail.assistant import thread_messages
    row = require(db, ident, "mail_thread")
    return {"thread": row, "messages": thread_messages(db, ident, [row["scope"]])}


class ThreadLink(BaseModel):
    target_thread_id: str


@router.post("/mail/messages/{ident}/thread")
def link_thread(ident: str, body: ThreadLink, db=Depends(database)):
    return message_service.attach_to_thread(db, ident, body.target_thread_id)
