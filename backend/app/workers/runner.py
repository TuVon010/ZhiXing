"""Background process entry point and fair scheduler for durable queues."""

import json
import threading
import time
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from backend.app.core.config import settings
from backend.app.persistence.store import store, now
from backend.app.agent.graph import work_once
from backend.app.agent.evolution import seed, curate

def notifications(db=store):
    for row in db.list('notification',status='pending'):
        db.update(row['id'],status='local')

def tick(db=store):
    for row in db.list('reminder',status='active',limit=10000):
        if datetime.fromisoformat(row['body']['due_at']).timestamp()<=time.time():
            with db.engine.begin() as c:
                db.insert('notification',{'title':'到期提醒','content':row['body']['title'],'item_id':row['id'],'run_id':row['body'].get('run_id')},scope=row['scope'],status='pending',dedupe='reminder:'+row['id'],conn=c)
                db.update(row['id'],status='completed',conn=c)
    local=datetime.now(ZoneInfo('Asia/Shanghai'))
    notifications(db)
    try:
        cfg=db.get('runtime-settings')['body']
    except KeyError:
        cfg={}
    if cfg.get('evolution_enabled') and settings.mode=='live':
        marker='evolution:'+local.date().isoformat()
        try:
            db.get(marker)
        except KeyError:
            db.insert('schedule',{},id=marker,status='running')
            failures=db.list('run',status='completed_with_attention',limit=10)
            if failures:
                try:
                    curate(db,'skill','todo-extraction',[r['id'] for r in failures])
                    db.update(marker,status='completed')
                except Exception as e:
                    db.update(marker,{'error':type(e).__name__},'failed')
            else:
                db.update(marker,status='no_evidence')
            names={r['body']['name'] for r in db.list('sample',status='confirmed',limit=10000) if r['body']['split']=='train'}
            for name in names:
                samples=[r['id'] for r in db.list('sample',status='confirmed',limit=10000) if r['body']['name']==name and r['body']['split']=='train']
                existing=any(r['body']['name']==name for r in db.list('parser',status='candidate'))
                if len(samples)>=20 and not existing:
                    try:
                        curate(db,'parser',name,samples[:50])
                    except Exception as e:
                        db.audit(marker,'PARSER_CURATOR_FAILED',error=type(e).__name__)

def work_cycle(db=store):
    """Give each queue one turn: a busy mail queue cannot starve approved actions."""
    from backend.app.workers import mail_jobs as mail_worker
    action_work = work_once(db)
    mail_work = mail_worker.work_once(db)
    return action_work or mail_work


def main():
    os.environ['ZHIXING_MAIL_WORKER']='1'
    seed(store)
    from backend.app.workers import mail_jobs as mail_worker
    from backend.app.modules.mail.repository import initialize
    initialize(store)
    def run_loop():
        while True:
            try:
                if not work_cycle(store):
                    time.sleep(.5)
            except Exception:
                time.sleep(2)
    for _ in range(2):
        threading.Thread(target=run_loop,daemon=True).start()
    while True:
        try:
            tick()
            if settings.mode=='live':
                mail_worker.schedule(store)
        except Exception as e:
            store.audit('system','SCHEDULER_ERROR',error=type(e).__name__)
        try:
            store.get('worker-heartbeat');store.update('worker-heartbeat',{'at':now()})
        except KeyError:
            store.insert('channel',{'at':now()},id='worker-heartbeat')
        time.sleep(5)

if __name__=='__main__':
    main()
