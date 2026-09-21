import json
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from .config import settings
from .db import store, now
from .runtime import ingest, approve, work_once
from .channels import poll_mail, start_feishu, Feishu, refresh_channel_settings
from .evolution import seed, curate

def notifications(db=store):
    for row in db.list('notification',status='pending'):
        b=row['body']
        if settings.mode=='demo' or not settings.notifications or not settings.feishu_owner:
            db.update(row['id'],status='local')
            continue
        # Reserve before dispatch: uncertain delivery never retries automatically.
        db.update(row['id'],status='sending')
        try:
            if b.get('approval_id'):
                approval=db.get(b['approval_id'])
                a=approval['body']
                if approval['status']!='pending':
                    db.update(row['id'],status='superseded');continue
                card={'header':{'title':{'tag':'plain_text','content':'知性待审批'}},'elements':[{'tag':'markdown','content':a['reason']+'\n'+json.dumps(a['action'],ensure_ascii=False)},{'tag':'action','actions':[{'tag':'button','text':{'tag':'plain_text','content':label},'value':{'approval_id':approval['id'],'decision':decision,'version':a['version']}} for label,decision in [('批准','approve'),('拒绝','reject')]]}]}
                Feishu().send(settings.feishu_owner,card,row['id'],card=True)
            else:
                Feishu().send(settings.feishu_owner,b['title']+'\n'+b.get('content','')+'\n'+b.get('status',''),row['id'])
            db.update(row['id'],status='sent')
        except Exception as e:
            db.update(row['id'],{**b,'error':type(e).__name__},'unknown')

def tick(db=store):
    refresh_channel_settings(db)
    for row in db.list('reminder',status='active',limit=10000):
        if datetime.fromisoformat(row['body']['due_at']).timestamp()<=time.time():
            with db.engine.begin() as c:
                db.insert('notification',{'title':'到期提醒','content':row['body']['title']},status='pending',dedupe='reminder:'+row['id'],conn=c)
                db.update(row['id'],status='completed',conn=c)
    local=datetime.now(ZoneInfo('Asia/Shanghai'))
    if local.hour>=20:
        daily='daily:'+local.date().isoformat()
        try:
            db.get(daily)
        except KeyError:
            todos=db.list('todo',status='active',limit=10000)
            db.insert('notification',{'title':'每日工作摘要','content':'\n'.join(r['body']['title'] for r in todos) or '当前没有待办任务'},id=daily,status='pending')
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

def main():
    seed(store)
    def run_loop():
        while True:
            try:
                if not work_once():
                    time.sleep(.5)
            except Exception:
                time.sleep(2)
    for _ in range(2):
        threading.Thread(target=run_loop,daemon=True).start()
    if settings.mode=='live' and settings.feishu_app_id and settings.feishu_app_secret:
        def feishu_loop():
            delay=2
            while True:
                try:
                    import asyncio
                    asyncio.set_event_loop(asyncio.new_event_loop())
                    start_feishu(store,ingest,approve)
                except Exception as e:
                    store.audit('system','CHANNEL_ERROR',channel='feishu',error=type(e).__name__)
                    time.sleep(delay);delay=min(60,delay*2)
        threading.Thread(target=feishu_loop,daemon=True).start()
    while True:
        try:
            tick()
            if settings.mode=='live':
                poll_mail(store,ingest)
        except Exception as e:
            store.audit('system','SCHEDULER_ERROR',error=type(e).__name__)
        try:
            store.get('worker-heartbeat');store.update('worker-heartbeat',{'at':now()})
        except KeyError:
            store.insert('channel',{'at':now()},id='worker-heartbeat')
        time.sleep(30)

if __name__=='__main__':
    main()
