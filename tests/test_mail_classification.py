"""API-classifier learning loop with synthetic mail and simulated model calls.

These verify behavior, not real-model quality. No external API is contacted.
"""
import json
import pytest
from email.message import EmailMessage
from fastapi.testclient import TestClient
from backend import main
from backend.app.core.config import settings
from backend.app.modules.mail.repository import save_account
from backend.app.modules.mail.schemas import MailAccount,PerceptionFeedback
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.perception import apply_feedback,_store_result,perceive
from backend.app.modules.mail.repositories.classification import current_label,policy
from backend.app.modules.mail.services import classification as service


def account(db,n=1):
    return save_account(db,MailAccount(name=f'合成账号{n}',address=f'test{n}@qq.com'))['id']


def mail(db,aid,n=1,text='科研论文方法分析，包含文献综述，点击退订。',subject='科研简报',reply_to=None):
    msg=EmailMessage();msg['From']='research@example.com';msg['To']='test@qq.com'
    msg['Subject']=subject;msg['Message-ID']=f'<classification-{aid}-{n}@test.local>'
    if reply_to:msg['In-Reply-To']=reply_to
    msg.set_content(text)
    return store_message(db,aid,'test',n,msg.as_bytes(),'2026-10-08T01:00:00+00:00')


def label(db,mid,**fields):
    return apply_feedback(db,PerceptionFeedback(message_id=mid,**fields))


def test_partial_labels_are_atomic_idempotent_and_revisioned(db):
    aid=account(db);mid=mail(db,aid)
    _store_result(db,mid,{'category':'ad','spam_score':.9,'confidence':.99})
    label(db,mid,category='work')
    row=current_label(db,mid)
    assert row['body']['labels']=={'category':'work'}
    assert db.get(mid)['status']=='filtered'  # category never implies normal
    assert row['body']['model_prediction']['category']=='ad'
    assert row['body']['revision']==db.get(mid)['body']['classification_label_revision']==1
    label(db,mid,category='work')
    assert current_label(db,mid)['body']['revision']==1
    label(db,mid,spam_label='normal',expected_revision=1)
    assert db.get(mid)['status']=='active'
    assert current_label(db,mid)['body']['labels']=={'category':'work','spam_label':'normal'}
    assert len(db.list('mail_classification_label_revision'))==2
    with pytest.raises(ValueError,match='刷新'):label(db,mid,priority='high',expected_revision=1)
    assert 'priority' not in current_label(db,mid)['body']['labels']


def test_reanalysis_preserves_human_judgment_and_uncertainty(db):
    aid=account(db);mid=mail(db,aid)
    label(db,mid,spam_label='normal')
    _store_result(db,mid,{'category':'ad','spam_score':.99,'confidence':.99})
    assert db.get(mid)['status']=='active' and db.get(mid)['body']['perception']['spam_score']==0
    label(db,mid,spam_label='uncertain')
    _store_result(db,mid,{'spam_score':.99,'confidence':.99})
    assert db.get(mid)['status']=='review'
    assert db.get(mid)['body']['classification_decision']['action']=='review'


def test_model_first_keeps_subscriptions_until_model_decides(db):
    aid=account(db);mid=mail(db,aid,text='第十期科研周刊，推荐阅读。订阅更新，点击退订。')
    assert db.get(mid)['status']=='active'
    _store_result(db,mid,{'category':'work','spam_score':.1,'confidence':.95})
    assert db.get(mid)['status']=='active'


def test_memory_is_optional_candidate_and_bounded_to_mail(db):
    aid=account(db);mid=mail(db,aid)
    _store_result(db,mid,{'spam_score':.99,'confidence':.99})
    label(db,mid,spam_label='normal')
    assert not db.list('memory')
    other=mail(db,aid,2,subject='科研方法订阅')
    _store_result(db,other,{'spam_score':.99,'confidence':.99})
    label(db,other,spam_label='normal',remember=True)
    memory=db.list('memory')[0]
    assert memory['status']=='candidate' and '不要仅据此放行' in memory['body']['content']
    assert not service.context(db,aid,db.get(mid)['body'])['preferences']


