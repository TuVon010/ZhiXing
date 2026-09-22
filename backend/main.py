import asyncio
import json
import secrets
import mimetypes
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Request, Response, Query
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text
from .config import settings, ROOT
from .db import store, now, uid
from .schemas import NormalizedMessage, Approval, ActionPlan, PlannedAction
from .runtime import ingest, approve
from . import evolution
from .tracing import RunDetail, detail as trace_detail

SESSION = secrets.token_urlsafe(32)

@asynccontextmanager
async def lifespan(app):
    evolution.seed(store)
    yield

app=FastAPI(title='知性 ZhiXing Personal Agent',version='1.0.0',lifespan=lifespan)

@app.middleware('http')
async def guard(request:Request,call_next):
    host=request.headers.get('host','').split(':')[0]
    if host not in {'127.0.0.1','localhost','testserver'}:
        return Response('Forbidden host',403)
    if request.url.path.startswith('/api/') and request.url.path not in {'/api/session','/api/health'}:
        if not secrets.compare_digest(request.cookies.get('zhixing_session',''),SESSION):
            return Response('请刷新本地页面建立会话',401)
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('origin')
            if origin and origin not in {'http://127.0.0.1:8000','http://localhost:8000','http://127.0.0.1:5173','http://localhost:5173'}:
                return Response('Forbidden origin',403)
            if request.headers.get('x-zhixing-local')!='1':
                return Response('Missing local request marker',403)
    try:
        return await call_next(request)
    except (ValueError,KeyError) as e:
        return Response(json.dumps({'detail':str(e)},ensure_ascii=False),400,media_type='application/json')

@app.exception_handler(ValueError)
async def value_error(request,e):
    return Response(json.dumps({'detail':str(e)},ensure_ascii=False),400,media_type='application/json')

@app.exception_handler(KeyError)
async def missing(request,e):
    return Response(json.dumps({'detail':'记录不存在'},ensure_ascii=False),404,media_type='application/json')

@app.get('/api/session')
def session(response:Response):
    response.set_cookie('zhixing_session',SESSION,httponly=True,samesite='strict',max_age=86400)
    return {'mode':settings.mode}

@app.get('/api/health')
def health():
    try:
        heartbeat=store.get('worker-heartbeat')['body']['at']
    except KeyError:
        heartbeat=None
    from datetime import datetime,timezone
    alive=bool(heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds()<120)
    return {'status':'ok','mode':settings.mode,'worker_heartbeat':heartbeat,'worker_alive':alive}

@app.post('/api/messages')
def message(body:NormalizedMessage):
    if body.source not in {'web','demo'}:
        raise ValueError('外部渠道仅允许经适配器接入')
    return {'id':ingest(body.model_dump())}

COLLECTIONS={'messages':'message','runs':'run','approvals':'approval','todos':'todo','calendar':'calendar','reminders':'reminder','audit':'audit','memory':'memory','trust':'trust','skills':'skill','parsers':'parser','evaluations':'evaluation','samples':'sample','notifications':'notification','pairings':'pairing','drafts':'draft','model-calls':'model_call','parser-shadow':'parser_shadow'}

@app.get('/api/stream')
async def stream(request:Request):
    async def events():
        last=None
        while not await request.is_disconnected():
            runs=store.list('run',limit=50)
            signature=json.dumps([(r['id'],r['updated_at'],r['status']) for r in runs])
            if signature!=last:
                yield 'data: '+json.dumps({'event':'refresh'})+'\n\n';last=signature
            else:
                yield ': keepalive\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

@app.get('/api/settings')
def get_settings():
    from .channels import refresh_channel_settings
    refresh_channel_settings(store)
    try:
        local=store.get('runtime-settings')['body']
    except KeyError:
        local={}
    return {'mode':settings.mode,'model_name':settings.model_name,'model_base_url':settings.model_base_url,'model_configured':bool(settings.model_api_key and settings.model_name),'feishu_configured':bool(settings.feishu_app_id and settings.feishu_app_secret),'feishu_owner':settings.feishu_owner,'feishu_groups':settings.feishu_groups,'mail_configured':bool(settings.mail_address and settings.mail_password),'notifications':settings.notifications,'daily_call_limit':settings.model_daily_calls,'evolution_enabled':local.get('evolution_enabled',False),'data_dir':str(store.path.parent),'jev_mode':settings.jev_mode,'jev_configured':bool(settings.jev_api_key),'jev_model':settings.jev_model,'jev_daily_calls':settings.jev_daily_calls}

class LocalSettings(BaseModel):
    evolution_enabled: bool = False
    feishu_groups: list[str] = Field(default_factory=list)

@app.post('/api/settings')
def save_settings(body:LocalSettings):
    if body.evolution_enabled and not (settings.model_api_key and settings.model_name):
        raise ValueError('启用自动演进前请配置模型和调用预算')
    try:
        store.get('runtime-settings');store.update('runtime-settings',body.model_dump())
    except KeyError:
        store.insert('setting',body.model_dump(),id='runtime-settings')
    return {'saved':True}

