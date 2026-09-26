"""Mailbox account lifecycle, dashboard read model and controlled sync endpoints."""
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
@router.get('/mail/accounts')
def accounts(db=Depends(database)):
    return {'items':[public_account(r) for r in rows(db,'mail_account',limit=1000)]}


@router.get('/mail/home')
def home(account_id:str|None=None,include_test:bool=False,db=Depends(database)):
    """Action-oriented home read model; all derived items retain their source ids."""
    accounts=rows(db,'mail_account',limit=1000)
    if account_id:
        require(db,account_id,'mail_account');account_ids=[account_id]
    else:
        account_ids=[a['id'] for a in accounts if include_test or not a['body'].get('test_account')]
    if not account_ids:
        return {'stats':{'new':0,'important':0,'reply':0,'pending_analysis':0,'overdue':0},'important':[],
                'focus':[],'todos':[],'calendar':[],'today_calendar':[],'upcoming_calendar':[],
                'replies':[],'reminders':[],'review':[],'activity':[],'accounts':[],'account_status':[]}
    slots=','.join(':a'+str(i) for i in range(len(account_ids)));params={'a'+str(i):v for i,v in enumerate(account_ids)}
    shanghai=timezone(timedelta(hours=8));current=datetime.now(shanghai)
    start=current.replace(hour=0,minute=0,second=0,microsecond=0);tomorrow=start+timedelta(days=1);week_end=start+timedelta(days=8)
    params['start']=start.astimezone(timezone.utc).isoformat();params['recent']=(start-timedelta(days=7)).astimezone(timezone.utc).isoformat()
    work_scopes=[];work_params={}
    for i,aid in enumerate(account_ids):
        work_scopes.extend((':w'+str(i),':c'+str(i)));work_params['w'+str(i)]='web:mail:'+aid;work_params['c'+str(i)]=aid
    with db.engine.connect() as c:
        mail_rows=c.execute(text(f"""SELECT * FROM records WHERE kind='mail_message' AND scope IN ({slots})
            AND status IN ('active','review','archived') AND json_extract(body,'$.received_at')>=:recent
            ORDER BY json_extract(body,'$.received_at') DESC"""),params).mappings().all()
        today_rows=c.execute(text(f"""SELECT id,status,body FROM records WHERE kind='mail_message' AND scope IN ({slots})
            AND json_extract(body,'$.received_at')>=:start ORDER BY json_extract(body,'$.received_at') DESC"""),params).mappings().all()
        work_rows=c.execute(text(f"""SELECT * FROM records WHERE scope IN ({','.join(work_scopes)})
            AND kind IN ('todo','calendar','reminder','mail_followup')
            AND status NOT IN ('trashed','deleted','dismissed','cancelled') ORDER BY updated_at DESC"""),
            work_params).mappings().all()
    mails=[{**dict(r),'body':json.loads(r['body'])} for r in mail_rows]
    today_mails=[{'id':r['id'],'status':r['status'],'body':json.loads(r['body'])} for r in today_rows]
    work=[{**dict(r),'body':json.loads(r['body'])} for r in work_rows]
    def score(row):
        p=row['body'].get('perception') or {};value=0
        if p.get('priority')=='high':value+=50
        if p.get('needs_reply'):value+=30
        if p.get('todos'):value+=20
        if p.get('calendar_events'):value+=15
        if not row['body'].get('read_at'):value+=5
        return value
    important=sorted((m for m in mails if score(m)>=20 and not m['body'].get('resolved_at')),key=lambda m:m['body'].get('received_at',''),reverse=True)
    important=sorted(important,key=score,reverse=True)[:6]
    def parsed(value):
        try:return datetime.fromisoformat(str(value)).astimezone(shanghai)
        except (TypeError,ValueError):return None
    compact=lambda r:{'id':r['id'],'kind':r['kind'],'status':r['status'],'scope':r['scope'],'body':r['body'],
                      'created_at':r.get('created_at'),'updated_at':r.get('updated_at')}
    calendar=[]
    for row in work:
        if row['kind']!='calendar' or row['status']!='active':continue
        event_start=parsed(row['body'].get('start'));event_end=parsed(row['body'].get('end')) or event_start
        if event_start and event_start<week_end and event_end and event_end>=start:
            calendar.append(compact(row)|{'start_local':event_start.isoformat(),'is_today':event_start<tomorrow and event_end>=start,
                                           'is_next':event_end>=current})
    calendar.sort(key=lambda r:r['start_local'])
    next_event=next((r for r in calendar if (parsed(r['body'].get('end')) or parsed(r['body'].get('start')))>=current),None)
    active_todos=[r for r in work if r['kind']=='todo' and r['status']=='active']
    replies=[r for r in work if r['kind']=='mail_followup' and r['status'] in {'active','waiting'}]
    focus=[];overdue=0
    for row in active_todos:
        due=parsed(row['body'].get('deadline'));urgency='later';rank=40
        if due and due<current:urgency='overdue';rank=0;overdue+=1
        elif due and due<tomorrow:urgency='today';rank=10
        elif due and due<week_end:urgency='week';rank=30
        focus.append(compact(row)|{'urgency':urgency,'sort_key':(rank,due.isoformat() if due else '9999')})
    for row in replies:
        waiting=row['status']=='waiting';focus.append(compact(row)|{'urgency':'waiting' if waiting else 'reply','sort_key':(35 if waiting else 20,row['created_at'])})
    focus.sort(key=lambda r:r['sort_key']);focus=[{k:v for k,v in r.items() if k!='sort_key'} for r in focus[:8]]
    analyzed_today=[m for m in today_mails if m['body'].get('perception')]
    def relevant_candidate(row):
        if row['status']!='candidate':return False
        value=parsed(row['body'].get('start') if row['kind']=='calendar' else row['body'].get('deadline'))
        return not value or value>=current
    review=[r for r in work if relevant_candidate(r)]
    activity=[]
    for mail in analyzed_today[:5]:
        perception=mail['body'].get('perception') or {}
        activity.append({'kind':'analysis','message_id':mail['id'],'title':mail['body'].get('subject') or '无主题',
                         'summary':perception.get('summary'),'outcomes':{'todos':len(perception.get('todos') or []),
                         'calendar':len(perception.get('calendar_events') or []),'needs_reply':bool(perception.get('needs_reply'))}})
    account_status=[]
    for aid in account_ids:
        account=next(a for a in accounts if a['id']==aid);cursor=None
        try:cursor=require(db,'mail-cursor:'+aid+':INBOX','mail_cursor')['body']
        except KeyError:pass
        with db.engine.connect() as c:
            pending=c.execute(text("SELECT COUNT(*) FROM mail_jobs WHERE account_id=:a AND status IN ('queued','running')"),{'a':aid}).scalar_one()
            latest=c.execute(text("SELECT json_extract(body,'$.received_at') FROM records WHERE kind='mail_message' AND scope=:a ORDER BY json_extract(body,'$.received_at') DESC LIMIT 1"),{'a':aid}).scalar()
        account_status.append({'id':aid,'name':account['body'].get('name'),'enabled':bool(account['body'].get('enabled')),
            'auto_analyze':bool(account['body'].get('auto_analyze')),'poll_interval_seconds':account['body'].get('poll_interval_seconds',15),
            'last_checked_at':cursor.get('last_poll') if cursor else None,'last_message_at':latest,'pending_jobs':pending,
            'attention':bool(cursor and cursor.get('paused')),'last_job':account['body'].get('last_job'),
            'last_error':account['body'].get('last_error')})
    today_active=[m for m in today_mails if m['status'] in {'active','review','archived'}]
    brief={'headline':f"今天有 {len(focus)} 项需要关注",
           'detail':f"{len(active_todos)} 项待办，{len(replies)} 项邮件跟进，未来七天 {len(calendar)} 个日程。",
           'next_event':next_event}
    return {'date':start.date().isoformat(),'generated_at':current.isoformat(),'brief':brief,
            'stats':{'new':len(today_mails),'important':len([m for m in today_active if score(m)>=30]),
            'reply':len([m for m in today_active if (m['body'].get('perception') or {}).get('needs_reply')]),
            'pending_analysis':len([m for m in today_active if not m['body'].get('perception')]),'overdue':overdue,
            'filtered':len([m for m in today_mails if m['status']=='filtered']),'analyzed':len(analyzed_today)},
            'important':[compact(m)|{'attention_score':score(m)} for m in important],
            'focus':focus,
            'todos':[compact(r) for r in work if r['kind']=='todo' and r['status'] in {'active','completed'}],
            'calendar':calendar,'today_calendar':[r for r in calendar if r['is_today']],
            'upcoming_calendar':[r for r in calendar if not r['is_today']],
            'replies':[compact(r) for r in replies],
            'reminders':[compact(r) for r in work if r['kind']=='reminder' and r['status']=='active'],
            'review':[compact(r) for r in review],
            'activity':activity,'account_status':account_status,
            'accounts':[public_account(a) for a in accounts if a['id'] in account_ids]}


