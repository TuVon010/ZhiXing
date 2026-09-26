"""Workspace regressions with synthetic accounts; no real mailbox or LLM."""
import json
from datetime import datetime, timezone, timedelta
import pytest
from backend.app.modules.mail.schemas import MailAccount, SessionInput, TurnInput, DraftInput
from backend.app.modules.mail.repository import initialize, save_account
from backend.app.modules.mail.assistant import create_session, create_turn, draft, submit_draft, run_turn
from backend.app.observability.mail import activity, trace, notifications, read_notification, CalendarInput, calendar_action
from backend.app.modules.mail.routes.work_items import followups
from backend.app.agent.graph import ingest, work_once, approve


def account(db,number=1):
    initialize(db)
    return save_account(db,MailAccount(name=f'合成账号{number}',address=f'test{number}@qq.com',enabled=True))['id']


def turn(db,aid):
    session=create_session(db,SessionInput(account_ids=[aid]))
    return create_turn(db,session['id'],TurnInput(text='整理待办'))


def action(db,aid,parent=None):
    return ingest({'message_id':f'{aid}:{parent}','source':'web','conversation_id':'mail:'+aid,'text':'添加实验待办',
                   'metadata':{'mail_accounts':[aid],'assistant_turn':parent}},db,
                  explicit_plan={'summary':'实验待办','actions':[{'tool':'create_todo','args':{'title':'提交报告'},'confidence':1}]})


def test_activity_pagination_and_accounts(db):
    a,b=account(db),account(db,2)
    t=turn(db,a);r=action(db,a,t['id']);action(db,b)
    assert activity(db,a,1,0)['total']==2
    ids={activity(db,a,1,n)['items'][0]['id'] for n in range(2)}
    assert ids=={r,t['id']}
    assert activity(db,b)['total']==1


def test_trace_joins_parent_execution_and_audit(db):
    aid=account(db);t=turn(db,aid);rid=action(db,aid,t['id'])
    db.audit(t['id'],'MAIL_AGENT_STEP',tool='actions',result={'run_id':rid})
    assert work_once(db)
    result=trace(db,rid)
    assert result['root']['id']==t['id']
    assert result['runs'][0]['run']['status']=='completed'
    assert result['runs'][0]['run']['body']['outcomes']
    events={r['body']['event_type'] for r in result['audit']}
    assert {'MAIL_AGENT_STEP','TOOL_RESULT','WORKFLOW_FINISHED'}<=events


def test_agent_draft_retains_origin_after_edit_and_approval(db):
    aid=account(db);t=turn(db,aid)
    d=draft(db,DraftInput(account_id=aid,to=['recipient@example.com'],subject='报告',content='内容'),source_turn=t['id'])
    changed=draft(db,DraftInput(account_id=aid,to=['recipient@example.com'],subject='更新报告',content='新内容',version=1),d['id'])
    rid=submit_draft(db,d['id'],changed['body']['version'])['run_id']
    assert trace(db,rid)['root']['id']==t['id']
    work_once(db)
    approval=db.list('approval')[0]
    approve(approval['id'],{'decision':'approve','version':approval['body']['version']},db)
    work_once(db)
    assert trace(db,t['id'])['runs'][0]['run']['status']=='completed'
    assert db.get(d['id'])['status']=='simulated'


def test_notifications_scoped_read_and_reminder_delivery(db):
    from backend.app.workers.runner import tick
    aid,other=account(db),account(db,2)
    rid=action(db,aid)
    db.insert('reminder',{'title':'报告到期','due_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),'run_id':rid},scope='web:mail:'+aid)
    db.insert('notification',{'title':'另一个邮箱'},scope='web:mail:'+other,status='local')
    tick(db);tick(db)
    result=notifications(aid,100,0,db)
    reminders=[r for r in result['items'] if r['body']['title']=='到期提醒']
    assert len(reminders)==1 and reminders[0]['body']['run_id']==rid
    assert all(r['body']['title']!='另一个邮箱' for r in result['items'])
    read_notification(reminders[0]['id'],db);read_notification(reminders[0]['id'],db)
    assert db.get(reminders[0]['id'])['status']=='read'


def test_calendar_approval_visibility_and_scope(db):
    aid,other=account(db),account(db,2)
    body=CalendarInput(account_id=aid,title='组会',start='2026-10-01T14:00:00+08:00',end='2026-10-01T15:00:00+08:00')
    rid=calendar_action(db,body)['run_id'];work_once(db)
    assert db.get(rid)['status']=='waiting_approval'
    assert followups(aid,db)['items']==[]
    approval=db.list('approval')[0]
    approve(approval['id'],{'decision':'approve','version':approval['body']['version']},db);work_once(db)
    item=followups(aid,db)['items'][0]
    assert item['kind']=='calendar'
    with pytest.raises(ValueError):calendar_action(db,body.model_copy(update={'account_id':other}),item['id'])


def test_busy_mail_queue_does_not_starve_actions(db,monkeypatch):
    from backend.app.workers import runner as worker, mail_jobs as mail_worker
    calls=[]
    monkeypatch.setattr(worker,'work_once',lambda db:calls.append('action') or True)
    monkeypatch.setattr(mail_worker,'work_once',lambda db:calls.append('mail') or True)
    for _ in range(3):assert worker.work_cycle(db)
    assert calls==['action','mail']*3


def test_agent_action_status_rejects_cross_account(db):
    aid,other=account(db),account(db,2);t=turn(db,aid);rid=action(db,other)
    db.update(t['id'],{**t['body'],'pending_decision':{'tool':'action_status','args':{'run_id':rid}}})
    run_turn(db,t['id'])
    step=db.get(t['id'])['body']['steps'][0]
    assert '不能读取范围外' in json.loads(step['result'])['error']


def test_import_continues_batches_without_changing_live_cursor(db,monkeypatch):
    from contextlib import contextmanager
    from backend.app.modules.mail import ingestion as mail_ingest
    from backend.app.workers import mail_jobs as mail_worker
    from backend.app.modules.mail.repository import enqueue,rows
    aid=account(db);a=db.get(aid);db.update(aid,{**a['body'],'scan_limit':1})
    searches=[]
    class Imap:
        def select(self,*a,**k):return 'OK',[]
        def response(self,*a):return 'UIDVALIDITY',[b'100']
        def uid(self,command,*args):
            if command=='search':searches.append(args);return 'OK',[b'1 2 3']
            return 'OK',[(b'INTERNALDATE "22-Sep-2026 10:00:00 +0000" RFC822.SIZE 100',b'From: teacher@example.com\r\nSubject: report\r\n\r\nReport')]
    @contextmanager
    def connection(*a):yield Imap()
    monkeypatch.setattr(mail_ingest,'connection',connection)
    ident=db.insert('mail_import',{'account_id':aid,'start':'2026-09-22T00:00:00+00:00','end':'2026-09-23T00:00:00+00:00','limit':2,'last_uid':0,'imported':0,'scanned':0},scope=aid,status='running')
    enqueue(db,'import',{'import_id':ident},aid,priority=15)
    assert mail_worker.work_once(db)
    assert db.get(ident)['status']=='running'
    assert mail_worker.work_once(db)
    assert db.get(ident)['status']=='completed'
    assert db.get(ident)['body']['imported']==2
    assert not rows(db,'mail_cursor')
    assert all('SINCE' in args and 'BEFORE' in args for args in searches)
