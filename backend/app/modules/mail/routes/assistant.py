"""HTTP adapter for retrieval, drafts and bounded mail-Agent sessions."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.schemas import DraftInput, SearchRequest, SessionInput, TurnInput
from backend.app.modules.mail.services import assistant as assistant_service


router = APIRouter()
database = get_store


@router.post("/search")
def search(body: SearchRequest, db=Depends(database)):
    return assistant_service.request_search(db, body)


@router.post("/mail/accounts/{ident}/reindex")
def reindex(ident: str, db=Depends(database)):
    return assistant_service.request_reindex(db, ident)


@router.get("/mail/drafts")
def drafts(account_id: str | None = None, db=Depends(database)):
    return assistant_service.list_drafts(db, account_id)


@router.post("/mail/drafts")
def create_draft(body: DraftInput, db=Depends(database)):
    from backend.app.modules.mail.assistant import draft
    return draft(db, body)


@router.post("/mail/drafts/{ident}")
def update_draft(ident: str, body: DraftInput, db=Depends(database)):
    from backend.app.modules.mail.assistant import draft
    return draft(db, body, ident)


@router.post("/mail/drafts/{ident}/suggest")
def suggest_draft_reply(ident: str, db=Depends(database)):
    from backend.app.modules.mail.assistant import suggest_reply
    return suggest_reply(db, ident)


class Version(BaseModel):
    version: int = Field(ge=1)


@router.post("/mail/drafts/{ident}/send")
def confirm_send(ident: str, body: Version, db=Depends(database)):
    """Authorize exactly one frozen draft version from a user's final click."""
    from backend.app.modules.mail.assistant import submit_draft
    return submit_draft(db, ident, body.version, user_confirmed=True)


@router.post("/assistant/sessions")
def session(body: SessionInput, db=Depends(database)):
    from backend.app.modules.mail.assistant import create_session
    return create_session(db, body)


@router.get("/assistant/sessions")
def sessions(db=Depends(database)):
    return assistant_service.list_sessions(db)


@router.post("/assistant/sessions/{ident}/turns")
def turn(ident: str, body: TurnInput, db=Depends(database)):
    from backend.app.modules.mail.assistant import create_turn
    return create_turn(db, ident, body)


@router.get("/assistant/sessions/{ident}")
def session_detail(ident: str, db=Depends(database)):
    return assistant_service.get_session(db, ident)


@router.get("/assistant/turns/{ident}")
def turn_detail(ident: str, db=Depends(database)):
    return assistant_service.get_turn(db, ident)


@router.post("/assistant/turns/{ident}/cancel")
def cancel_turn(ident: str, db=Depends(database)):
    return assistant_service.cancel_turn(db, ident)