@router.post('/mail/accounts')
def create_account(body:MailAccount,db=Depends(database)):
    saved=save_account(db,body)
    if saved['body'].get('enabled') and saved['body'].get('auto_import_enabled'):
        from backend.app.workers.mail_jobs import queue_rolling_import
        queue_rolling_import(db,saved['id'],'account_created')
    return saved


@router.post('/mail/test-scenarios')
def test_scenarios(db=Depends(database)):
    from backend.app.modules.mail.scenarios import seed
    return seed(db)


@router.post('/mail/accounts/{ident}')
def update_account(ident:str,body:MailAccount,db=Depends(database)):
    saved=save_account(db,body,ident)
    if saved['body'].get('enabled') and saved['body'].get('auto_import_enabled'):
        from backend.app.workers.mail_jobs import queue_rolling_import
        queue_rolling_import(db,ident,'settings_updated')
    return saved


@router.post('/mail/accounts/{ident}/test')
def connection_test(ident:str,db=Depends(database)):
    if require(db,ident,'mail_account')['body'].get('test_account'):
        raise ValueError('测试邮箱只有本地合成邮件，不连接服务器')
    return {'job_id':enqueue(db,'connection_test',{},ident,priority=0)}


@router.post('/mail/accounts/{ident}/sync')
def sync(ident:str,db=Depends(database)):
    account=require(db,ident,'mail_account')['body']
    if account.get('test_account'):
        raise ValueError('测试邮箱只有本地合成邮件，不连接服务器')
    rolling=None
    if account.get('auto_import_enabled'):
        from backend.app.workers.mail_jobs import queue_rolling_import
        rolling=queue_rolling_import(db,ident,'manual_sync')
    return {'job_id':enqueue(db,'sync',{},ident,priority=10),'rolling_import':rolling}


