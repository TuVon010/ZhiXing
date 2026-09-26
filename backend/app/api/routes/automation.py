"""Generic Agent runs, review, evolution and record collection endpoints."""
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

router = APIRouter()
@router.post('/api/messages')
def message(body:NormalizedMessage):
    if body.source not in {'web','demo'}:
        raise ValueError('外部渠道仅允许经适配器接入')
    return {'id':ingest(body.model_dump())}

COLLECTIONS={'messages':'message','runs':'run','approvals':'approval','todos':'todo','calendar':'calendar','reminders':'reminder','audit':'audit','memory':'memory','trust':'trust','skills':'skill','parsers':'parser','evaluations':'evaluation','samples':'sample','notifications':'notification','drafts':'draft','model-calls':'model_call','parser-shadow':'parser_shadow'}
@router.get('/api/runs/{id}',response_model=RunDetail)
def run_detail(id:str):
    return trace_detail(store,id)

@router.post('/api/runs/{id}/{operation}')
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

@router.post('/api/approvals/{id}')
def approval(id:str,body:Approval):
    return approve(id,body.model_dump())

class ItemInput(BaseModel):
    tool: str
    args: dict

@router.post('/api/actions')
def action(body:ItemInput):
    p=ActionPlan(summary='用户在控制台发起',actions=[PlannedAction(tool=body.tool,args=body.args,confidence=1)])
    args=body.args
    source,conversation='web','inbox'
    target=None
    if args.get('id'):
        target=store.get(args['id'])
        if target['kind'] not in {'todo','calendar','reminder','draft'}:
            raise ValueError('目标不允许修改')
        source,conversation=target['scope'].split(':',1)
    metadata={}
    if target is not None and target['scope'].startswith('web:mail:'):
        metadata['mail_accounts']=[target['scope'][len('web:mail:'):]]
    return {'id':ingest({'message_id':uid(),'source':source,'conversation_id':conversation,'text':'控制台操作：'+body.tool,'metadata':metadata},explicit_plan=p.model_dump())}

class CandidateInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    content:str=''
    rules:dict | None=None
    evidence:list[str]=Field(default_factory=list)

@router.post('/api/{collection}/candidate')
def create_candidate(collection:str,body:CandidateInput):
    if collection not in {'skills','parsers'}:
        raise ValueError('不支持的候选集合')
    return {'id':evolution.candidate(store,COLLECTIONS[collection],**body.model_dump())}

@router.post('/api/{collection}/curate')
def curate(collection:str,body:CandidateInput):
    if collection not in {'skills','parsers'}:
        raise ValueError('不支持的候选集合')
    return {'id':evolution.curate(store,COLLECTIONS[collection],body.name,body.evidence)}

@router.post('/api/artifacts/{id}/{operation}')
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

@router.post('/api/memory')
def memory(body:MemoryInput):
    return {'id':store.insert('memory',{'content':body.content},status='candidate',scope=body.scope)}

@router.post('/api/review/{id}/{decision}')
def review(id:str,decision:str):
    row=store.get(id)
    if row['kind'] not in {'memory','trust'} or decision not in {'publish','reject','suspend'}:
        raise ValueError('无效审核')
    store.update(id,status={'publish':'published','reject':'rejected','suspend':'suspended'}[decision])
    return {'ok':True}

class SampleInput(BaseModel):
    name:str
    split:str
    message:NormalizedMessage
    expected:ActionPlan
    safety:bool=False

@router.post('/api/samples')
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

@router.post('/api/reconcile/{id}')
def reconcile(id:str,body:Reconciliation):
    from backend.app.persistence.store import encode
    row=store.get(id)
    if row['kind']!='run' or row['status'] in {'running','queued','waiting_approval'}:
        raise ValueError('只能核对已停止的运行')
    b=row['body'];outcome=b['outcomes'].get(body.action_id,{})
    if outcome.get('status') not in {'unknown','failed'}:
        raise ValueError('该动作不需要核对')
    action=next(a for a in b['actions'] if a['id']==body.action_id)
    from backend.app.agent.tools import EXTERNAL
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

@router.get('/api/{collection}')
def listing(collection:str,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0),status:str|None=None):
    if collection not in COLLECTIONS:
        raise HTTPException(404)
    return {'items':store.list(COLLECTIONS[collection],limit,offset,status),'limit':limit,'offset':offset}

