import hashlib
import json
import sqlite3
import time
from pathlib import Path
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command
from langgraph.checkpoint.sqlite import SqliteSaver
from .db import store, uid, now, encode
from .schemas import NormalizedMessage, ZhiXingState, Approval, ActionPlan
from .planner import plan, trace_run
from .policy import decide, risk, key, suspend, suggest
from .tools import execute, validate
from .channels import ExternalUnknown

def ingest(message, db=store, explicit_plan=None, replay=False):
    m = NormalizedMessage.model_validate(message).model_dump()
    m['timestamp'] = m['timestamp'] or now()
    scope = m['source']+':'+m['conversation_id']
    dedupe = m['source']+':'+m['message_id']
    rid = uid()
    versions = {kind+'s':[r['id'] for r in db.list(kind,status='published')] for kind in ['skill','parser']}
    versions['policy'] = 1
    versions['trust'] = [r['id'] for r in db.list('trust',status='published')]
    try:
        with db.engine.begin() as c:
            mid = db.insert('message',m,scope=scope,dedupe=dedupe,conn=c)
            db.insert('run',{'message':m,'message_record_id':mid,'versions':versions,'explicit_plan':explicit_plan,'outcomes':{},'replay':replay},status='queued',scope=scope,id=rid,conn=c)
            c.execute(text("INSERT INTO jobs(id,run_id,scope,status,created_at) VALUES(:id,:run,:scope,'queued',:at)"),{'id':uid(),'run':rid,'scope':scope,'at':now()})
    except IntegrityError:
        with db.engine.connect() as c:
            row = c.execute(text('SELECT id FROM records WHERE dedupe=:key'),{'key':dedupe}).first()
        if not row:
            raise
        for run in db.list('run',limit=10000):
            if run['body']['message_record_id']==row[0]:
                return run['id']
        raise ValueError('事件存在但运行记录缺失')
    db.audit(rid,'MESSAGE_RECEIVED',source=m['source'])
    return rid

def approve(approval_id, payload, db=store):
    command = Approval.model_validate(payload)
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        row = db.get(approval_id,c)
        if row['kind'] != 'approval':
            raise ValueError('不是审批记录')
        b = row['body']
        if row['status'] != 'pending' or command.version != b['version']:
            raise ValueError('审批已处理或版本过期')
        run = db.get(b['run_id'],c)
        if run['status'] == 'cancelled':
            raise ValueError('运行已取消')
        if command.decision == 'edit':
            if command.args is None:
                raise ValueError('修改需要提供完整参数')
            edited = {**b['action'],'args':command.args,'clarification':None,'confidence':1}
            issue = validate(edited)
            if issue:
                raise ValueError(issue)
            db.update(approval_id,{**b,'action':edited,'version':b['version']+1,'edited':True},conn=c)
            c.commit()
            suspend(db,edited)
            db.audit(b['run_id'],'APPROVAL_EDITED',approval_id=approval_id,action_id=edited['id'],version=b['version']+1,input=command.args)
            return {'status':'pending','version':b['version']+1}
        db.update(approval_id,{**b,'decision':command.decision},'approved' if command.decision=='approve' else 'rejected',conn=c)
        decision_id = db.insert('decision',{'approval_id':approval_id,'key':key(b['action']),'risk':risk(b['action']),'decision':command.decision,'action_id':b['action']['id'],'result':'pending'},conn=c)
        c.execute(text("UPDATE jobs SET status=CASE WHEN status='running' THEN 'running' ELSE 'queued' END,resume=:resume WHERE run_id=:run"),{'run':b['run_id'],'resume':encode({'approval_id':approval_id,'decision_id':decision_id})})
        db.update(b['run_id'],status='queued',conn=c)
        c.commit()
    db.audit(b['run_id'],'APPROVAL_DECIDED',approval_id=approval_id,decision=command.decision)
    if command.decision=='reject':
        suspend(db,b['action'])
    return {'status':'queued'}