class Toggle(BaseModel):
    enabled: bool


class ReminderAction(BaseModel):
    minutes:int=Field(default=30,ge=5,le=10080)


@router.post('/mail/reminders/{ident}/{operation}')
def reminder_action(ident:str,operation:str,body:ReminderAction,db=Depends(database)):
    if operation not in {'complete','dismiss','snooze'}:raise ValueError('无效提醒操作')
    row=require(db,ident,'reminder')
    if not row['scope'].startswith('web:mail:'):raise ValueError('不是当前邮件工作台提醒')
    if operation=='snooze':
        due=datetime.now(timezone.utc)+timedelta(minutes=body.minutes)
        db.update(ident,{**row['body'],'due_at':due.isoformat(),'snoozed_at':now()},'active')
    else:db.update(ident,{**row['body'],'user_action_at':now()},'completed' if operation=='complete' else 'dismissed')
    return db.get(ident)


@router.post('/mail/accounts/{ident}/enabled')
def enable(ident:str,body:Toggle,db=Depends(database)):
    row=require(db,ident,'mail_account')
    if row['body'].get('test_account') and body.enabled:
        raise ValueError('测试邮箱不可启用真实收取')
    db.update(ident,{**row['body'],'enabled':body.enabled})
    if body.enabled and row['body'].get('auto_import_enabled'):
        from backend.app.workers.mail_jobs import queue_rolling_import
        queue_rolling_import(db,ident,'account_enabled')
    return public_account(db.get(ident))


@router.get('/mail/accounts/{ident}/status')
def account_status(ident:str,db=Depends(database)):
    account=require(db,ident,'mail_account')
    try:cursor=db.get('mail-cursor:'+ident+':INBOX')
    except KeyError:cursor=None
    with db.engine.connect() as c:
        counts=c.execute(text('SELECT kind,status,COUNT(*) AS count FROM mail_jobs WHERE account_id=:a GROUP BY kind,status'),{'a':ident}).mappings().all()
    return {'account':public_account(account),'cursor':cursor,'jobs':[dict(r) for r in counts]}


@router.post('/mail/accounts/{ident}/backlog/{choice}')
def backlog(ident:str,choice:str,db=Depends(database)):
    from backend.app.modules.mail.ingestion import lease,connection
    account=require(db,ident,'mail_account')['body'];key='mail-cursor:'+ident+':INBOX'
    if account.get('test_account'):
        raise ValueError('测试邮箱没有远端积压')
    if choice not in {'continue','from_now'}:raise ValueError('无效的积压处理方式')
    if choice=='continue':
        row=db.get(key);b=row['body'];b.update(paused=False,catchup=None,last_poll=now());db.update(key,b,'active')
    else:
        # Network operation is queued; resetting the baseline is explicit and audited.
        return {'job_id':enqueue(db,'baseline',{},ident,priority=5)}
    db.audit('system','MAIL_BACKLOG_CHOICE',account_id=ident,choice=choice)
    return {'ok':True}



