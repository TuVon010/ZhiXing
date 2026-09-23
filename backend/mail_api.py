from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,Field,field_validator,ConfigDict
from sqlalchemy import text
import json
from .mail_models import MailAccount,ImportRequest,SearchRequest,DraftInput,SessionInput,TurnInput,PerceptionFeedback
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
        items.extend(db.list('todo',limit=200,scope='web:mail:'+aid))
        items.extend(db.list('reminder',limit=200,scope='web:mail:'+aid))
        items.extend(db.list('calendar',limit=200,scope='web:mail:'+aid))
    return {'items':sorted(items,key=lambda r:r['updated_at'],reverse=True)}


@router.post('/mail/followups')
def create_followup(body:dict,db=Depends(database)):
    from .mail_models import FollowupInput
    value=FollowupInput.model_validate(body)
    require(db,value.account_id,'mail_account')
    scope='web:mail:'+value.account_id
    ident=db.insert('todo',{'title':value.title,'owner':'self','deadline':value.deadline.isoformat() if value.deadline else None,'origin':'mail_workspace'},scope=scope)
    db.audit('system','MAIL_FOLLOWUP_CREATED',todo_id=ident,account_id=value.account_id)
    return db.get(ident)


@router.get('/mail/approvals')
def mail_approvals(account_id:str|None=None,db=Depends(database)):
    items=[]
    allowed=[account_id] if account_id else [r['id'] for r in rows(db,'mail_account',limit=1000)]
    for approval in rows(db,'approval',limit=1000):
        if approval['status'] not in {'pending','approved','rejected'}:continue
        try:run=require(db,approval['body']['run_id'],'run')
        except (KeyError,ValueError):continue
        selected=run['body']['message'].get('metadata',{}).get('mail_accounts',[])
        if set(selected)&set(allowed):items.append(approval)
    return {'items':sorted(items,key=lambda r:r['updated_at'],reverse=True)}


@router.get('/mail/accounts')
def accounts(db=Depends(database)):
    return {'items':[public_account(r) for r in rows(db,'mail_account',limit=1000)]}


@router.post('/mail/accounts')
def create_account(body:MailAccount,db=Depends(database)):
    return save_account(db,body)


@router.post('/mail/accounts/{ident}')
def update_account(ident:str,body:MailAccount,db=Depends(database)):
    return save_account(db,body,ident)


@router.post('/mail/accounts/{ident}/test')
def connection_test(ident:str,db=Depends(database)):
    require(db,ident,'mail_account')
    return {'job_id':enqueue(db,'connection_test',{},ident,priority=0)}


@router.post('/mail/accounts/{ident}/sync')
def sync(ident:str,db=Depends(database)):
    require(db,ident,'mail_account')
    return {'job_id':enqueue(db,'sync',{},ident,priority=10)}


class Toggle(BaseModel):
    enabled: bool


@router.post('/mail/accounts/{ident}/enabled')
def enable(ident:str,body:Toggle,db=Depends(database)):
    row=require(db,ident,'mail_account');db.update(ident,{**row['body'],'enabled':body.enabled})
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
    require(db,body.account_id,'mail_account')
    return {'job_id':enqueue(db,'preview',body.model_dump(mode='json'),body.account_id,priority=5)}


@router.post('/mail/imports')
def create_import(body:ImportRequest,db=Depends(database)):
    require(db,body.account_id,'mail_account')
    ident=db.insert('mail_import',{**body.model_dump(mode='json'),'last_uid':0,'imported':0,'scanned':0},scope=body.account_id,status='running')
    enqueue(db,'import',{'import_id':ident},body.account_id,priority=15)
    return db.get(ident)


@router.get('/mail/imports')
def imports(account_id:str|None=None,db=Depends(database)):
    return {'items':rows(db,'mail_import',[account_id] if account_id else None)}


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
def messages(account_id:str|None=None,status:str|None=None,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),db=Depends(database)):
    items=rows(db,'mail_message',[account_id] if account_id else None,status,limit,offset)
    # The list is a summary; full sources are loaded only on opening a message.
    return {'items':[{**r,'body':{k:v for k,v in r['body'].items() if k not in {'raw_text','headers','attachments'}}} for r in items],'limit':limit,'offset':offset}


@router.get('/mail/messages/{ident}')
def message(ident:str,db=Depends(database)):
    return require(db,ident,'mail_message')


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
    return {'items':rows(db,'mail_draft',[account_id] if account_id else None)}


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


@router.post('/mail/drafts/{ident}/submit')
def submit(ident:str,body:Version,db=Depends(database)):
    from .mail_assistant import submit_draft
    return submit_draft(db,ident,body.version)


@router.post('/assistant/sessions')
def session(body:SessionInput,db=Depends(database)):
    from .mail_assistant import create_session
    return create_session(db,body)


@router.get('/assistant/sessions')
def sessions(db=Depends(database)):
    return {'items':rows(db,'assistant_session')}


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
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'active')
    return db.get(ident)


@router.post('/mail/perception/todos/{ident}/dismiss')
def dismiss_perception_todo(ident:str,db=Depends(database)):
    """忽略感知生成的待办。"""
    row = require(db,ident,'todo')
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'dismissed')
    return db.get(ident)


from .mail_observability import router as observability_router
router.include_router(observability_router)