class Runtime:
    def __init__(self, db=store):
        self.db = db
        self.connection = sqlite3.connect(str(db.path.parent/'checkpoints.db'),check_same_thread=False,timeout=30)
        self.connection.execute('PRAGMA journal_mode=WAL')
        saver = SqliteSaver(self.connection)
        g = StateGraph(ZhiXingState)
        for name, fn in [('understand',self.understand),('risk',self.gate),('approval',self.approval),('execute',self.execute),('advance',self.advance),('finish',self.finish)]:
            g.add_node(name,fn)
        g.add_edge(START,'understand')
        g.add_conditional_edges('understand',lambda s:'risk' if s['actions'] else 'finish')
        g.add_conditional_edges('risk',lambda s:'approval' if s['decision'] in {'ASK','CLARIFY'} else 'execute')
        g.add_edge('approval','execute')
        g.add_edge('execute','advance')
        g.add_conditional_edges('advance',lambda s:'risk' if s['index']<len(s['actions']) else 'finish')
        g.add_edge('finish',END)
        self.graph = g.compile(checkpointer=saver)

    def close(self):
        self.connection.close()

    def understand(self,s):
        start = time.monotonic()
        run = self.db.get(s['run_id'])
        self.db.audit(s['run_id'],'UNDERSTAND_STARTED',message_id=run['body']['message_record_id'],versions=s['versions'])
        token=trace_run.set(s['run_id'])
        try:
            p = ActionPlan.model_validate(run['body']['explicit_plan']) if run['body'].get('explicit_plan') else plan(self.db,s['message'],s['versions'])
        finally:
            trace_run.reset(token)
        mapping = {a.id:s['run_id']+'-'+a.id for a in p.actions}
        actions = [{**a.model_dump(),'id':mapping[a.id],'depends_on':[mapping[d] for d in a.depends_on]} for a in p.actions]
        for a in actions:
            ref=a['args'].get('id')
            if isinstance(ref,str) and ref.startswith('@'):
                if ref[1:] not in mapping or mapping[ref[1:]] not in a['depends_on']:
                    raise ValueError('动作结果引用必须声明依赖')
                a['args']['id']='@'+mapping[ref[1:]]
        self.db.update(s['run_id'],{**run['body'],'summary':p.summary,'actions':actions},'running')
        self.db.audit(s['run_id'],'ACTION_PLANNED',actions=actions,latency_ms=round((time.monotonic()-start)*1000))
        return {'actions':actions,'index':0,'outcomes':{}}

    def gate(self,s):
        action = s['actions'][s['index']]
        reference=action['args'].get('id')
        if isinstance(reference,str) and reference.startswith('@'):
            action['args']['id']=s['outcomes'].get(reference[1:],{}).get('data',{}).get('id')
        if self.db.get(s['run_id'])['status']=='cancelled':
            decision='DENY'
        elif any(s['outcomes'].get(d,{}).get('status')!='completed' for d in action['depends_on']):
            decision='DENY'
        else:
            issue = validate(action)
            if issue:
                action['clarification']=issue
            decision = decide(self.db,action,s['versions'].get('trust',[]))
        self.db.audit(s['run_id'],'RISK_CHECKED',action_id=action['id'],risk=risk(action),decision=decision)
        if decision in {'ASK','CLARIFY'}:
            approval_id = 'approval-'+action['id']
            try:
                self.db.get(approval_id)
            except KeyError:
                self.db.insert('approval',{'run_id':s['run_id'],'action':action,'risk':risk(action),'reason':action.get('clarification') or '需要人工确认','version':1},status='pending',id=approval_id)
                self.db.insert('notification',{'title':'待审批','run_id':s['run_id'],'approval_id':approval_id},status='pending')
                self.db.audit(s['run_id'],'APPROVAL_REQUESTED',approval_id=approval_id,action_id=action['id'],risk=risk(action),input=action['args'])
        return {'decision':decision,'actions':s['actions']}

    def approval(self,s):
        action = s['actions'][s['index']]
        value = interrupt({'approval_id':'approval-'+action['id']})
        row = self.db.get(value['approval_id'])
        if row['body']['action']['id'] != action['id'] or row['status'] not in {'approved','rejected'}:
            raise ValueError('无效的审批恢复')
        edited = row['body']['action']
        actions = list(s['actions']); actions[s['index']]=edited
        # Human approval is tied to persisted immutable version, never client-provided tool names.
        decision='ALLOW' if row['status']=='approved' and not validate(edited) and not edited.get('clarification') else 'DENY'
        return {'actions':actions,'decision':decision}

    def execute(self,s):
        started=time.monotonic()
        a = s['actions'][s['index']]
        result = {'status':'denied'}
        run = self.db.get(s['run_id'])
        if s['decision']=='ALLOW' and run['status']!='cancelled':
            try:
                self.db.audit(s['run_id'],'TOOL_CALLED',action_id=a['id'],tool=a['tool'],input=a['args'])
                result = {'status':'completed','data':execute(self.db,a,s['run_id'],run['scope'],dry_run=run['body'].get('replay',False))}
            except ExternalUnknown as e:
                result = {'status':'unknown','error':str(e)}
                suspend(self.db,a)
            except Exception as e:
                result = {'status':'failed','error':str(e)}
                suspend(self.db,a)
        self.db.audit(s['run_id'],'TOOL_RESULT',action_id=a['id'],latency_ms=round((time.monotonic()-started)*1000),**result)
        for d in self.db.list('decision',limit=10000):
            if d['body']['action_id']==a['id']:
                self.db.update(d['id'],{**d['body'],'result':result['status']})
        outcomes={**s['outcomes'],a['id']:result}
        self.db.update(s['run_id'],{**run['body'],'outcomes':outcomes,'actions':s['actions']})
        return {'outcomes':outcomes}

    def advance(self,s):
        return {'index':s['index']+1}

    def finish(self,s):
        from datetime import datetime
        row=self.db.get(s['run_id'])
        status = 'completed' if all(v['status']=='completed' for v in s['outcomes'].values()) else 'completed_with_attention'
        if row['status']=='cancelled':
            status='cancelled'
        duration=(datetime.fromisoformat(now())-datetime.fromisoformat(row['created_at'])).total_seconds()*1000
        self.db.update(s['run_id'],{**row['body'],'duration_ms':round(duration)},status=status)
        self.db.audit(s['run_id'],'WORKFLOW_FINISHED',status=status)
        self.db.insert('notification',{'title':'执行结果','run_id':s['run_id'],'status':status},status='pending')
        suggest(self.db)
        return {}

    def run(self, rid, resume=None):
        row=self.db.get(rid)
        config={'configurable':{'thread_id':rid},'recursion_limit':150}
        snapshot=self.graph.get_state(config)
        if resume:
            self.db.audit(rid,'WORKFLOW_RESUMED',approval_id=resume.get('approval_id'))
        value=Command(resume=resume) if resume else None if snapshot.values else {'run_id':rid,'message':row['body']['message'],'versions':row['body']['versions']}
        self.graph.invoke(value,config)
        snapshot=self.graph.get_state(config)
        if snapshot.next:
            # Do not overwrite a decision concurrently submitted just after interrupt.
            with self.db.engine.begin() as c:
                pending=c.execute(text("SELECT COUNT(*) FROM records WHERE kind='approval' AND status='pending' AND json_extract(body,'$.run_id')=:run"),{'run':rid}).scalar_one()
                if pending:
                    self.db.update(rid,status='waiting_approval',conn=c)
            if pending:
                self.db.audit(rid,'WORKFLOW_PAUSED',status='waiting_approval')
            return 'waiting' if pending else 'queued'
        return 'done'