def test_context_excludes_accounts_self_thread_template_and_holdout(db):
    aid,other=account(db),account(db,2)
    ids=[]
    for i in range(1,12):
        mid=mail(db,aid,i,text='科研文献综述，实验方法的研究论文。'+chr(96+i),subject='科研资料'+chr(96+i))
        label(db,mid,category='work');ids.append(mid)
    foreign=mail(db,other);label(db,foreign,category='personal')
    target=db.get(ids[0])['body']
    ctx=service.context(db,aid,target)
    samples={r['id']:r for r in service.dataset(db,aid)}
    assert ctx['examples']
    for e in ctx['examples']:
        b=samples[e['label_id']]['body']
        assert b['account_id']==aid and b['message_id']!=ids[0]
        assert b['thread_id']!=target['thread_id'] and b['split']=='optimization'
    excluded={r['body']['message_id'] for r in samples.values()}
    assert not service.context(db,aid,target,excluded=excluded)['examples']
    # Structural duplicates across threads share one split and are not examples.
    duplicate=mail(db,aid,20,text=target['text'],subject=target['subject'])
    label(db,duplicate,category='work')
    grouped={r['body']['message_id']:r['body'] for r in service.dataset(db,aid)}
    assert grouped[duplicate]['split']==grouped[ids[0]]['split']
    assert not any(e['label_id']=='classification-label:'+duplicate for e in service.context(db,aid,target)['examples'])


def test_metrics_do_not_infer_unconfirmed_fields_or_report_zero_denominators():
    from backend.app.modules.mail.classification import summarize_metrics
    m=summarize_metrics([{'labels':{'priority':'high'},'prediction':{'category':'ad','priority':'high'},'decision':{'action':'filtered'}}])
    assert m['fields']['priority']['accuracy']==1
    assert m['fields']['category']['accuracy'] is None
    assert m['false_positive_rate'] is None and m['false_negative_rate'] is None
    m=summarize_metrics([{'labels':{'spam_label':'normal'},'prediction':{'spam_label':'spam'},'decision':{'action':'filtered'}},
                         {'labels':{'spam_label':'spam'},'prediction':{'spam_label':'uncertain'},'decision':{'action':'review'}}])
    assert m['false_positive_rate']==1 and m['false_negative_rate']==1


def test_demo_evaluation_is_unverified(db):
    aid=account(db);mid=mail(db,aid);label(db,mid,category='work')
    split=service.dataset(db,aid)[0]['body']['split']
    row=service.create_evaluation(db,aid,split,1)
    assert service.evaluate_step(db,row['id'])['status']=='unverified'
    assert 'metrics' not in db.get(row['id'])['body']


def test_evaluation_is_frozen_resumable_and_has_no_mail_side_effects(db,monkeypatch):
    aid=account(db);mid=mail(db,aid);label(db,mid,category='work',spam_label='normal')
    monkeypatch.setattr(settings,'mode','live');calls=[]
    def model(_db,messages,schema,purpose):
        calls.append((messages,purpose))
        return {'category':'work','spam_score':.1,'confidence':.95,'summary':'模拟分类'}
    monkeypatch.setattr('backend.app.agent.model_client.model_json',model)
    split=service.dataset(db,aid)[0]['body']['split'];row=service.create_evaluation(db,aid,split,1)
    before=db.get(mid)
    first=service.evaluate_step(db,row['id']);assert first['cursor']==1
    # The confirmed value changes afterwards; the original test label remains frozen.
    label(db,mid,category='personal')
    for _ in range(20):
        if db.get(row['id'])['status']=='completed':break
        service.evaluate_step(db,row['id'])
    result=db.get(row['id'])
    assert result['status']=='completed' and len(calls)==9
    assert result['body']['metrics']['baseline']['fields']['category']['accuracy']==1
    assert not result['body']['errors'] and result['body']['safety_passed']
    assert not db.list('todo') and not db.list('calendar') and not db.list('mail_draft')
    service.evaluate_step(db,row['id']);assert len(calls)==9
    assert before['body']['text']==db.get(mid)['body']['text']
    assert all(not json.loads(ms[1]['content'])['confirmed_examples'] for ms,_ in calls)


def test_api_failure_and_config_change_are_not_successful_evaluations(db,monkeypatch):
    aid=account(db);mid=mail(db,aid);label(db,mid,spam_label='normal')
    monkeypatch.setattr(settings,'mode','live')
    split=service.dataset(db,aid)[0]['body']['split'];row=service.create_evaluation(db,aid,split,1)
    def fail(*a,**kw):raise TimeoutError('模拟失败')
    monkeypatch.setattr('backend.app.agent.model_client.model_json',fail)
    service.evaluate_step(db,row['id'])
    assert db.get(row['id'])['body']['results'][0]['error']=='TimeoutError'
    monkeypatch.setattr(settings,'model_name','changed-model')
    assert service.evaluate_step(db,row['id'])['status']=='failed'


