"""Read models for the mail workspace; execution remains in runtime/ledger."""
import json
from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from .mail_store import require
from .billing import summarize
from .tracing import detail
from datetime import datetime
from pydantic import BaseModel, Field, model_validator

router = APIRouter()


def database():
    from .main import store
    return store


def activity(db, account_id=None, limit=30, offset=0):
    condition = "kind IN ('run','assistant_turn') AND coalesce(json_extract(body,'$.ui_hidden'),0)=0"
    if account_id:
        require(db, account_id, 'mail_account')
        condition += " AND EXISTS (SELECT 1 FROM json_each(CASE WHEN kind='run' THEN json_extract(body,'$.message.metadata.mail_accounts') ELSE json_extract(body,'$.account_ids') END) WHERE value=:account)"
    else:
        condition += " AND (kind='assistant_turn' OR json_array_length(json_extract(body,'$.message.metadata.mail_accounts'))>0)"
    with db.engine.connect() as c:
        total = c.execute(text('SELECT COUNT(*) FROM records WHERE '+condition), {'account':account_id}).scalar_one()
        records = c.execute(text('SELECT * FROM records WHERE '+condition+' ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset'), {'account':account_id,'limit':limit,'offset':offset}).mappings().all()
    items=[]
    for r in records:
        b=json.loads(r['body'])
        items.append({'id':r['id'],'kind':r['kind'],'status':r['status'],'created_at':r['created_at'],
                      'title':b.get('text') or b.get('summary') or b.get('message',{}).get('text',''),
                      'account_ids':b.get('account_ids') or b.get('message',{}).get('metadata',{}).get('mail_accounts',[])})
    return {'items':items,'total':total,'limit':limit,'offset':offset}


def trace(db, ident):
    root=db.get(ident)
    if root['kind']=='mail_message':
        calls=db.for_run('model_call',ident)
        audit=db.for_run('audit',ident)
        with db.engine.connect() as c:
            jobs=c.execute(text("SELECT id,kind,status,result,attempts,created_at,updated_at FROM mail_jobs WHERE json_extract(payload,'$.message_id')=:id ORDER BY created_at,id"),{'id':ident}).mappings().all()
        return {'id':ident,'requested_id':ident,'root':root,'runs':[],
                'audit':audit,'model_calls':calls,'billing':summarize(calls),
                'jobs':[{**dict(j),'result':json.loads(j['result']) if j['result'] else None} for j in jobs]}
    if root['kind'] not in {'run','assistant_turn'}:raise ValueError('不是 Agent、邮件或动作运行')
    parent=root['body'].get('message',{}).get('metadata',{}).get('assistant_turn')
    if parent: root=require(db,parent,'assistant_turn')
    if root['kind']=='assistant_turn':
        with db.engine.connect() as c:
            ids=c.execute(text("SELECT id FROM records WHERE kind='run' AND json_extract(body,'$.message.metadata.assistant_turn')=:id ORDER BY created_at,id"),{'id':root['id']}).scalars().all()
        runs=[detail(db,rid).model_dump() for rid in ids]
        audit=db.for_run('audit',root['id'])
        calls=db.for_run('model_call',root['id'])
    else:
        runs=[detail(db,root['id']).model_dump()];audit=[];calls=[]
    for run in runs:
        audit.extend(run['audit']);calls.extend(run['model_calls'])
    audit=sorted({r['id']:r for r in audit}.values(),key=lambda r:(r['created_at'],r['id']))
    calls=list({r['id']:r for r in calls}.values())
    return {'id':root['id'],'requested_id':ident,'root':root,'runs':runs,'audit':audit,
            'model_calls':calls,'billing':summarize(calls)}


@router.get('/mail/activity')
def list_activity(account_id:str|None=None,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(database)):
    return activity(db,account_id,limit,offset)


@router.get('/mail/traces/{ident}')
def get_trace(ident:str,db=Depends(database)):
    return trace(db,ident)


@router.get('/mail/notifications')
def notifications(account_id:str|None=None,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(database)):
    condition="n.kind='notification' AND n.status!='trashed'"
    if account_id:
        require(db,account_id,'mail_account')
        condition+=" AND (n.scope=:scope OR EXISTS (SELECT 1 FROM records r,json_each(json_extract(r.body,'$.message.metadata.mail_accounts')) a WHERE r.id=json_extract(n.body,'$.run_id') AND a.value=:account))"
    else:
        condition+=" AND n.scope LIKE 'web:mail:%'"
    args={'scope':'web:mail:'+str(account_id),'account':account_id,'limit':limit,'offset':offset}
    with db.engine.connect() as c:
        total=c.execute(text('SELECT COUNT(*) FROM records n WHERE '+condition),args).scalar_one()
        unread=c.execute(text("SELECT COUNT(*) FROM records n WHERE "+condition+" AND n.status IN ('pending','local')"),args).scalar_one()
        records=c.execute(text('SELECT n.* FROM records n WHERE '+condition+' ORDER BY n.created_at DESC,n.id LIMIT :limit OFFSET :offset'),args).mappings().all()
    return {'items':[{**dict(r),'body':json.loads(r['body'])} for r in records],'total':total,'unread':unread}


@router.post('/mail/notifications/{ident}/read')
def read_notification(ident:str,db=Depends(database)):
    require(db,ident,'notification');db.update(ident,status='read')
    return {'ok':True}


class CalendarInput(BaseModel):
    account_id: str
    title: str = Field(min_length=1,max_length=200)
    start: datetime
    end: datetime

    @model_validator(mode='after')
    def times(self):
        if self.start.tzinfo is None or self.end.tzinfo is None or self.end<=self.start:
            raise ValueError('日程必须带时区且结束时间晚于开始时间')
        return self


def calendar_action(db,body,ident=None):
    from .db import uid
    require(db,body.account_id,'mail_account')
    args=body.model_dump(mode='json');args.pop('account_id');scope='web:mail:'+body.account_id
    from .mail_schedule import detect_conflicts,sync_calendar_reminder
    conflicts=detect_conflicts(db,body.account_id,args['start'],args['end'],ident)
    if ident:
        old=require(db,ident,'calendar',[scope]);db.update(ident,{**old['body'],**args,'conflicts':conflicts,'has_conflict':bool(conflicts),'updated_by':'user'},'active')
    else:
        ident=db.insert('calendar',{**args,'source':'manual','conflicts':conflicts,'has_conflict':bool(conflicts),'created_by':'user'},id='calendar-'+uid(),scope=scope)
    sync_calendar_reminder(db,ident)
    db.audit('user','MAIL_CALENDAR_SAVED',calendar_id=ident,account_id=body.account_id,conflicts=len(conflicts))
    return {'id':ident,'status':'active','conflicts':conflicts}


@router.post('/mail/calendar')
def create_calendar(body:CalendarInput,db=Depends(database)):
    return calendar_action(db,body)


@router.post('/mail/calendar/{ident}')
def update_calendar(ident:str,body:CalendarInput,db=Depends(database)):
    return calendar_action(db,body,ident)