def work_once(db=store):
    import threading
    timestamp=time.time()
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        c.execute(text("UPDATE jobs SET status='queued' WHERE status='running' AND lease_until<:t"),{'t':timestamp})
        job=c.execute(text("SELECT * FROM jobs j WHERE status='queued' AND NOT EXISTS(SELECT 1 FROM jobs other WHERE other.scope=j.scope AND other.status='running') ORDER BY created_at LIMIT 1")).mappings().first()
        if not job:
            c.rollback(); return False
        job=dict(job)
        c.execute(text("UPDATE jobs SET status='running',lease_until=:lease,attempts=attempts+1 WHERE id=:id"),{'id':job['id'],'lease':timestamp+120})
        c.commit()
    stop=threading.Event()
    def heartbeat():
        while not stop.wait(30):
            with db.engine.begin() as c:
                c.execute(text("UPDATE jobs SET lease_until=:lease WHERE id=:id AND status='running'"),{'id':job['id'],'lease':time.time()+120})
    thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
    rt=Runtime(db)
    try:
        if db.get(job['run_id'])['status']=='cancelled':
            status='done'
        else:
            status=rt.run(job['run_id'],json.loads(job['resume']) if job['resume'] else None)
        with db.engine.begin() as c:
            current=c.execute(text('SELECT resume FROM jobs WHERE id=:id'),{'id':job['id']}).scalar_one()
            new_resume=current if current!=job['resume'] else None
            c.execute(text("UPDATE jobs SET status=:status,lease_until=0,resume=:resume WHERE id=:id AND status='running'"),{'id':job['id'],'status':'queued' if new_resume else status,'resume':new_resume})
    except Exception as e:
        db.audit(job['run_id'],'WORKFLOW_FAILED',error=type(e).__name__,detail=str(e)[:500])
        db.update(job['run_id'],status='failed')
        with db.engine.begin() as c:
            c.execute(text("UPDATE jobs SET status='failed',lease_until=0 WHERE id=:id"),{'id':job['id']})
    finally:
        stop.set();thread.join();rt.close()
    return True
