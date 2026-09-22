import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

import backend.main as api
from backend.config import settings
from backend.jev import QUESTIONS, review_plan
from backend.runtime import ingest, work_once
from backend.schemas import ActionPlan
from backend.tracing import detail


def plan():
    return ActionPlan.model_validate({'summary':'模拟评审用计划','actions':[
        {'tool':'send_email','args':{'recipient':'teacher@example.com','subject':'实验','content':'实验已整理'}}]})


def message():
    return {'message_id':'jev-test','source':'web','conversation_id':'inbox','text':'把实验结果发给导师'}


def response_body():
    return {'model':'jev-1.13.0','answers':{key:{'type':'noul','noul':.95} for key in QUESTIONS},
            'usage':{'input_tokens':120,'output_tokens':20}}


@pytest.fixture
def enabled(db,monkeypatch):
    monkeypatch.setattr(settings,'mode','live')
    monkeypatch.setattr(settings,'jev_mode','shadow')
    monkeypatch.setattr(settings,'jev_api_key','test-jev-secret')
    monkeypatch.setattr(settings,'jev_daily_calls',30)
    monkeypatch.setattr(settings,'jev_input_price',None)
    return db


def transport(handle):
    original=httpx.Client
    return patch('backend.jev.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw))


def test_review_records_contract_and_preserves_high_risk_approval(enabled,monkeypatch):
    db=enabled
    sent=[]
    def handle(request):
        assert str(request.url)=='https://api.typesafe.ai/v1/systemone'
        assert request.headers['authorization']=='Bearer test-jev-secret'
        payload=json.loads(request.content)
        assert set(payload['questions'])==set(QUESTIONS)
        assert 'sender_id' not in payload['state']['message']
        sent.append(payload)
        return httpx.Response(200,json=response_body())
    rid=ingest(message(),db,explicit_plan=plan().model_dump())
    with transport(handle):
        assert work_once(db)
    trace=detail(db,rid)
    assert len(sent)==1
    assert trace.run['status']=='waiting_approval'
    assert db.list('approval')[0]['body']['risk']=='HIGH'
    assert not db.list('decision')
    review=trace.jev_calls[0]
    assert review['status']=='completed'
    assert review['body']['cost'] == pytest.approx(120*.042/1_000_000)
    assert review['body']['billing']['currency'] == 'USD'
    assert review['body']['advisory_only'] is True
    assert trace.trace.jev_call_count==1 and trace.trace.model_call_count==0
    assert 'test-jev-secret' not in trace.model_dump_json()
    monkeypatch.setattr(api,'store',db)
    with TestClient(api.app) as client:
        assert client.get('/api/runs/'+rid).status_code==401
        client.get('/api/session')
        assert client.get('/api/runs/'+rid).json()['jev_calls'][0]['id']==review['id']
        assert 'test-jev-secret' not in client.get('/api/settings').text


@pytest.mark.parametrize('reason',['off','demo_mode','not_configured'])
def test_disabled_and_demo_never_contact_service(enabled,monkeypatch,reason):
    if reason=='off':
        monkeypatch.setattr(settings,'jev_mode','off')
    elif reason=='demo_mode':
        monkeypatch.setattr(settings,'mode','demo')
    else:
        monkeypatch.setattr(settings,'jev_api_key','')
    with patch('backend.jev.httpx.Client',side_effect=AssertionError('Unexpected network')):
        result=review_plan(enabled,'run-disabled',message(),plan())
    assert result is None if reason=='off' else result['body']['reason']==reason


@pytest.mark.parametrize('failure',['timeout','unauthorized','rate_limit','bad_probability','missing_answer','not_json'])
def test_review_failure_does_not_bypass_or_break_workflow(enabled,failure):
    def handle(request):
        if failure=='timeout':
            raise httpx.ReadTimeout('private server text',request=request)
        if failure in {'unauthorized','rate_limit'}:
            return httpx.Response(401 if failure=='unauthorized' else 429,json={'error':'private server text'})
        if failure=='not_json':
            return httpx.Response(200,text='not JSON')
        body=response_body()
        if failure=='bad_probability':
            body['answers']['missing_intent']['noul']=1.5
        else:
            body['answers'].pop('missing_intent')
        return httpx.Response(200,json=body)
    rid=ingest(message(),enabled,explicit_plan=plan().model_dump())
    with transport(handle):
        work_once(enabled)
    trace=detail(enabled,rid)
    assert trace.run['status']=='waiting_approval'
    assert trace.jev_calls[0]['status']=='failed'
    assert 'private server text' not in trace.model_dump_json()
    assert 'test-jev-secret' not in trace.model_dump_json()


def test_same_request_not_resubmitted_and_budget_reserved_concurrently(enabled,monkeypatch):
    monkeypatch.setattr(settings,'jev_daily_calls',1)
    sent=[]
    def handle(request):
        sent.append(request)
        return httpx.Response(200,json=response_body())
    with transport(handle):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda rid:review_plan(enabled,rid,message(),plan()),['one','two']))
        completed=next(r for r in results if r['status']=='completed')
        review_plan(enabled,completed['body']['run_id'],message(),plan())
    assert len(sent)==1
    assert sorted(r['status'] for r in results)==['completed','skipped']
    assert next(r for r in results if r['status']=='skipped')['body']['reason']=='daily_budget'


def test_configured_cost_and_unknown_usage(enabled,monkeypatch):
    monkeypatch.setattr(settings,'jev_input_price',.042)
    with transport(lambda request:httpx.Response(200,json=response_body())):
        result=review_plan(enabled,'priced',message(),plan())
    assert result['body']['cost']==pytest.approx(120*.042/1_000_000)
    body=response_body();body.pop('usage')
    with transport(lambda request:httpx.Response(200,json=body)):
        result=review_plan(enabled,'unpriced',message(),plan())
    assert result['body']['cost'] is None


def test_failed_request_is_not_retried(enabled):
    with transport(lambda request:httpx.Response(429)):
        first=review_plan(enabled,'failed-once',message(),plan())
    with patch('backend.jev.httpx.Client',side_effect=AssertionError('Do not retry')):
        second=review_plan(enabled,'failed-once',message(),plan())
    assert first['id']==second['id'] and second['status']=='failed'
