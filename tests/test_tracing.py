from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.app.core.config import settings
from backend.app.agent.graph import ingest, work_once, approve
from backend.app.observability.tracing import detail
from backend.app.persistence.store import Store
import backend.main as api

def message(text='待办：整理材料'):
    return {'message_id':'trace-message','source':'web','text':text,'timestamp':'2026-09-21T10:00:00+08:00'}

@pytest.mark.parametrize('valid',[True,False])
def test_model_success_and_failure_stay_in_run_trace(db,monkeypatch,valid):
    monkeypatch.setattr(settings,'mode','live')
    monkeypatch.setattr(settings,'model_api_key','private-test-key')
    monkeypatch.setattr(settings,'model_name','mock-model')
    content='{"summary":"task","actions":[{"tool":"create_todo","args":{"title":"整理材料"}}]}' if valid else 'malformed JSON'
    def handle(request):
        return httpx.Response(200,json={'choices':[{'message':{'content':content}}],'usage':{'prompt_tokens':30,'completion_tokens':10,'total_tokens':40}})
    client=httpx.Client
    rid=ingest(message(),db)
    with patch('backend.app.agent.model_client.httpx.Client',side_effect=lambda **kw:client(transport=httpx.MockTransport(handle),**kw)):
        work_once(db)
    trace=detail(db,rid)
    assert trace.trace.trace_id==rid
    assert trace.trace.status==('completed' if valid else 'failed')
    assert trace.trace.model_call_count==1
    assert trace.trace.total_tokens==40
    assert trace.trace.cost is None
    call=trace.model_calls[0]
    assert call['body']['run_id']==rid
    assert call['body']['trace_id']==rid
    assert call['body']['latency_ms']>=0
    assert 'schema' in call['body']['messages'][-1]['content']
    events=[r['body']['event_type'] for r in trace.audit]
    assert 'MODEL_REQUEST' in events
    assert ('MODEL_RESPONSE' if valid else 'MODEL_FAILED') in events
    assert all(r['body']['trace_id']==rid for r in trace.audit)
    assert 'private-test-key' not in trace.model_dump_json()
    from backend.app.api.routes import automation
    monkeypatch.setattr(automation,'store',db)
    with TestClient(api.app) as c:
        assert c.get('/api/runs/'+rid).status_code==401
        c.get('/api/session')
        response=c.get('/api/runs/'+rid)
        assert response.status_code==200
        assert response.json()['trace']['model_call_count']==1

def test_human_wait_and_resume_share_one_trace(db):
    rid=ingest(message('明天下午三点组会'),db)
    work_once(db)
    waiting=detail(db,rid)
    assert waiting.trace.status=='waiting_approval'
    assert waiting.trace.model_call_count==0
    approval=db.list('approval',status='pending')[0]
    approve(approval['id'],{'decision':'approve'},db)
    work_once(db)
    finished=detail(db,rid)
    events=[r['body']['event_type'] for r in finished.audit]
    assert {'APPROVAL_REQUESTED','APPROVAL_DECIDED','WORKFLOW_PAUSED','WORKFLOW_RESUMED','WORKFLOW_FINISHED'}<=set(events)
    assert finished.trace.trace_id==waiting.trace.trace_id

def test_legacy_failed_model_record_is_recovered_from_audit(db):
    rid=ingest(message(),db)
    call=db.insert('model_call',{'error':'HTTPError'},status='failed')
    db.audit(rid,'MODEL_FAILED',call_id=call,error='HTTPError')
    trace=detail(db,rid)
    assert trace.model_calls[0]['id']==call
    assert trace.trace.total_tokens is None

def test_database_rename_preserves_pending_approval(tmp_path,monkeypatch):
    from scripts.migrate_brand import migrate
    from backend.app.agent.graph import Runtime
    old=Store(tmp_path/'pulse.db')
    rid=ingest(message('明天下午三点组会'),old)
    work_once(old)
    old.engine.dispose()
    assert migrate(tmp_path)
    renamed=Store(tmp_path/'zhixing.db')
    assert renamed.get(rid)['status']=='waiting_approval'
    approval=renamed.list('approval',status='pending')[0]
    approve(approval['id'],{'decision':'approve'},renamed)
    work_once(renamed)
    assert renamed.get(rid)['status']=='completed'
    assert len(renamed.list('calendar'))==1
    assert not migrate(tmp_path)
    renamed.engine.dispose()
