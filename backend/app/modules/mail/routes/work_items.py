"""Todo, follow-up and reminder HTTP endpoints."""
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
@router.get('/mail/followups')
def followups(account_id:str|None=None,db=Depends(database)):
    accounts=[account_id] if account_id else [r['id'] for r in rows(db,'mail_account',limit=1000)]
    items=[]
    for aid in accounts:
        require(db,aid,'mail_account')
        for kind in ('todo','reminder','calendar','mail_followup'):
            items.extend(r for r in db.list(kind,limit=200,scope='web:mail:'+aid)
                         if r['status'] not in {'trashed','deleted'})
        # 感知候选保留邮箱作用域；确认后仍在同一工作台可见。
        for kind in ('todo','calendar'):
            items.extend(r for r in rows(db,kind,[aid],limit=200)
                         if r['body'].get('source')=='perception' and r['status'] not in {'trashed','deleted'})
    return {'items':sorted(items,key=lambda r:r['updated_at'],reverse=True)}


@router.post('/mail/followups/{ident}/{operation}')
def followup_action(ident:str,operation:str,db=Depends(database)):
    if operation not in {'resolve','reopen'}:raise ValueError('无效跟进操作')
    row=require(db,ident,'mail_followup')
    status='resolved' if operation=='resolve' else 'active'
    db.update(ident,{**row['body'],'user_action_at':now()},status)
    db.audit('user','MAIL_FOLLOWUP_'+operation.upper(),followup_id=ident)
    return db.get(ident)


@router.post('/mail/todos/{ident}/{operation}')
def local_todo_action(ident:str,operation:str,db=Depends(database)):
    if operation not in {'complete','reopen'}:raise ValueError('无效待办操作')
    row=require(db,ident,'todo')
    if not row['scope'].startswith('web:mail:') or row['status'] not in {'active','completed'}:
        raise ValueError('仅能更改当前本地工作台的已确认待办')
    account_id=row['scope'].removeprefix('web:mail:')
    require(db,account_id,'mail_account')
    db.update(ident,{**row['body'],'user_action_at':now()},'completed' if operation=='complete' else 'active')
    from backend.app.modules.mail.work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    db.audit('user','MAIL_TODO_'+operation.upper(),todo_id=ident)
    return db.get(ident)


@router.post('/mail/followups')
def create_followup(body:dict,db=Depends(database)):
    from backend.app.modules.mail.schemas import FollowupInput
    value=FollowupInput.model_validate(body)
    require(db,value.account_id,'mail_account')
    scope='web:mail:'+value.account_id
    ident=db.insert('todo',{'title':value.title,'owner':'self','deadline':value.deadline.isoformat() if value.deadline else None,'origin':'mail_workspace'},scope=scope)
    from backend.app.modules.mail.work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    db.audit('system','MAIL_FOLLOWUP_CREATED',todo_id=ident,account_id=value.account_id)
    return db.get(ident)


