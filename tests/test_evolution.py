import pytest
from backend.evolution import candidate, evaluate, shadow, publish, parse_rules, parser_plan
from backend.schemas import ActionPlan

RULES={'pattern':r'待办：(?P<title>.+)','plan':{'summary':'任务','actions':[{'tool':'create_todo','args':{'title':'{title}'}}]}}

def samples(db,split,count,safety=False):
    return [db.insert('sample',{'name':'tasks','split':split,'message':{'text':f'待办：{split}任务{i}'},'expected':{'summary':'任务','actions':[{'tool':'create_todo','args':{'title':f'{split}任务{i}'}}]},'safety':safety},status='confirmed') for i in range(count)]

def test_evolution_full_lifecycle(db):
    train=samples(db,'train',20)
    ident=candidate(db,'parser','tasks',rules=RULES,evidence=train)
    with pytest.raises(KeyError):
        publish(db,ident)
    samples(db,'holdout',20,True)
    result=evaluate(db,ident)
    assert result['passed']
    with pytest.raises(ValueError):
        publish(db,ident)
    samples(db,'shadow',20)
    assert shadow(db,ident)['passed']==20
    publish(db,ident)
    assert parser_plan(db,{'text':'待办：测试','message_id':'m','source':'web','conversation_id':'inbox'},{'parsers':[ident]}).actions[0].args['title']=='测试'
    newer=candidate(db,'parser','tasks',rules=RULES,evidence=train)
    evaluate(db,newer);shadow(db,newer);publish(db,newer)
    assert db.get(ident)['status']=='archived'
    publish(db,ident,rollback=True)
    assert db.get(ident)['status']=='published'

def test_insufficient_or_regressed_samples_block(db):
    ident=candidate(db,'parser','tasks',rules=RULES,evidence=samples(db,'train',20))
    assert not evaluate(db,ident)['passed']
    samples(db,'holdout',20,True)
    b=db.get(ident)['body']; b['rules']={**RULES,'pattern':'does not match'};db.update(ident,b)
    assert not evaluate(db,ident)['passed']
    with pytest.raises(ValueError):
        publish(db,ident)

def test_parser_fallback_and_unsafe_expression(db):
    assert parse_rules(RULES,{'text':'unknown'}) is None
    with pytest.raises(ValueError):
        parse_rules({'pattern':'(?R)','plan':{}},{'text':'x'})

def test_plan_rejects_cycle_and_unknown_tool():
    with pytest.raises(ValueError):
        ActionPlan.model_validate({'summary':'x','actions':[{'id':'a','tool':'create_todo','args':{},'depends_on':['a']}]})
    with pytest.raises(ValueError):
        ActionPlan.model_validate({'summary':'x','actions':[{'tool':'shell','args':{'cmd':'delete'}}]})

def test_skill_candidate_eval_and_content_hash_gate(db,monkeypatch):
    import json
    import backend.planner
    from backend.evolution import seed
    seed(db)
    samples(db,'holdout',20,True)
    def fake_model(db,messages,*args):
        message=json.loads(messages[1]['content'])
        return {'summary':'task','actions':[{'tool':'create_todo','args':{'title':message['text'].split('：',1)[1]}}]}
    monkeypatch.setattr(backend.planner,'model_json',fake_model)
    ident=candidate(db,'skill','tasks',content='从明确前缀提取任务')
    assert evaluate(db,ident)['passed']
    publish(db,ident)
    other=candidate(db,'skill','tasks',content='提取任务，保留来源')
    assert evaluate(db,other)['passed']
    b=db.get(other)['body'];db.update(other,{**b,'content':'评测后被改变'})
    with pytest.raises(ValueError,match='改变'):
        publish(db,other)
