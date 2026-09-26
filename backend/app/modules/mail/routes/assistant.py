"""Search, drafts and bounded mail-agent session endpoints."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, ConfigDict
from sqlalchemy import text
import json
from datetime import datetime, timezone, timedelta
from backend.app.modules.mail.schemas import MailAccount, ImportRequest, SearchRequest, DraftInput, SessionInput, TurnInput, PerceptionFeedback
from backend.app.modules.mail.perception_queue import BatchRequest, queue_batch, summary as perception_queue_summary
from backend.app.modules.mail.repository import initialize, rows, require, public_account, save_account, enqueue, job, validate_accounts
from backend.app.persistence.store import now

router = APIRouter()


def database():
    """Provide the shared local store while keeping route handlers injectable."""
    from backend.app.persistence.store import store
    initialize(store)
    return store
@router.post('/search')
def search(body:SearchRequest,db=Depends(database)):
    validate_accounts(db,body.account_ids)
    return {'job_id':enqueue(db,'search',body.model_dump(mode='json'),scope='search:'+now(),priority=0)}


@router.post('/mail/accounts/{ident}/reindex')
def reindex(ident:str,db=Depends(database)):
    require(db,ident,'mail_account')
    return {'job_id':enqueue(db,'reindex',{},ident,priority=50)}


@router.get('/mail/drafts')
def drafts(account_id:str|None=None,db=Depends(database)):
    return {'items':[r for r in rows(db,'mail_draft',[account_id] if account_id else None) if r['status']!='trashed']}


@router.post('/mail/drafts')
def create_draft(body:DraftInput,db=Depends(database)):
    from backend.app.modules.mail.assistant import draft
    return draft(db,body)


@router.post('/mail/drafts/{ident}')
def update_draft(ident:str,body:DraftInput,db=Depends(database)):
    from backend.app.modules.mail.assistant import draft
    return draft(db,body,ident)


class Version(BaseModel):
    version:int=Field(ge=1)


@router.post('/mail/drafts/{ident}/send')
def confirm_send(ident:str,body:Version,db=Depends(database)):
    """A user's final click authorizes exactly this frozen draft version."""
    from backend.app.modules.mail.assistant import submit_draft
    return submit_draft(db,ident,body.version,user_confirmed=True)


@router.post('/assistant/sessions')
def session(body:SessionInput,db=Depends(database)):
    from backend.app.modules.mail.assistant import create_session
    return create_session(db,body)


@router.get('/assistant/sessions')
def sessions(db=Depends(database)):
    return {'items':[r for r in rows(db,'assistant_session') if r['status']!='trashed']}


@router.post('/assistant/sessions/{ident}/turns')
def turn(ident:str,body:TurnInput,db=Depends(database)):
    from backend.app.modules.mail.assistant import create_turn
    return create_turn(db,ident,body)


@router.get('/assistant/sessions/{ident}')
def session_detail(ident:str,db=Depends(database)):
    return {'session':require(db,ident,'assistant_session'),'turns':rows(db,'assistant_turn',[ident])}


@router.get('/assistant/turns/{ident}')
def turn_detail(ident:str,db=Depends(database)):
    from backend.app.observability.billing import summarize
    row=require(db,ident,'assistant_turn');calls=db.for_run('model_call',ident)
    return {'turn':row,'audit':db.for_run('audit',ident),'model_calls':calls,'billing':summarize(calls)}


@router.post('/assistant/turns/{ident}/cancel')
def cancel_turn(ident:str,db=Depends(database)):
    require(db,ident,'assistant_turn');db.update(ident,status='cancelled');return {'ok':True}



