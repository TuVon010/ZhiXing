"""Historical import and persistent background-job endpoints."""
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
@router.post('/mail/imports/preview')
def preview(body:ImportRequest,db=Depends(database)):
    if require(db,body.account_id,'mail_account')['body'].get('test_account'):
        raise ValueError('测试邮箱使用本地合成样本，无远端历史导入')
    return {'job_id':enqueue(db,'preview',body.model_dump(mode='json'),body.account_id,priority=5)}


@router.post('/mail/imports')
def create_import(body:ImportRequest,db=Depends(database)):
    if require(db,body.account_id,'mail_account')['body'].get('test_account'):
        raise ValueError('测试邮箱使用本地合成样本，无远端历史导入')
    ident=db.insert('mail_import',{**body.model_dump(mode='json'),'last_uid':0,'imported':0,'existing':0,'scanned':0},scope=body.account_id,status='running')
    enqueue(db,'import',{'import_id':ident},body.account_id,priority=15)
    return db.get(ident)


@router.get('/mail/imports')
def imports(account_id:str|None=None,db=Depends(database)):
    return {'items':rows(db,'mail_import',[account_id] if account_id else None)}


@router.get('/mail/accounts/{ident}/perception/summary')
def perception_summary(ident:str,db=Depends(database)):
    return perception_queue_summary(db,ident)


@router.post('/mail/accounts/{ident}/perception/batch')
def perception_batch(ident:str,body:BatchRequest,db=Depends(database)):
    return queue_batch(db,ident,body)


@router.post('/mail/imports/{ident}/{operation}')
def import_action(ident:str,operation:str,db=Depends(database)):
    row=require(db,ident,'mail_import')
    if operation not in {'pause','resume','cancel'}:raise ValueError('无效批次操作')
    if row['status'] in {'completed','cancelled'}:raise ValueError('批次已结束')
    if operation=='resume' and row['body']['imported']>=row['body']['limit']:raise ValueError('累计额度已用完，请创建新的时间范围批次')
    db.update(ident,status={'pause':'paused','resume':'running','cancel':'cancelled'}[operation])
    if operation=='resume':enqueue(db,'import',{'import_id':ident},row['scope'],priority=15)
    return db.get(ident)


@router.get('/mail/jobs/{ident}')
def job_status(ident:str,db=Depends(database)):
    return job(db,ident)


@router.post('/mail/jobs/{ident}/retry')
def retry_job(ident:str,db=Depends(database)):
    old=job(db,ident)
    if old['status']!='failed':raise ValueError('只能重试失败的内部读取任务')
    return {'job_id':enqueue(db,old['kind'],old['payload'],old['account_id'],old['scope'],old['priority'])}