def test_http_account_scope_withdraw_and_backfill(db,monkeypatch):
    aid,other=account(db),account(db,2);mid=mail(db,aid);label(db,mid,category='work')
    monkeypatch.setattr('backend.app.persistence.store.store',db)
    with TestClient(main.app) as client:
        client.get('/api/session');headers={'X-ZhiXing-Local':'1'}
        base=f'/api/mail/accounts/{aid}/classification'
        data=client.get(base).json();assert data['total']==1
        ident=data['items'][0]['id']
        ev=client.post(base+'/evaluations',headers=headers,json={'split':data['items'][0]['body']['split'],'limit':1}).json()
        assert client.get(f'/api/mail/accounts/{other}/classification/evaluations/{ev["id"]}').status_code!=200
        first=client.post(base+f'/evaluations/{ev["id"]}/export',headers=headers,json={}).json()
        second=client.post(base+f'/evaluations/{ev["id"]}/export',headers=headers,json={}).json()
        from pathlib import Path
        assert first['path']!=second['path'] and Path(first['path']).exists() and Path(second['path']).exists()
        assert json.loads(Path(first['path']).read_text(encoding='utf-8'))['id']==ev['id']
        assert client.post(base+f'/evaluations/{ev["id"]}/cancel',headers=headers,json={}).json()['status']=='cancelled'
        assert client.post(f'/api/mail/accounts/{other}/classification/labels/{ident}/withdraw',headers=headers,json={}).status_code!=200
        assert client.post(base+f'/labels/{ident}/withdraw',headers=headers,json={}).status_code==200
        assert client.get(base).json()['total']==0
        assert db.get(mid)['body']['perception']['category']=='work'
        assert client.post(base+'/labels/backfill',headers=headers,json={}).json()['imported']==0  # withdrawal is respected


def test_candidate_and_publication_require_evidence(db):
    aid=account(db)
    with pytest.raises(ValueError,match='至少需要'):service.create_candidate(db,aid)


def test_no_api_secret_or_other_mail_content_in_reference_context(db,monkeypatch):
    aid=account(db);mid=mail(db,aid)
    monkeypatch.setattr(settings,'model_api_key','do-not-copy-secret')
    ctx=service.context(db,aid,db.get(mid)['body'])
    assert 'do-not-copy-secret' not in json.dumps(ctx)


