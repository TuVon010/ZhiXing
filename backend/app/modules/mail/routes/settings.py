"""Per-account filters, memory candidates, migration review and demo data endpoints."""
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
class FilterRules(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=True
    whitelist_senders:list[str]=Field(default_factory=list,max_length=200)
    blacklist_senders:list[str]=Field(default_factory=list,max_length=200)
    blacklist_domains:list[str]=Field(default_factory=list,max_length=200)
    subject_keywords:list[str]=Field(default_factory=list,max_length=200)
    content_keywords:list[str]=Field(default_factory=list,max_length=200)
    auto_filter_categories:list[str]=Field(default_factory=lambda:['ad','subscription'])
    @field_validator('whitelist_senders','blacklist_senders','blacklist_domains','subject_keywords','content_keywords')
    @classmethod
    def bounded_rules(cls,values):
        if any(not v.strip() or len(v)>256 for v in values):raise ValueError('过滤条件须为 1—256 字符')
        return list(dict.fromkeys(v.strip() for v in values))


@router.get('/mail/accounts/{ident}/filters')
def get_filters(ident:str,db=Depends(database)):
    require(db,ident,'mail_account')
    try:return db.get('mail-filter:'+ident)['body']
    except KeyError:return {**FilterRules().model_dump(),'version':0}


@router.post('/mail/accounts/{ident}/filters')
def filters(ident:str,body:FilterRules,db=Depends(database)):
    from backend.app.modules.mail.ingestion import put
    require(db,ident,'mail_account')
    if set(body.auto_filter_categories)-{'ad','subscription','transaction','todo','manual'}:raise ValueError('未知分类')
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE');key='mail-filter:'+ident
        try:old=db.get(key,c)
        except KeyError:old=None
        value={**body.model_dump(),'version':old['body'].get('version',0)+1 if old else 1}
        if old:db.update(key,value,conn=c)
        else:db.insert('setting',value,id=key,conn=c)
        c.commit()
    return value


class MemoryCandidate(BaseModel):
    content:str=Field(min_length=1,max_length=2000)
    account_id:str
    memory_type:str='preference'


@router.post('/mail/memory')
def candidate_memory(body:MemoryCandidate,db=Depends(database)):
    if body.account_id!='global':require(db,body.account_id,'mail_account')
    ident=db.insert('memory',body.model_dump(),status='candidate',scope=body.account_id)
    return db.get(ident)


@router.get('/mail/migration')
def migration_status(db=Depends(database)):
    return {'runs':rows(db,'run',status='migration_review'),'unassigned':rows(db,'mail_message',['legacy-unassigned'])}


@router.post('/mail/legacy/{ident}/resume')
def resume_legacy(ident:str,db=Depends(database)):
    row=require(db,ident,'run')
    if row['status']!='migration_review':raise ValueError('运行不需要迁移复核')
    old=row['body'].get('migration_previous_status','queued')
    with db.engine.begin() as c:
        db.update(ident,status='waiting_approval' if old=='waiting_approval' else 'queued',conn=c)
        c.execute(text('UPDATE jobs SET status=:status WHERE run_id=:id'),{'status':'waiting' if old=='waiting_approval' else 'queued','id':ident})
    db.audit(ident,'MIGRATION_REVIEWED');return db.get(ident)


@router.post('/mail/demo')
def demo(db=Depends(database)):
    from backend.app.core.config import settings
    if settings.mode!='demo':raise ValueError('演示资料仅能在 demo 模式导入')
    from backend.app.modules.mail.ingestion import store_message
    from email.message import EmailMessage
    account=save_account(db,MailAccount(name='演示邮箱',address='demo@example.com',provider='custom',imap_host='example.com',smtp_host='example.com'))
    msg=EmailMessage();msg['From']='teacher@example.com';msg['To']='demo@example.com';msg['Subject']='实验报告修改';msg['Message-ID']='<demo-experiment@example.com>';msg.set_content('请在周五前补充实验对比，并回复最终报告。')
    mid=store_message(db,account['id'],'demo',1,msg.as_bytes(),now())
    return {'account_id':account['id'],'message_id':mid}



# ============================================================
# 主动感知 Agent API
# ============================================================


