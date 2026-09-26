"""Session, health, live refresh and runtime settings endpoints."""
import json
from fastapi import APIRouter, HTTPException, Request, Response, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from backend.app.core.config import settings
from backend.app.persistence.store import store, now, uid
from backend.app.agent.schemas import NormalizedMessage, Approval, ActionPlan, PlannedAction
from backend.app.agent.graph import ingest, approve
from backend.app.agent import evolution
from backend.app.observability.tracing import RunDetail, detail as trace_detail
from backend.app.observability import billing
from backend.app.core.security import SESSION_TOKEN

router = APIRouter()
from backend.app.api.routes.pricing import billing_summary
import asyncio
@router.get('/api/session')
def session(response:Response):
    response.set_cookie('zhixing_session',SESSION_TOKEN,httponly=True,samesite='strict',max_age=86400)
    return {'mode':settings.mode}

@router.get('/api/health')
def health():
    try:
        heartbeat=store.get('worker-heartbeat')['body']['at']
    except KeyError:
        heartbeat=None
    from datetime import datetime,timezone
    alive=bool(heartbeat and (datetime.now(timezone.utc)-datetime.fromisoformat(heartbeat)).total_seconds()<120)
    return {'status':'ok','mode':settings.mode,'worker_heartbeat':heartbeat,'worker_alive':alive}

@router.get('/api/stream')
async def stream(request:Request):
    async def events():
        last=None
        while not await request.is_disconnected():
            with store.engine.connect() as c:
                revision=c.execute(text("""SELECT max(updated_at),count(*) FROM records WHERE kind IN
                    ('run','assistant_turn','mail_message','todo','calendar','reminder','mail_followup','notification','mail_draft')""")).first()
                jobs=c.execute(text("SELECT max(updated_at),count(*) FROM mail_jobs")).first()
            signature=json.dumps([list(revision),list(jobs)])
            if signature!=last:
                yield 'data: '+json.dumps({'event':'refresh'})+'\n\n';last=signature
            else:
                yield ': keepalive\n\n'
            await asyncio.sleep(2)
    return StreamingResponse(events(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})

@router.get('/api/settings')
def get_settings():
    try:
        local=store.get('runtime-settings')['body']
    except KeyError:
        local={}
    return {'mode':settings.mode,'model_name':settings.model_name,'model_base_url':settings.model_base_url,'model_configured':bool(settings.model_api_key and settings.model_name),'mail_configured':bool(settings.mail_address and settings.mail_password),'daily_call_limit':settings.model_daily_calls,'evolution_enabled':local.get('evolution_enabled',False),'data_dir':str(store.path.parent)}

class LocalSettings(BaseModel):
    evolution_enabled: bool = False

@router.post('/api/settings')
def save_settings(body:LocalSettings):
    if body.evolution_enabled and not (settings.model_api_key and settings.model_name):
        raise ValueError('启用自动演进前请配置模型和调用预算')
    try:
        current=store.get('runtime-settings')['body'];store.update('runtime-settings',{**current,**body.model_dump(exclude_unset=True)})
    except KeyError:
        store.insert('setting',body.model_dump(),id='runtime-settings')
    return {'saved':True}

@router.get('/api/stats')
def stats():
    import statistics
    with store.engine.connect() as c:
        counts=c.execute(text('SELECT kind,status,count(*) AS count FROM records GROUP BY kind,status')).mappings().all()
    runs=store.list('run',limit=10000)
    durations=[r['body']['duration_ms'] for r in runs if r['body'].get('duration_ms') is not None]
    calls=store.list('model_call',limit=10000)
    usages=[r['body'].get('usage',{}) for r in calls]
    observations=store.list('parser_observation',limit=10000)
    return {'counts':[dict(r) for r in counts],'average_latency_ms':statistics.mean(durations) if durations else None,'model_calls':len(calls),'tokens':sum(u.get('total_tokens',0) for u in usages),'cost':None,'billing':billing_summary(),'parser_hit_rate':sum(bool(r['body']['hit']) for r in observations)/len(observations) if observations else None}


