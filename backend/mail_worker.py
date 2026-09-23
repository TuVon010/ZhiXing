"""Persistent mail jobs. Interactive work takes priority over indexing."""
import json
import os
import threading
import time
from datetime import datetime,timezone,timedelta
from sqlalchemy import text
from .db import now,encode
from .mail_store import initialize,enqueue,rows,require,secret


def schedule(db):
    initialize(db)
    if os.environ.get('ZHIXING_DISABLE_MAIL_NETWORK')=='1':return
    from .config import settings
    if settings.mode!='live':return
    for account in rows(db,'mail_account',limit=1000):
        if account['body'].get('enabled'):
            with db.engine.connect() as c:
                pending=c.execute(text("SELECT 1 FROM mail_jobs WHERE account_id=:a AND kind='sync' AND status IN ('queued','running')"),{'a':account['id']}).first()
            if not pending:enqueue(db,'sync',{},account['id'],priority=20,dedupe='sync:'+account['id']+':'+str(int(time.time()//30)))


def execute_job(db,job):
    payload=json.loads(job['payload']);kind=job['kind'];aid=job['account_id']
    from .mail_ingest import scan
    if kind=='sync':return scan(db,aid)
    if kind=='baseline':
        from .mail_ingest import connection,lease,put
        account=require(db,aid,'mail_account')['body']
        with lease(db,aid),connection(db,account) as imap:
            imap.select('INBOX',readonly=True);validity=imap.response('UIDVALIDITY')[1][0].decode()
            typ,data=imap.uid('search',None,'ALL')
            if typ!='OK':raise ValueError('无法建立收取基线')
            put(db,'mail-cursor:'+aid+':INBOX','mail_cursor',{'account_id':aid,'validity':validity,'uid':max([int(v) for v in (data[0] or b'').split()],default=0),'last_poll':now(),'catchup':None})
        db.audit('system','MAIL_BASELINE_RESET',account_id=aid)
        return {'baseline':True}
    if kind=='preview':return scan(db,aid,payload,preview=True)
    if kind=='import':
        row=require(db,payload['import_id'],'mail_import')
        if row['status']!='running':return {'paused':True}
        result=scan(db,aid,{k:v for k,v in row['body'].items() if k in {'account_id','start','end','limit'}},import_id=row['id'])
        current=require(db,row['id'],'mail_import')
        if current['status']=='running':
            enqueue(db,'import',{'import_id':row['id']},aid,priority=15,
                    dedupe=f"import:{row['id']}:{current['body']['last_uid']}")
        return result
    if kind=='index':
        from .mail_rag import index_message
        result=index_message(db,payload['message_id'])
        account=require(db,aid,'mail_account')['body']
        if account.get('auto_analyze'):
            enqueue(db,'analyze',payload,aid,priority=30,dedupe='analyze:'+payload['message_id'])
        return result
    if kind=='search':
        from .mail_rag import search
        return search(db,payload)
    if kind=='reindex':
        from .mail_rag import rebuild_account
        return rebuild_account(db,aid)
    if kind=='assistant':
        from .mail_assistant import run_turn
        return run_turn(db,payload['turn_id'])
    if kind=='analyze':
        from .mail_assistant import create_session,create_turn
        from .mail_models import SessionInput,TurnInput
        account=require(db,aid,'mail_account')['body'];mail=require(db,payload['message_id'],'mail_message',[aid])
        if mail['status']!='active' or not account.get('enabled') or not account.get('auto_analyze'):return {'skipped':True}
        cutoff=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        with db.engine.connect() as c:
            c.exec_driver_sql('BEGIN IMMEDIATE')
            key='auto-analysis:'+mail['id']
            previous=c.execute(text('SELECT body FROM records WHERE id=:id'),{'id':key}).first()
            if previous and json.loads(previous[0]).get('turn_id'):return {'turn_id':json.loads(previous[0])['turn_id']}
            count=c.execute(text("SELECT COUNT(*) FROM records WHERE kind='analysis_slot' AND scope=:a AND created_at>=:cutoff"),{'a':aid,'cutoff':cutoff}).scalar_one()
            if not previous:
                if count>=account['hourly_analysis_limit']:raise ValueError('该账号每小时自动分析预算已用完；可稍后手动重试')
                db.insert('analysis_slot',{'message_id':mail['id']},id=key,scope=aid,conn=c)
            c.commit()
        session=create_session(db,SessionInput(account_ids=[aid],thread_id=mail['body']['thread_id']),new_id='session-'+mail['id'])
        turn=create_turn(db,session['id'],TurnInput(text='检查本线程是否有尚未记录的明确待办、会议和承诺，先查现有任务避免重复；仅在证据明确时提出本地动作。不要自动发送邮件。'),new_id='turn-'+mail['id'])
        db.update(key,{'message_id':mail['id'],'turn_id':turn['id']});return {'turn_id':turn['id']}
    if kind=='connection_test':
        from .mail_ingest import connection
        from .mail_send import smtp_connection
        account=require(db,aid,'mail_account')['body']
        with connection(db,account) as imap:imap.noop()
        with smtp_connection(db,account) as smtp:smtp.noop()
        return {'imap':True,'smtp':True,'sent':False}
    raise ValueError('未知邮件任务')


def work_once(db):
    initialize(db)
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        c.execute(text("UPDATE mail_jobs SET status=CASE WHEN attempts<3 THEN 'queued' ELSE 'failed' END WHERE status='running' AND lease<:t"),{'t':time.time()})
        row=c.execute(text("SELECT * FROM mail_jobs j WHERE status='queued' AND NOT EXISTS(SELECT 1 FROM mail_jobs other WHERE other.scope=j.scope AND other.status='running') ORDER BY priority,created_at,id LIMIT 1")).mappings().first()
        if not row:c.rollback();return False
        job=dict(row);c.execute(text("UPDATE mail_jobs SET status='running',lease=:lease,attempts=attempts+1,updated_at=:at WHERE id=:id"),{'lease':time.time()+120,'at':now(),'id':job['id']});c.commit()
    stopped=threading.Event()
    def heartbeat():
        while not stopped.wait(20):
            with db.engine.begin() as c:c.execute(text("UPDATE mail_jobs SET lease=:lease WHERE id=:id AND status='running'"),{'lease':time.time()+120,'id':job['id']})
    t=threading.Thread(target=heartbeat,daemon=True);t.start()
    try:
        result=execute_job(db,job);status='completed'
    except Exception as exc:
        result={'error':type(exc).__name__,'detail':str(exc)[:200] if isinstance(exc,(ValueError,KeyError)) else '连接或处理失败，请检查配置及日志'};status='failed'
        if job['kind']=='assistant':
            turn_id=json.loads(job['payload'])['turn_id'];turn=db.get(turn_id)
            db.update(turn_id,{**turn['body'],'error':result},'failed')
    finally:stopped.set();t.join()
    with db.engine.begin() as c:
        c.execute(text('UPDATE mail_jobs SET status=:status,result=:result,lease=0,updated_at=:at WHERE id=:id'),{'status':status,'result':encode(result),'at':now(),'id':job['id']})
    if job['account_id']:
        try:
            account=require(db,job['account_id'],'mail_account')
            db.update(account['id'],{**account['body'],'last_job':{'id':job['id'],'kind':job['kind'],'status':status,'at':now(),'error':result.get('error')}})
        except KeyError:pass
    return True