def publication_fixture(db,monkeypatch):
    from backend.app.modules.mail.classification import template_key
    aid=account(db);count=0
    for i in range(300):
        code=chr(97+i//26)+chr(97+i%26)
        is_spam=count%2==1
        subject=('垃圾' if is_spam else '科研')+'资料'+code
        text=('无关推销信息' if is_spam else '科研方法说明')+code
        if int(template_key({'subject':subject,'text':text})[:8],16)%5!=0:continue
        mid=mail(db,aid,i+1,text=text,subject=subject)
        label(db,mid,category='ad' if is_spam else 'work',spam_label='spam' if is_spam else 'normal')
        count+=1
        if count==10:break
    assert count==10
    monkeypatch.setattr(settings,'mode','live');monkeypatch.setattr(settings,'model_name','test-api-model')
    def model(_db,ms,schema,purpose):
        subject=json.loads(ms[1]['content'])['mail']['subject']
        spam=subject.startswith('垃圾')
        return {'category':'ad' if spam else 'work','spam_score':.99 if spam else .1,'confidence':.99}
    monkeypatch.setattr('backend.app.agent.model_client.model_json',model)
    candidate=db.insert('mail_classification_policy',{'guidance':'主动订阅不等于垃圾','version':2,'parent_id':'builtin-v2'},scope=aid,status='candidate')
    evaluation=service.create_evaluation(db,aid,'holdout',20,candidate)
    for _ in range(40):
        if db.get(evaluation['id'])['status']=='completed':break
        service.evaluate_step(db,evaluation['id'])
    assert db.get(evaluation['id'])['status']=='completed'
    return aid,candidate,evaluation['id']


def test_publish_non_regressing_candidate_then_rollback(db,monkeypatch):
    aid,candidate,ev=publication_fixture(db,monkeypatch)
    service.publish(db,aid,candidate,ev)
    assert policy(db,aid)['id']==candidate and policy(db,aid)['body']['version']==2
    service.rollback(db,aid)
    assert policy(db,aid)['id']=='builtin-v2'


@pytest.mark.parametrize('failure',['regression','small_sample','safety','label_change','config_change','category_missing'])
def test_publish_gate_blocks_invalid_evidence(db,monkeypatch,failure):
    aid,candidate,ev=publication_fixture(db,monkeypatch);row=db.get(ev);b=row['body']
    if failure=='regression':b['metrics']['candidate']['false_positive_rate']=.2
    if failure=='small_sample':b['metrics']['candidate']['normal_count']=4
    if failure=='safety':b['safety_passed']=False
    if failure=='label_change':label(db,b['cells'][0]['sample_id'].removeprefix('classification-label:'),priority='high')
    if failure=='config_change':service.configure(db,aid,{'enabled':False,'max_examples':3})
    if failure=='category_missing':b['metrics']['candidate']['fields']['category']['confirmed']=0
    db.update(ev,b)
    with pytest.raises(ValueError):service.publish(db,aid,candidate,ev)
    assert policy(db,aid)['id']=='builtin-v2'


def test_cancel_during_call_does_not_resume_evaluation(db,monkeypatch):
    aid=account(db);mid=mail(db,aid);label(db,mid,category='work')
    monkeypatch.setattr(settings,'mode','live')
    ev=service.create_evaluation(db,aid,service.dataset(db,aid)[0]['body']['split'],1)
    def cancel(*args,**kw):
        row=db.get(ev['id']);db.update(ev['id'],row['body'],'cancelled')
        return {'category':'work','spam_score':0}
    monkeypatch.setattr('backend.app.agent.model_client.model_json',cancel)
    assert service.evaluate_step(db,ev['id'])['status']=='cancelled'
    assert db.get(ev['id'])['body']['cursor']==0


def test_candidate_generation_uses_only_optimization_labels(db,monkeypatch):
    aid=account(db)
    for n in range(20):
        mid=mail(db,aid,n+1,subject='文献主题'+chr(97+n),text='实验方法文献内容'+chr(97+n))
        label(db,mid,category='work')
    candidate=service.create_candidate(db,aid)
    assert len(candidate['body']['evidence'])>=5
    samples={r['id']:r for r in service.dataset(db,aid)}
    assert all(samples[e['id']]['body']['split']=='optimization' for e in candidate['body']['evidence'])
    monkeypatch.setattr(settings,'mode','live')
    monkeypatch.setattr('backend.app.agent.model_client.model_json',lambda *a,**kw:{'guidance':'科研内容不能仅因退订链接而判垃圾','rationale':'修正退订误判'})
    result=service.generate_candidate(db,candidate['id'])
    assert result['status']=='candidate'


def test_completed_prediction_survives_crash_before_cursor_commit(db,monkeypatch):
    aid=account(db);mid=mail(db,aid);label(db,mid,category='work')
    monkeypatch.setattr(settings,'mode','live')
    ev=service.create_evaluation(db,aid,service.dataset(db,aid)[0]['body']['split'],1)
    cell=ev['body']['cells'][0]
    saved={'sample_id':cell['sample_id'],'mode':cell['mode'],'labels':cell['labels'],
           'prediction':{'category':'work'},'decision':{'action':'active'},'safety':False,'latency_ms':2,'cost':None}
    db.insert('mail_classification_prediction',saved,id=f'classification-result:{ev["id"]}:0',scope=aid)
    def forbidden(*a,**kw):raise AssertionError('已完成的结果不应重复调用 API')
    monkeypatch.setattr('backend.app.agent.model_client.model_json',forbidden)
    assert service.evaluate_step(db,ev['id'])['cursor']==1


def test_rule_override_is_replayed_without_ingestion_side_effects():
    from backend.app.modules.mail.classification import filter_decision
    body={'sender':'research@example.com','subject':'科研资讯','text':'论文更新，点击退订'}
    result={'spam_score':.01,'confidence':.99}
    assert filter_decision(body,result,{'blacklist_senders':['research@example.com']})['action']=='filtered'
    assert filter_decision(body,result,{'blacklist_senders':['research@example.com'],
                                      'whitelist_senders':['research@example.com']})['action']=='active'
    assert filter_decision(body,{'spam_score':.99,'confidence':.99},
                           {'classification_mode':'rules','auto_filter_categories':[]})['action']=='active'


def test_uncertain_model_result_enters_review_and_protects_action_mail(db):
    aid=account(db);mid=mail(db,aid)
    _store_result(db,mid,{'category':'ad','spam_score':.7,'confidence':.7})
    assert db.get(mid)['status']=='review'
    assert db.get(mid)['body']['classification_decision']['action']=='review'
    other=mail(db,aid,2,subject='项目任务')
    _store_result(db,other,{'spam_score':.99,'confidence':.99,'needs_reply':True})
    assert db.get(other)['status']=='review'  # Never filter away a conflicting action.


def test_transitive_thread_template_group_is_not_reference_context(db):
    aid=account(db)
    target=mail(db,aid,1,subject='科研资料甲',text='科研文献综述实验方法甲')
    bridge=mail(db,aid,2,subject='科研资料乙',text='科研文献综述实验方法乙',
                reply_to=db.get(target)['body']['message_id'])
    duplicate=mail(db,aid,3,subject='科研资料乙',text='科研文献综述实验方法乙')
    for mid in (target,bridge,duplicate):label(db,mid,category='work')
    groups={r['body']['group_key'] for r in service.dataset(db,aid)}
    assert len(groups)==1
    assert not service.context(db,aid,db.get(target)['body'])['examples']


def test_trace_supports_evaluation_and_rollback_can_republish(db,monkeypatch):
    from backend.app.observability.mail import trace
    aid,candidate,ev=publication_fixture(db,monkeypatch)
    view=trace(db,ev)
    assert view['root']['kind']=='mail_classification_evaluation'
    assert len(view['audit'])==24
    service.publish(db,aid,candidate,ev);service.rollback(db,aid)
    second=db.insert('mail_classification_policy',{'guidance':'第二候选','version':2},scope=aid,status='candidate')
    from backend.app.modules.mail.repositories.classification import activate_policy
    activate_policy(db,aid,db.get(second))
    assert service.rollback(db,aid)['policy_id']=='builtin-v2'


def test_live_context_and_schema_validation_leave_mail_unchanged(db,monkeypatch):
    aid=account(db);mid=mail(db,aid);label(db,mid,spam_label='normal')
    monkeypatch.setattr(settings,'mode','live')
    def invalid(_db,ms,schema,purpose):
        wire=json.loads(ms[1]['content'])
        assert wire['mail']['text']==db.get(mid)['body']['text']
        assert wire['confirmed_examples']==[]
        assert purpose=='mail_perception'
        return {'category':'not-a-category'}
    monkeypatch.setattr('backend.app.agent.model_client.model_json',invalid)
    with pytest.raises(ValueError,match='校验失败'):perceive(db,mid)
    assert db.get(mid)['status']=='active'
    assert db.get(mid)['body']['perception']['spam_label']=='normal'
    assert db.get(mid)['body']['classification_context']['versions']['prompt']=='mail-perception-v2'


def test_classifier_gateway_records_usage_cache_and_honors_budget(db,monkeypatch):
    from unittest.mock import patch
    import httpx
    from backend.app.observability.mail import trace
    aid=account(db);mid=mail(db,aid);label(db,mid,category='work',spam_label='normal')
    monkeypatch.setattr(settings,'mode','live')
    monkeypatch.setattr(settings,'model_name','synthetic-api')
    monkeypatch.setattr(settings,'model_api_key','synthetic-private-key')
    monkeypatch.setattr(settings,'model_daily_calls',1)
    split=service.dataset(db,aid)[0]['body']['split']
    ev=service.create_evaluation(db,aid,split,1)
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'category':'work','spam_score':.1})}}],
            'usage':{'prompt_tokens':100,'completion_tokens':20,'total_tokens':120,
                     'prompt_tokens_details':{'cached_tokens':60}}})
    original=httpx.Client
    with patch('backend.app.agent.model_client.httpx.Client',side_effect=lambda **kw:original(transport=httpx.MockTransport(handle),**kw)):
        service.evaluate_step(db,ev['id'])
        service.evaluate_step(db,ev['id'])
    result=db.get(ev['id'])['body']['results']
    assert len(requests)==1  # The second cell cannot bypass the global budget.
    assert result[0]['usage']['total_tokens']==120 and result[0]['cost'] is None
    assert result[1]['error']=='ValueError' and result[1]['usage'] is None
    view=trace(db,ev['id'])
    assert len(view['model_calls'])==1
    assert view['billing']['cache_hit_rate']==.6
    assert 'synthetic-private-key' not in json.dumps(view)