@app.get('/api/stats')
def stats():
    import statistics
    with store.engine.connect() as c:
        counts=c.execute(text('SELECT kind,status,count(*) AS count FROM records GROUP BY kind,status')).mappings().all()
    runs=store.list('run',limit=10000)
    durations=[r['body']['duration_ms'] for r in runs if r['body'].get('duration_ms') is not None]
    calls=store.list('model_call',limit=10000)
    usages=[r['body'].get('usage',{}) for r in calls]
    observations=store.list('parser_observation',limit=10000)
    return {'counts':[dict(r) for r in counts],'average_latency_ms':statistics.mean(durations) if durations else None,'model_calls':len(calls),'tokens':sum(u.get('total_tokens',0) for u in usages),'cost':sum(r['body']['cost'] for r in calls) if calls and all(r['body'].get('cost') is not None for r in calls) else None,'parser_hit_rate':sum(bool(r['body']['hit']) for r in observations)/len(observations) if observations else None}

@app.get('/api/runs/{id}',response_model=RunDetail)
def run_detail(id:str):
    return trace_detail(store,id)

@app.post('/api/runs/{id}/{operation}')
def run_command(id:str,operation:str):
    row=store.get(id)
    if row['kind']!='run':
        raise ValueError('不是运行记录')
    if operation=='cancel':
        store.update(id,status='cancelled')
        store.audit(id,'WORKFLOW_CANCELLED',status='cancelled')
        for a in store.list('approval',status='pending',limit=10000):
            if a['body']['run_id']==id:
                store.update(a['id'],status='cancelled')
    elif operation=='retry' and row['status']=='failed':
        with store.engine.begin() as c:
            store.update(id,status='queued',conn=c)
            c.execute(text("UPDATE jobs SET status='queued',lease_until=0 WHERE run_id=:id AND status='failed'"),{'id':id})
        store.audit(id,'WORKFLOW_RETRY_REQUESTED')
    elif operation=='replay':
        return {'id':ingest({**row['body']['message'],'message_id':uid()},replay=True)}
    else:
        raise ValueError('当前状态不支持该操作')
    return {'ok':True}

@app.post('/api/approvals/{id}')
def approval(id:str,body:Approval):
    return approve(id,body.model_dump())

class ItemInput(BaseModel):
    tool: str
    args: dict

@app.post('/api/actions')
def action(body:ItemInput):
    p=ActionPlan(summary='用户在控制台发起',actions=[PlannedAction(tool=body.tool,args=body.args,confidence=1)])
    args=body.args
    source,conversation='web','inbox'
    if args.get('id'):
        target=store.get(args['id'])
        if target['kind'] not in {'todo','calendar','reminder','draft'}:
            raise ValueError('目标不允许修改')
        source,conversation=target['scope'].split(':',1)
    return {'id':ingest({'message_id':uid(),'source':source,'conversation_id':conversation,'text':'控制台操作：'+body.tool},explicit_plan=p.model_dump())}

class CandidateInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    content:str=''
    rules:dict | None=None
    evidence:list[str]=Field(default_factory=list)

@app.post('/api/{collection}/candidate')
def create_candidate(collection:str,body:CandidateInput):
    if collection not in {'skills','parsers'}:
        raise ValueError('不支持的候选集合')
    return {'id':evolution.candidate(store,COLLECTIONS[collection],**body.model_dump())}

@app.post('/api/{collection}/curate')
def curate(collection:str,body:CandidateInput):
    if collection not in {'skills','parsers'}:
        raise ValueError('不支持的候选集合')
    return {'id':evolution.curate(store,COLLECTIONS[collection],body.name,body.evidence)}

@app.post('/api/artifacts/{id}/{operation}')
def artifact(id:str,operation:str):
    if operation=='evaluate':
        return evolution.evaluate(store,id)
    if operation=='shadow':
        return evolution.shadow(store,id)
    if operation in {'publish','rollback'}:
        evolution.publish(store,id,operation=='rollback')
    elif operation=='reject':
        row=store.get(id)
        if row['kind'] not in {'skill','parser'} or row['status']=='published':
            raise ValueError('不能拒绝该对象')
        store.update(id,status='rejected')
    else:
        raise ValueError('无效操作')
    return {'ok':True}

class MemoryInput(BaseModel):
    content:str=Field(min_length=1,max_length=4000)
    scope:str='web:inbox'

@app.post('/api/memory')
def memory(body:MemoryInput):
    return {'id':store.insert('memory',{'content':body.content},status='candidate',scope=body.scope)}

