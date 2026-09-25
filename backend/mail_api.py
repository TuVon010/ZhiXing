from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,Field,field_validator,ConfigDict
from sqlalchemy import text
import json
from datetime import datetime,timezone,timedelta
from .mail_models import MailAccount,ImportRequest,SearchRequest,DraftInput,SessionInput,TurnInput,PerceptionFeedback
from .mail_perception_queue import BatchRequest, queue_batch, summary as perception_queue_summary
from .mail_store import initialize,rows,require,public_account,save_account,enqueue,job,validate_accounts
from .db import now

router=APIRouter(prefix='/api')


def database():
    from .main import store
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
    from .mail_work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    db.audit('user','MAIL_TODO_'+operation.upper(),todo_id=ident)
    return db.get(ident)


@router.post('/mail/followups')
def create_followup(body:dict,db=Depends(database)):
    from .mail_models import FollowupInput
    value=FollowupInput.model_validate(body)
    require(db,value.account_id,'mail_account')
    scope='web:mail:'+value.account_id
    ident=db.insert('todo',{'title':value.title,'owner':'self','deadline':value.deadline.isoformat() if value.deadline else None,'origin':'mail_workspace'},scope=scope)
    from .mail_work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    db.audit('system','MAIL_FOLLOWUP_CREATED',todo_id=ident,account_id=value.account_id)
    return db.get(ident)


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
        from .mail_worker import queue_rolling_import
        queue_rolling_import(db,saved['id'],'account_created')
    return saved


@router.post('/mail/test-scenarios')
def test_scenarios(db=Depends(database)):
    from .mail_scenarios import seed
    return seed(db)


@router.post('/mail/accounts/{ident}')
def update_account(ident:str,body:MailAccount,db=Depends(database)):
    saved=save_account(db,body,ident)
    if saved['body'].get('enabled') and saved['body'].get('auto_import_enabled'):
        from .mail_worker import queue_rolling_import
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
        from .mail_worker import queue_rolling_import
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
        from .mail_worker import queue_rolling_import
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
    from .mail_ingest import lease,connection
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
    from .mail_assistant import thread_messages
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
    from .mail_assistant import draft
    return draft(db,body)


@router.post('/mail/drafts/{ident}')
def update_draft(ident:str,body:DraftInput,db=Depends(database)):
    from .mail_assistant import draft
    return draft(db,body,ident)


class Version(BaseModel):
    version:int=Field(ge=1)


@router.post('/mail/drafts/{ident}/send')
def confirm_send(ident:str,body:Version,db=Depends(database)):
    """A user's final click authorizes exactly this frozen draft version."""
    from .mail_assistant import submit_draft
    return submit_draft(db,ident,body.version,user_confirmed=True)


@router.post('/assistant/sessions')
def session(body:SessionInput,db=Depends(database)):
    from .mail_assistant import create_session
    return create_session(db,body)


@router.get('/assistant/sessions')
def sessions(db=Depends(database)):
    return {'items':[r for r in rows(db,'assistant_session') if r['status']!='trashed']}


@router.post('/assistant/sessions/{ident}/turns')
def turn(ident:str,body:TurnInput,db=Depends(database)):
    from .mail_assistant import create_turn
    return create_turn(db,ident,body)


@router.get('/assistant/sessions/{ident}')
def session_detail(ident:str,db=Depends(database)):
    return {'session':require(db,ident,'assistant_session'),'turns':rows(db,'assistant_turn',[ident])}


@router.get('/assistant/turns/{ident}')
def turn_detail(ident:str,db=Depends(database)):
    from .billing import summarize
    row=require(db,ident,'assistant_turn');calls=db.for_run('model_call',ident)
    return {'turn':row,'audit':db.for_run('audit',ident),'model_calls':calls,'billing':summarize(calls)}


@router.post('/assistant/turns/{ident}/cancel')
def cancel_turn(ident:str,db=Depends(database)):
    require(db,ident,'assistant_turn');db.update(ident,status='cancelled');return {'ok':True}


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
    from .mail_ingest import put
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
    from .config import settings
    if settings.mode!='demo':raise ValueError('演示资料仅能在 demo 模式导入')
    from .mail_ingest import store_message
    from email.message import EmailMessage
    account=save_account(db,MailAccount(name='演示邮箱',address='demo@example.com',provider='custom',imap_host='example.com',smtp_host='example.com'))
    msg=EmailMessage();msg['From']='teacher@example.com';msg['To']='demo@example.com';msg['Subject']='实验报告修改';msg['Message-ID']='<demo-experiment@example.com>';msg.set_content('请在周五前补充实验对比，并回复最终报告。')
    mid=store_message(db,account['id'],'demo',1,msg.as_bytes(),now())
    return {'account_id':account['id'],'message_id':mid}



# ============================================================
# 主动感知 Agent API
# ============================================================

