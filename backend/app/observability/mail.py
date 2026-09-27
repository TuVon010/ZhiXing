"""Read models for the mail workspace; execution remains in runtime/ledger."""
from fastapi import APIRouter, Depends, Query
from backend.app.modules.mail.repository import require
from backend.app.observability.billing import summarize
from backend.app.observability.repository import (
    activity_rows,
    jobs_for_message,
    notification_rows,
    run_ids_for_turn,
)
from backend.app.observability.tracing import detail
from datetime import datetime
from pydantic import BaseModel, Field, model_validator

router = APIRouter()


def database():
    from backend.app.persistence.store import store
    return store


def activity(db, account_id=None, limit=30, offset=0):
    if account_id:
        require(db, account_id, 'mail_account')
    total, records = activity_rows(db,account_id,limit,offset)
    items=[]
    for r in records:
        b=r['body']
        items.append({'id':r['id'],'kind':r['kind'],'status':r['status'],'created_at':r['created_at'],
                      'title':b.get('text') or b.get('summary') or b.get('message',{}).get('text',''),
                      'account_ids':b.get('account_ids') or b.get('message',{}).get('metadata',{}).get('mail_accounts',[])})
    return {'items':items,'total':total,'limit':limit,'offset':offset}


def trace(db, ident):
    root=db.get(ident)
    if root['kind'] in {'mail_message', 'mail_draft'}:
        calls=db.for_run('model_call',ident)
        audit=db.for_run('audit',ident)
        jobs=jobs_for_message(db,ident)
        return {'id':ident,'requested_id':ident,'root':root,'runs':[],
                'audit':audit,'model_calls':calls,'billing':summarize(calls),
                'jobs':jobs}
    if root['kind'] not in {'run','assistant_turn'}:raise ValueError('不是 Agent、邮件或动作运行')
    parent=root['body'].get('message',{}).get('metadata',{}).get('assistant_turn')
    if parent: root=require(db,parent,'assistant_turn')
    if root['kind']=='assistant_turn':
        ids=run_ids_for_turn(db,root['id'])
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
    if account_id:
        require(db,account_id,'mail_account')
    return notification_rows(db,account_id,limit,offset)


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
    from backend.app.persistence.store import uid
    require(db,body.account_id,'mail_account')
    args=body.model_dump(mode='json');args.pop('account_id');scope='web:mail:'+body.account_id
    from backend.app.modules.mail.calendar import detect_conflicts,sync_calendar_reminder
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