@app.post('/api/review/{id}/{decision}')
def review(id:str,decision:str):
    row=store.get(id)
    if row['kind'] not in {'memory','trust','pairing'} or decision not in {'publish','reject','suspend'}:
        raise ValueError('无效审核')
    if row['kind']=='pairing':
        if decision=='publish':
            try:
                store.get('feishu-binding');store.update('feishu-binding',{'open_id':row['body']['open_id']})
            except KeyError:
                store.insert('setting',{'open_id':row['body']['open_id']},id='feishu-binding')
            settings.feishu_owner=row['body']['open_id']
    store.update(id,status={'publish':'published','reject':'rejected','suspend':'suspended'}[decision])
    return {'ok':True}

class SampleInput(BaseModel):
    name:str
    split:str
    message:NormalizedMessage
    expected:ActionPlan
    safety:bool=False

@app.post('/api/samples')
def sample(body:SampleInput):
    if body.split not in {'train','holdout','shadow'}:
        raise ValueError('无效数据集划分')
    import hashlib
    fingerprint=hashlib.sha256(body.message.text.strip().encode()).hexdigest()
    return {'id':store.insert('sample',body.model_dump(),status='confirmed',dedupe='sample:'+fingerprint)}

class Reconciliation(BaseModel):
    action_id:str
    executed:bool
    evidence:str=Field(min_length=5,max_length=2000)

@app.post('/api/reconcile/{id}')
def reconcile(id:str,body:Reconciliation):
    from .db import encode
    row=store.get(id)
    if row['kind']!='run' or row['status'] in {'running','queued','waiting_approval'}:
        raise ValueError('只能核对已停止的运行')
    b=row['body'];outcome=b['outcomes'].get(body.action_id,{})
    if outcome.get('status') not in {'unknown','failed'}:
        raise ValueError('该动作不需要核对')
    action=next(a for a in b['actions'] if a['id']==body.action_id)
    from .tools import EXTERNAL
    if action['tool'] not in EXTERNAL:
        raise ValueError('仅用于外部写入结果核对')
    result={'status':'completed' if body.executed else 'not_executed','data':{'human_verified':True,'evidence':body.evidence}}
    with store.engine.begin() as c:
        changed=c.execute(text("UPDATE ledger SET status=:status,result=:result,updated_at=:at WHERE action_id=:id AND status='executing'"),{'status':result['status'],'result':encode(result['data']),'at':now(),'id':body.action_id}).rowcount
        if not changed:
            raise ValueError('动作已核对或没有外部提交记录')
        outcomes={**b['outcomes'],body.action_id:result}
        store.update(id,{**b,'outcomes':outcomes},'completed' if all(o['status']=='completed' for o in outcomes.values()) else 'completed_with_attention',conn=c)
    store.audit(id,'EXTERNAL_RECONCILED',**body.model_dump())
    if not body.executed:
        fresh={**action,'id':'0','depends_on':[]}
        m={**b['message'],'message_id':uid(),'text':'人工核对确认未执行，重新申请：'+action['tool']}
        return {'retry_run_id':ingest(m,explicit_plan={'summary':'人工核对后重新申请','actions':[fresh]})}
    return {'ok':True}

class SyncResolution(BaseModel):
    use_local:bool

@app.post('/api/sync-resolution/{id}')
def resolve_sync(id:str,body:SyncResolution):
    from .channels import Feishu
    row=store.get(id);b=row['body']
    if row['kind'] not in {'todo','calendar'} or b.get('sync_status') not in {'conflict','unknown'}:
        raise ValueError('没有可处理的同步冲突')
    if not body.use_local:
        raise ValueError('首版不反向覆盖本地数据；请先手工修改本地内容，再确认重试同步')
    endpoint='/task/v2/tasks/'+b['external_id'] if row['kind']=='todo' else '/calendar/v4/calendars/'+settings.feishu_calendar_id+'/events/'+b['external_id']
    snapshot=Feishu().request('GET',endpoint).get('task' if row['kind']=='todo' else 'event',{})
    store.update(id,{**b,'external_snapshot':snapshot,'sync_status':'local'})
    return action(ItemInput(tool='sync_todo' if row['kind']=='todo' else 'sync_calendar',args={'id':id}))

@app.post('/api/mail/import')
def import_mail():
    from .channels import poll_mail
    poll_mail(store,ingest,historical=True)
    return {'ok':True}

@app.get('/api/{collection}')
def listing(collection:str,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),status:str|None=None):
    if collection not in COLLECTIONS:
        raise HTTPException(404)
    return {'items':store.list(COLLECTIONS[collection],limit,offset,status),'limit':limit,'offset':offset}

mimetypes.init()
mimetypes.add_type('text/javascript','.js')
mimetypes.add_type('text/css','.css')
DIST=ROOT/'frontend'/'dist'
if DIST.exists():
    app.mount('/assets',StaticFiles(directory=DIST/'assets'),name='assets')
    @app.get('/zhixing-mark.svg',include_in_schema=False)
    def brand_icon():
        return FileResponse(DIST/'zhixing-mark.svg',media_type='image/svg+xml')
    @app.get('/')
    def index():
        return FileResponse(DIST/'index.html')
