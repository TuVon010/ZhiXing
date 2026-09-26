import json
import pytest
from sqlalchemy import text
from backend.app.agent.graph import ingest, work_once, approve, Runtime
from backend.app.agent.tools import execute
from backend.app.core.exceptions import ExternalUnknown
from backend.app.agent.policy import decide, suggest, key, risk
from backend.app.persistence.store import uid

def input_message(text='待办：整理实验',id='m1'):
    return {'message_id':id,'text':text,'source':'web','conversation_id':'inbox','timestamp':'2026-09-21T10:00:00+08:00'}

def test_dedupe_and_restart_approval(db):
    m=input_message('明天下午三点组会，今晚整理实验结果；发送邮件给 teacher@example.com')
    rid=ingest(m,db)
    assert ingest(m,db)==rid
    assert work_once(db)
    assert len(db.list('todo'))==1
    assert len(db.list('reminder'))==1
    a=db.list('approval',status='pending')[0]
    assert a['body']['action']['tool']=='create_calendar'
    approve(a['id'],{'decision':'approve'},db)
    with pytest.raises(ValueError):
        approve(a['id'],{'decision':'approve'},db)
    assert work_once(db)
    assert len(db.list('calendar'))==1
    b=db.list('approval',status='pending')[0]
    assert b['body']['action']['tool']=='send_email'
    approve(b['id'],{'decision':'edit','args':{'recipient':'teacher@example.com','subject':'回复','content':'收到，今晚发给您。'}},db)
    with pytest.raises(ValueError):
        approve(b['id'],{'decision':'approve','version':1},db)
    approve(b['id'],{'decision':'approve','version':2},db)
    work_once(db)
    assert db.get(rid)['status']=='completed'
    rt=Runtime(db);rt.run(rid);rt.close()
    assert len(db.list('todo'))==1
    assert len(db.list('calendar'))==1

def test_reject_blocks_dependents_not_independent(db):
    p={'summary':'test','actions':[{'id':'a','tool':'send_email','args':{'recipient':'a@example.com','subject':'test','content':'test'}},{'id':'b','tool':'create_todo','args':{'title':'dependent'},'depends_on':['a']},{'id':'c','tool':'create_todo','args':{'title':'independent'}}]}
    rid=ingest(input_message(),db,p)
    work_once(db)
    approve(db.list('approval')[0]['id'],{'decision':'reject'},db)
    work_once(db)
    assert [r['body']['title'] for r in db.list('todo')]==['independent']
    assert db.get(rid)['status']=='completed_with_attention'

def test_ledger_idempotence(db):
    a={'id':'action','tool':'create_todo','args':{'title':'once'}}
    first=execute(db,a,'run','web:inbox')
    assert execute(db,a,'run','web:inbox')==first
    assert len(db.list('todo'))==1

def test_uncertain_external_reservation(db):
    with db.engine.begin() as c:
        c.execute(text("INSERT INTO ledger VALUES('external','executing',NULL,'now')"))
    a={'id':'external','tool':'send_email','args':{'recipient':'a@example.com','subject':'test','content':'test'}}
    with pytest.raises(ExternalUnknown):
        execute(db,a,'run','web:inbox')

def test_high_risk_never_trusted(db):
    a={'tool':'send_email','args':{'recipient':'a@example.com'},'confidence':1,'scenario':'work'}
    db.insert('trust',{'key':key(a)},status='published')
    assert decide(db,a)=='ASK'

def test_trust_requires_success_and_manual_publish(db):
    a={'tool':'update_todo','args':{'id':'todo'},'confidence':1,'scenario':'work'}
    for _ in range(20):
        db.insert('decision',{'key':key(a),'risk':'MEDIUM','decision':'approve','result':'completed'})
    suggest(db)
    assert len(db.list('trust',status='candidate'))==1
    assert decide(db,a)=='ASK'
    db.update(db.list('trust')[0]['id'],status='published')
    assert decide(db,a)=='ALLOW'

def test_cross_scope_write_denied(db):
    item=db.insert('todo',{'title':'private'},scope='web:mail:private-account')
    with pytest.raises(ValueError):
        execute(db,{'id':'x','tool':'update_todo','args':{'id':item,'status':'completed'}},'run','email:other')

def test_expired_lease_recovers(db):
    rid=ingest(input_message(),db)
    with db.engine.begin() as c:
        c.execute(text("UPDATE jobs SET status='running',lease_until=0"))
    work_once(db)
    assert db.get(rid)['status']=='completed'

def test_same_session_concurrency(db):
    rid=ingest(input_message(id='one'),db)
    second=ingest(input_message(id='two'),db)
    with db.engine.begin() as c:
        c.execute(text("UPDATE jobs SET status='running',lease_until=99999999999 WHERE run_id=:id"),{'id':rid})
    assert work_once(db) is False
    assert db.get(second)['status']=='queued'

def test_multi_action_result_reference(db):
    p={'summary':'create then update','actions':[{'id':'a','tool':'create_todo','args':{'title':'task'}},{'id':'b','tool':'update_todo','args':{'id':'@a','title':'updated'},'depends_on':['a']}]}
    rid=ingest(input_message(),db,p)
    work_once(db)
    a=db.list('approval')[0]
    assert a['body']['action']['args']['id']==db.list('todo')[0]['id']
    approve(a['id'],{'decision':'approve'},db);work_once(db)
    assert db.get(rid)['status']=='completed'

def test_cancelled_run_does_not_resume(db):
    rid=ingest(input_message('明天下午三点组会'),db)
    work_once(db)
    db.update(rid,status='cancelled')
    with pytest.raises(ValueError,match='取消'):
        approve(db.list('approval')[0]['id'],{'decision':'approve'},db)

def test_late_approval_between_interrupt_and_worker_release(db,monkeypatch):
    from backend.app.agent.graph import Runtime
    rid=ingest(input_message('明天下午三点组会'),db)
    original=Runtime.run
    def run(self,*args,**kwargs):
        status=original(self,*args,**kwargs)
        if status=='waiting':
            approve(db.list('approval',status='pending')[0]['id'],{'decision':'approve'},db)
        return status
    monkeypatch.setattr(Runtime,'run',run)
    work_once(db)
    work_once(db)
    assert db.get(rid)['status']=='completed'
    assert len(db.list('calendar'))==1

def test_replay_never_mutates_work_data(db):
    rid=ingest(input_message(),db,replay=True)
    work_once(db)
    assert db.get(rid)['status']=='completed'
    assert not db.list('todo')
    assert next(iter(db.get(rid)['body']['outcomes'].values()))['data']['replay']

def test_memory_candidates_not_published_automatically(db):
    ingest(input_message('记住：我喜欢中文摘要'),db)
    work_once(db)
    assert len(db.list('memory',status='candidate'))==1
    assert not db.list('memory',status='published')
