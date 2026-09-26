"""Inbox messages, local state and thread endpoints."""
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
@router.get('/mail/messages')
def messages(account_id:str|None=None,status:str|None=None,sort:str='smart',view:str='all',category:str='',include_test:bool=False,
             limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db=Depends(database)):
    if sort not in {'smart','latest','oldest'} or view not in {'all','today','unread','actionable','pending'}:
        raise ValueError('无效的邮件排序或筛选')
    if status == 'inbox':
        query = "SELECT * FROM records WHERE kind='mail_message' AND status IN ('active','review','archived')"
        args = {'limit': limit, 'offset': offset}
        if account_id:
            require(db,account_id,'mail_account')
            query += ' AND scope=:account_id'
            args['account_id'] = account_id
        elif not include_test:
            query += " AND scope IN (SELECT id FROM records WHERE kind='mail_account' AND coalesce(json_extract(body,'$.test_account'),0)=0)"
        if view=='today':
            start=datetime.now(timezone(timedelta(hours=8))).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc).isoformat()
            query+=" AND json_extract(body,'$.received_at')>=:start";args['start']=start
        elif view=='unread':query+=" AND json_extract(body,'$.read_at') IS NULL"
        elif view=='pending':query+=" AND json_type(body,'$.perception') IS NULL"
        elif view=='actionable':query+=" AND (json_extract(body,'$.perception.priority')='high' OR json_extract(body,'$.perception.needs_reply')=1 OR json_array_length(coalesce(json_extract(body,'$.perception.todos'),'[]'))>0 OR json_array_length(coalesce(json_extract(body,'$.perception.calendar_events'),'[]'))>0)"
        if category:
            query+=" AND json_extract(body,'$.perception.category')=:category";args['category']=category
        received="coalesce(json_extract(body,'$.received_at'),created_at)"
        if sort=='latest':order=received+' DESC,id'
        elif sort=='oldest':order=received+' ASC,id'
        else:
            order="(CASE WHEN json_extract(body,'$.perception.priority')='high' THEN 50 ELSE 0 END + CASE WHEN json_extract(body,'$.perception.needs_reply')=1 THEN 30 ELSE 0 END + CASE WHEN json_extract(body,'$.read_at') IS NULL THEN 5 ELSE 0 END) DESC,"+received+' DESC,id'
        with db.engine.connect() as c:
            found = c.execute(text(query + ' ORDER BY '+order+' LIMIT :limit OFFSET :offset'),args).mappings().all()
        items = [{**dict(r),'body':json.loads(r['body'])} for r in found]
    else:
        items=rows(db,'mail_message',[account_id] if account_id else None,status,limit,offset)
    # The list is a summary; full sources are loaded only on opening a message.
    return {'items':[{**r,'body':{k:v for k,v in r['body'].items() if k not in {'raw_text','headers','attachments'}}} for r in items],'limit':limit,'offset':offset}


@router.get('/mail/messages/{ident}')
def message(ident:str,db=Depends(database)):
    return require(db,ident,'mail_message')


@router.post('/mail/messages/{ident}/read')
def message_read(ident:str,body:dict,db=Depends(database)):
    row=require(db,ident,'mail_message');read=bool(body.get('read',True))
    value={**row['body']}
    if read:value['read_at']=now()
    else:value.pop('read_at',None)
    db.update(ident,value,row['status']);return {'id':ident,'read':read}


class MessageState(BaseModel):
    status: str


@router.post('/mail/messages/{ident}/state')
def message_state(ident:str,body:MessageState,db=Depends(database)):
    row=require(db,ident,'mail_message')
    if body.status not in {'active','archived','filtered','review'}:raise ValueError('无效本地状态')
    db.update(ident,status=body.status)
    if body.status in {'active','archived'}:enqueue(db,'index',{'message_id':ident},row['scope'],priority=30)
    db.audit('system','MAIL_LOCAL_STATE',message_id=ident,old=row['status'],new=body.status)
    return db.get(ident)


@router.get('/mail/threads/{ident}')
def thread(ident:str,db=Depends(database)):
    from backend.app.modules.mail.assistant import thread_messages
    row=require(db,ident,'mail_thread')
    return {'thread':row,'messages':thread_messages(db,ident,[row['scope']])}


class ThreadLink(BaseModel):
    target_thread_id: str


@router.post('/mail/messages/{ident}/thread')
def link_thread(ident:str,body:ThreadLink,db=Depends(database)):
    row=require(db,ident,'mail_message');require(db,body.target_thread_id,'mail_thread',[row['scope']])
    db.update(ident,{**row['body'],'thread_id':body.target_thread_id})
    with db.engine.begin() as c:c.execute(text('UPDATE mail_chunks SET thread_id=:t WHERE message_id=:m'),{'t':body.target_thread_id,'m':ident})
    db.audit('system','MAIL_THREAD_LINK',message_id=ident,target=body.target_thread_id)
    return db.get(ident)