@router.get('/mail/messages/{ident}/perception')
def get_perception(ident:str,db=Depends(database)):
    """获取一封邮件的感知结果。"""
    from .mail_perception import get_perception as gp
    result = gp(db, ident)
    if result is None:
        return {'perception': None, 'status': 'pending'}
    return {'perception': result, 'status': 'ready'}


@router.post('/mail/messages/{ident}/perception')
def request_perception(ident:str,db=Depends(database)):
    """用户显式分析或重试；仍受全局模型调用预算限制。"""
    mail=require(db,ident,'mail_message')
    require(db,mail['scope'],'mail_account')
    with db.engine.connect() as c:
        pending=c.execute(text("SELECT id FROM mail_jobs WHERE kind='perception' AND account_id=:account AND json_extract(payload,'$.message_id')=:message AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1"),
                          {'account':mail['scope'],'message':ident}).first()
    if pending:return {'job_id':pending[0]}
    return {'job_id':enqueue(db,'perception',{'message_id':ident,'manual':True},mail['scope'],priority=5)}


@router.post('/mail/messages/{ident}/perception/feedback')
def perception_feedback(ident:str,body:PerceptionFeedback,db=Depends(database)):
    """用户对感知结果的纠偏反馈，会写入记忆供下次感知参考。"""
    from .mail_perception import apply_feedback
    body.message_id = ident
    return apply_feedback(db, body)


@router.get('/mail/perception/todos')
def list_perception_todos(account_id:str='',db=Depends(database)):
    """列出感知生成的待办候选（等待用户确认）。"""
    from .mail_perception import list_pending_todos
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.extend(list_pending_todos(db, a['id']))
        return result
    require(db,account_id,'mail_account')
    return list_pending_todos(db, account_id)


@router.post('/mail/perception/todos/{ident}/confirm')
def confirm_perception_todo(ident:str,db=Depends(database)):
    """确认感知生成的待办，将其从 candidate 变为 active。"""
    row = require(db,ident,'todo')
    if row['status'] == 'active' and row['body'].get('source') == 'perception':
        return row
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'active')
    if not row['scope'].startswith('web:mail:'):
        # 迁移旧候选，保证后续完成动作使用现有工作区作用域。
        require(db,row['scope'],'mail_account')
        with db.engine.begin() as c:
            c.execute(text('UPDATE records SET scope=:scope WHERE id=:id'),
                      {'scope': 'web:mail:' + row['scope'], 'id': ident})
    from .mail_work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    return db.get(ident)


@router.post('/mail/perception/todos/{ident}/dismiss')
def dismiss_perception_todo(ident:str,db=Depends(database)):
    """忽略感知生成的待办。"""
    row = require(db,ident,'todo')
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'dismissed')
    return db.get(ident)


@router.get('/mail/perception/calendars')
def list_perception_calendars(account_id:str='',db=Depends(database)):
    """列出感知生成的日程候选（等待用户确认），包含冲突检测信息。"""
    from .mail_schedule import list_calendar_candidates
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.extend(list_calendar_candidates(db, a['id']))
        return result
    return list_calendar_candidates(db, account_id)


@router.post('/mail/perception/calendars/{ident}/confirm')
def confirm_perception_calendar(ident:str,db=Depends(database)):
    """确认感知生成的日程候选，将其从 candidate 变为 active。确认时再次检测冲突。"""
    from .mail_schedule import confirm_calendar
    row = require(db,ident,'calendar')
    account_id = row['scope'].removeprefix('web:mail:')
    require(db,account_id,'mail_account')
    return confirm_calendar(db, ident, account_id)


@router.post('/mail/perception/calendars/{ident}/dismiss')
def dismiss_perception_calendar(ident:str,db=Depends(database)):
    """忽略感知生成的日程候选。"""
    row = require(db,ident,'calendar')
    if row['status'] != 'candidate':
        raise ValueError('该日程不是候选状态')
    db.update(ident, row['body'], 'dismissed')
    return db.get(ident)


@router.get('/mail/digest')
def get_digest(account_id:str='', date:str='', db=Depends(database)):
    """获取邮件动态摘要。date 格式 YYYY-MM-DD，默认今天。"""
    from .mail_digest import get_digest, get_latest_digest
    if account_id:
        return get_digest(db, account_id, date or None) or {'message': '该日期暂无摘要'}
    # 没有指定账号时，返回所有账号的最新摘要
    accounts = rows(db,'mail_account',limit=100)
    result = []
    for a in accounts:
        d = get_latest_digest(db, a['id'])
        if d:
            result.append(d)
    return result


@router.post('/mail/digest/generate')
def generate_digest_now(account_id:str='', date:str='', db=Depends(database)):
    """立即生成指定日期的邮件摘要（手动触发）。"""
    from .mail_digest import generate_and_save_digest
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.append(generate_and_save_digest(db, a['id'], date or None))
        return result
    return generate_and_save_digest(db, account_id, date or None)


from .mail_observability import router as observability_router
router.include_router(observability_router)
from .mail_records import router as records_router
router.include_router(records_router)
