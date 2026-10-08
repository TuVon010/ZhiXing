"""Ranking policy safety and complementarity, without model score mocks."""
from backend.app.modules.mail.retrieval_ranking import rank_candidates


def test_explicit_identifier_beats_semantically_similar_wrong_identifier():
    rows={'wrong':{'content':'合同 HT-204 付款'},'right':{'content':'合同 HT - 20 付款'}}
    ranked, info=rank_candidates('HT-20 怎么付款？',['right','wrong'],['wrong','right'],rows,automatic=True)
    assert ranked[0]=='right'
    assert info['route']=='identifier'


def test_semantic_route_rejects_lexical_only_leader_but_retains_its_evidence():
    rows={str(i):{'content':'证据'} for i in range(42)}
    ranked, info=rank_candidates('实验波动异常怎么办？',['41']+list(map(str,range(39,0,-1))),
                                list(map(str,range(40))),rows,automatic=True)
    assert ranked[:3]==['0','1','2']
    assert '41' in ranked and len(ranked)==41
    assert not info['rerank_applied']


def test_empty_channel_and_equal_rrf_are_supported():
    rows={'a':{'content':'说明'}}
    ranked, info=rank_candidates('说明',[],['a'],rows,automatic=True)
    assert ranked==['a']
    assert rank_candidates('说明',['a'],[],rows)[0]==['a']


def test_large_search_trace_does_not_break_next_decision_context():
    import json
    from backend.app.modules.mail.assistant_context import serialize_search_step,compact_steps
    result={'evidence':[{'id':'chunk','message_id':'mail','text':'正文'*10000}],
            'ranking_policy':{'scores':{str(i):float(i) for i in range(80)}},
            'warnings':['无效时间已忽略'],'degraded':False}
    saved=serialize_search_step(result)
    assert len(saved)<1000 and json.loads(saved)['warnings']==result['warnings']
    assert compact_steps([{'tool':'search','result':saved}])[0]['result']['matches']==1
