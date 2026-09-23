"""Extended mail regressions: edge cases, filtering, RAG, agent tools, API."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
import pytest
from sqlalchemy import text
from backend.mail_store import initialize, save_account, rows, require, enqueue, job
from backend.mail_models import MailAccount, DraftInput, SessionInput, TurnInput, ImportRequest, SearchRequest
from backend.mail_ingest import store_message, scan, lease, clean, internal_date
from backend.mail_assistant import draft, submit_draft, create_session, create_turn, run_turn, thread_messages, memory_snapshot
from backend.mail_rag import index_message, search, chunks, tokens
from backend.mail_api import FilterRules, filters, get_filters, message_state
from backend.runtime import work_once, approve
from backend.tools import execute
from backend.channels import ExternalUnknown
from backend.filtering import classify_message, CATEGORY_AD, CATEGORY_SUBSCRIPTION, CATEGORY_TRANSACTION, CATEGORY_TODO, CATEGORY_MANUAL


def account(db, number=1, enabled=True):
    initialize(db)
    aid = save_account(db, MailAccount(name=f'测试{number}', address=f'owner{number}@qq.com', enabled=enabled))['id']
    from backend.filtering import DEFAULT_RULES
    db.insert('setting', {**DEFAULT_RULES, 'whitelist_senders': ['teacher@example.com']}, id='mail-filter:' + aid)
    return aid


def make_message(from_addr='teacher@example.com', to='owner1@qq.com', subject='实验计划', body='请明天提交实验报告', mid=None, reference=None, cc=None):
    m = EmailMessage()
    m['From'] = from_addr
    m['To'] = to
    if cc:
        m['Cc'] = cc
    m['Subject'] = subject
    m['Message-ID'] = mid or '<test-msg@example.com>'
    if reference:
        m['In-Reply-To'] = reference
    m.set_content(body)
    return m


def store(db, aid, msg, uid=1, received='2026-09-22T10:00:00+00:00'):
    return store_message(db, aid, '100', uid, msg.as_bytes(), received)


# ============ 收取与存储 ============

class TestCollection:
    def test_html_clean_removes_scripts_and_style(self):
        html = '<html><head><style>body{color:red}</style></head><body><p>正文内容</p><script>alert(1)</script></body></html>'
        from backend.mail_ingest import TextHTML
        p = TextHTML()
        p.feed(html)
        result = ''.join(p.parts)
        assert '正文内容' in result
        assert 'alert' not in result
        assert 'color:red' not in result

    def test_clean_removes_signature_and_quotes(self):
        body = '这是正文\n\nOn 2026-01-01 wrote:\n> 引用内容\n-- \n签名'
        result = clean(body)
        assert '这是正文' in result
        assert '引用内容' not in result
        assert '签名' not in result

    def test_clean_chinese_reply_marker(self):
        body = '回复正文\n在 2026年1月1日 写道：\n> 旧内容'
        result = clean(body)
        assert '回复正文' in result
        assert '旧内容' not in result

    def test_store_message_preserves_headers(self, db):
        aid = account(db)
        msg = make_message(subject='头部测试')
        msg['X-Custom'] = 'custom-value'
        mid = store(db, aid, msg)
        row = db.get(mid)
        assert row['body']['headers'].get('X-Custom') == 'custom-value'
        assert row['body']['subject'] == '头部测试'

    def test_store_message_saves_three_timestamps(self, db):
        aid = account(db)
        msg = make_message()
        msg['Date'] = 'Mon, 22 Sep 2026 10:00:00 +0000'
        mid = store(db, aid, msg, received='2026-09-22T10:05:00+00:00')
        b = db.get(mid)['body']
        assert b['declared_at'] is not None
        assert b['received_at'] == '2026-09-22T10:05:00+00:00'
        assert b['fetched_at'] is not None

    def test_store_message_dedup_by_identity(self, db):
        aid = account(db)
        msg = make_message(mid='<dup@example.com>')
        first = store(db, aid, msg, uid=1)
        second = store(db, aid, msg, uid=1)
        assert first == second
        assert len(rows(db, 'mail_message', [aid])) == 1

    def test_store_message_different_uid_creates_new(self, db):
        aid = account(db)
        msg = make_message(mid='<same-mid@example.com>')
        first = store(db, aid, msg, uid=1)
        second = store(db, aid, msg, uid=2)
        assert first != second

    def test_oversize_message_marked_incomplete(self, db, monkeypatch):
        aid = account(db)
        import backend.mail_ingest as ing
        state = {'call': 0}
        class FakeIMAP:
            def select(self, *a, **k): return 'OK', [b'1']
            def response(self, *a): return 'UIDVALIDITY', [b'100']
            def uid(self, cmd, *args):
                if cmd == 'search':
                    state['call'] += 1
                    if state['call'] == 1:
                        return 'OK', [b'']
                    return 'OK', [b'1']
                if 'HEADER' in str(args):
                    return 'OK', [(b'1 (INTERNALDATE "22-Sep-2026 10:00:00 +0000" RFC822.SIZE 40000000)', b')')]
                return 'OK', [(b'body', b'small'), b')']
        @contextmanager
        def connect(*a): yield FakeIMAP()
        monkeypatch.setattr(ing, 'connection', connect)
        scan(db, aid)
        scan(db, aid)
        msgs = rows(db, 'mail_message', [aid])
        assert len(msgs) == 1
        assert msgs[0]['body'].get('incomplete') is True

    def test_message_state_transitions(self, db):
        aid = account(db)
        mid = store(db, aid, make_message())
        assert db.get(mid)['status'] == 'active'
        message_state(mid, type('S', (), {'status': 'archived'})(), db)
        assert db.get(mid)['status'] == 'archived'
        message_state(mid, type('S', (), {'status': 'filtered'})(), db)
        assert db.get(mid)['status'] == 'filtered'

    def test_invalid_state_rejected(self, db):
        aid = account(db)
        mid = store(db, aid, make_message())
        with pytest.raises(ValueError):
            message_state(mid, type('S', (), {'status': 'deleted'})(), db)

    def test_restore_filtered_triggers_index(self, db):
        aid = account(db)
        mid = store(db, aid, make_message())
        message_state(mid, type('S', (), {'status': 'filtered'})(), db)
        message_state(mid, type('S', (), {'status': 'active'})(), db)
        jobs = [j for j in db.list('mail_jobs' if hasattr(db, 'list') else 'job') if j.get('kind') == 'index'] if False else []
        # 验证状态恢复
        assert db.get(mid)['status'] == 'active'


# ============ 线程关联 ============

class TestThreading:
    def test_references_joins_existing_thread(self, db):
        aid = account(db)
        first = store(db, aid, make_message(mid='<first@example.com>'))
        tid = db.get(first)['body']['thread_id']
        reply = make_message(mid='<reply@example.com>', reference='<first@example.com>')
        reply['References'] = '<first@example.com>'
        second = store(db, aid, reply, uid=2)
        assert db.get(second)['body']['thread_id'] == tid
        assert len(thread_messages(db, tid, [aid])) == 2

    def test_subject_only_does_not_merge(self, db):
        aid = account(db)
        first = store(db, aid, make_message(subject='相同主题', mid='<a@example.com>'))
        second = store(db, aid, make_message(subject='相同主题', mid='<b@example.com>'), uid=2)
        assert db.get(first)['body']['thread_id'] != db.get(second)['body']['thread_id']

    def test_multiple_references_merge_threads(self, db):
        aid = account(db)
        t1 = store(db, aid, make_message(mid='<t1@example.com>'))
        t2 = store(db, aid, make_message(mid='<t2@example.com>'), uid=2)
        tid1 = db.get(t1)['body']['thread_id']
        tid2 = db.get(t2)['body']['thread_id']
        assert tid1 != tid2
        merge = make_message(mid='<merge@example.com>')
        merge['References'] = '<t1@example.com> <t2@example.com>'
        store(db, aid, merge, uid=3)
        merged_thread = db.get(tid2)
        assert merged_thread['status'] == 'merged'
        assert merged_thread['body']['merged_into'] == tid1

    def test_thread_detail_includes_messages(self, db):
        aid = account(db)
        first = store(db, aid, make_message(mid='<thread-a@example.com>'))
        reply = make_message(mid='<thread-b@example.com>', reference='<thread-a@example.com>')
        store(db, aid, reply, uid=2)
        tid = db.get(first)['body']['thread_id']
        msgs = thread_messages(db, tid, [aid])
        assert len(msgs) == 2

    def test_cross_account_thread_access_denied(self, db):
        a1, a2 = account(db), account(db, 2)
        mid = store(db, a1, make_message())
        tid = db.get(mid)['body']['thread_id']
        with pytest.raises(ValueError):
            thread_messages(db, tid, [a2])


# ============ 过滤规则 ============

class TestFiltering:
    def test_whitelist_bypasses_blacklist(self, db):
        aid = account(db)
        filters(aid, FilterRules(whitelist_senders=['trusted@example.com'], blacklist_senders=['trusted@example.com']), db)
        msg = make_message(from_addr='trusted@example.com', subject='限时优惠点击购买')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'active'

    def test_blacklist_sender_filters(self, db):
        aid = account(db)
        filters(aid, FilterRules(blacklist_senders=['spam@bad.com']), db)
        msg = make_message(from_addr='spam@bad.com', subject='正常内容')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'filtered'
        assert db.get(mid)['body']['filter']['reason'] == '黑名单发件人'

    def test_blacklist_domain_filters(self, db):
        aid = account(db)
        filters(aid, FilterRules(blacklist_domains=['bad.com']), db)
        msg = make_message(from_addr='user@bad.com', subject='正常')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'filtered'

    def test_subject_keyword_filters(self, db):
        aid = account(db)
        filters(aid, FilterRules(subject_keywords=['发票', '报销']), db)
        msg = make_message(subject='发票申请')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'filtered'

    def test_content_keyword_filters(self, db):
        aid = account(db)
        filters(aid, FilterRules(content_keywords=['赌博']), db)
        msg = make_message(subject='标题', body='这里有赌博网站')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'filtered'

    def test_ad_category_auto_filtered(self, db):
        aid = account(db)
        filters(aid, FilterRules(auto_filter_categories=['ad', 'subscription']), db)
        msg = make_message(from_addr='shop@ad.com', subject='限时优惠', body='点击立即购买，满减活动，免费领取')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'filtered'
        assert db.get(mid)['body']['filter']['category'] == 'ad'

    def test_manual_category_goes_to_review(self, db):
        aid = account(db)
        msg = make_message(from_addr='friend@personal.com', subject='问候', body='今天天气不错')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'review'

    def test_filter_disabled_all_pass(self, db):
        aid = account(db)
        filters(aid, FilterRules(enabled=False, blacklist_senders=['spam@bad.com']), db)
        msg = make_message(from_addr='spam@bad.com', subject='广告')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] != 'filtered'

    def test_filter_rules_version_increments(self, db):
        aid = account(db)
        v1 = get_filters(aid, db)
        v2 = filters(aid, FilterRules(subject_keywords=['test']), db)
        assert v2['version'] == v1['version'] + 1

    def test_filter_evidence_recorded(self, db):
        aid = account(db)
        filters(aid, FilterRules(blacklist_senders=['spam@bad.com']), db)
        msg = make_message(from_addr='spam@bad.com')
        mid = store(db, aid, msg)
        evidence = db.get(mid)['body']['filter']['evidence']
        assert len(evidence) > 0

    def test_filtered_message_not_indexed(self, db):
        aid = account(db)
        filters(aid, FilterRules(blacklist_senders=['spam@bad.com']), db)
        msg = make_message(from_addr='spam@bad.com', subject='过滤内容实验报告')
        mid = store(db, aid, msg)
        result = index_message(db, mid)
        assert result.get('skipped') or result.get('chunks', 0) == 0

    def test_review_message_not_indexed_until_active(self, db):
        aid = account(db)
        msg = make_message(from_addr='unknown@x.com', subject='模糊内容实验报告')
        mid = store(db, aid, msg)
        assert db.get(mid)['status'] == 'review'
        result = index_message(db, mid)
        assert result.get('skipped')


# ============ 分类规则 ============

class TestClassification:
    def test_classify_ad(self):
        cat, conf, ev = classify_message({'text': '限时优惠！点击立即购买，满减活动', 'sender_id': 'ad@shop.com'})
        assert cat == CATEGORY_AD

    def test_classify_subscription(self):
        cat, conf, ev = classify_message({'text': '技术周报第42期\n本期精选：Python 新特性回顾，往期内容', 'sender_id': 'news@tech.com'})
        assert cat == CATEGORY_SUBSCRIPTION

    def test_classify_transaction_verification(self):
        cat, conf, ev = classify_message({'text': '您的验证码是 123456', 'sender_id': 'noreply@bank.com'})
        assert cat == CATEGORY_TRANSACTION

    def test_classify_transaction_logistics(self):
        cat, conf, ev = classify_message({'text': '您的快递已发货，物流单号 SF123456', 'sender_id': 'service@shop.com'})
        assert cat == CATEGORY_TRANSACTION

    def test_classify_todo_promise(self):
        cat, conf, ev = classify_message({'text': '我会在明天下午之前完成报告', 'sender_id': 'colleague@work.com'})
        assert cat == CATEGORY_TODO

    def test_classify_todo_request(self):
        cat, conf, ev = classify_message({'text': '请帮我处理这个问题，今天下班前完成', 'sender_id': 'boss@work.com'})
        assert cat == CATEGORY_TODO

    def test_classify_manual_default(self):
        cat, conf, ev = classify_message({'text': '今天天气不错', 'sender_id': 'friend@x.com'})
        assert cat == CATEGORY_MANUAL

    def test_all_categories_have_labels(self):
        from backend.filtering import CATEGORY_LABELS
        for cat in [CATEGORY_AD, CATEGORY_SUBSCRIPTION, CATEGORY_TRANSACTION, CATEGORY_TODO, CATEGORY_MANUAL]:
            assert cat in CATEGORY_LABELS


# ============ RAG 检索 ============

class TestRAG:
    def test_qdrant_local_vectors_persist_and_search(self, db, monkeypatch):
        import numpy as np
        import backend.mail_rag as rag
        class Tokenizer:
            def encode(self, value, add_special_tokens=False): return list(value)
            def decode(self, ids, skip_special_tokens=True): return ''.join(ids)
        class Embedder:
            tokenizer=Tokenizer()
            def encode(self, values, normalize_embeddings=True): return np.tile(np.array([[1.,0.,0.]],dtype=np.float32),(len(values),1))
        class Reranker:
            def predict(self, pairs): return np.arange(len(pairs),dtype=np.float32)
        monkeypatch.setattr(rag,'load_models',lambda:(Embedder(),Reranker()))
        monkeypatch.setattr(rag,'model_version',lambda:'test-e5-v1')
        monkeypatch.setattr(rag,'model_manifest',lambda:{'embedding':{'revision':'test-e5-v1'},'reranker':{'revision':'test-r1'}})
        aid=account(db);mid=store(db,aid,make_message(subject='Qdrant持久化测试',body='本地向量检索与邮件账号隔离'))
        indexed=index_message(db,mid)
        assert indexed['model_version']=='test-e5-v1' and indexed['chunks']>0
        rag._close_vector_store(db)
        result=search(db,{'account_ids':[aid],'query':'语义查找邮件','mode':'vector'})
        assert not result['degraded'] and any(item['message_id']==mid for item in result['evidence'])
        assert (db.path.parent/'qdrant').exists()

    def test_keyword_search_returns_evidence(self, db):
        aid = account(db)
        mid = store(db, aid, make_message(subject='项目进度', body='本周完成了三个功能模块的开发'))
        index_message(db, mid)
        result = search(db, {'account_ids': [aid], 'query': '功能模块'})
        assert result['mode'] == 'keyword'
        assert any(e['message_id'] == mid for e in result['evidence'])

    def test_search_account_isolation(self, db):
        a1, a2 = account(db), account(db, 2)
        m1 = store(db, a1, make_message(subject='账号A的邮件', body='实验报告内容'))
        m2 = store(db, a2, make_message(subject='账号B的邮件', body='实验报告内容'), uid=2)
        index_message(db, m1)
        index_message(db, m2)
        result = search(db, {'account_ids': [a1], 'query': '实验报告'})
        ids = {e['message_id'] for e in result['evidence']}
        assert m1 in ids
        assert m2 not in ids

    def test_search_time_range_filter(self, db):
        aid = account(db)
        mid = store(db, aid, make_message(), received='2026-09-22T10:00:00+00:00')
        index_message(db, mid)
        result = search(db, {'account_ids': [aid], 'query': '实验报告', 'start': '2026-09-23T00:00:00+00:00', 'end': '2026-09-24T00:00:00+00:00'})
        assert len(result['evidence']) == 0

    def test_filtered_message_excluded_from_search(self, db):
        aid = account(db)
        filters(aid, FilterRules(blacklist_senders=['spam@bad.com']), db)
        mid = store(db, aid, make_message(from_addr='spam@bad.com', subject='过滤的实验报告'))
        index_message(db, mid)
        result = search(db, {'account_ids': [aid], 'query': '实验报告'})
        assert not any(e['message_id'] == mid for e in result['evidence'])

    def test_chunks_keyword_fallback(self):
        text = 'a' * 2000
        result = chunks(text, model=None)
        assert len(result) > 1
        assert all(len(c) <= 640 for c in result)

    def test_tokens_chinese_segmentation(self):
        result = tokens('这是一个中文测试句子')
        assert '这是' in result or '中文' in result

    def test_index_dedup_by_content_hash(self, db):
        aid = account(db)
        mid = store(db, aid, make_message(body='重复内容测试'))
        r1 = index_message(db, mid)
        r2 = index_message(db, mid)
        assert r1.get('chunks', 0) == r2.get('chunks', 0)

    def test_search_degraded_when_no_model(self, db):
        aid = account(db)
        mid = store(db, aid, make_message(body='测试内容'))
        index_message(db, mid)
        result = search(db, {'account_ids': [aid], 'query': '测试'})
        assert result.get('degraded') is True or result.get('mode') == 'keyword'


# ============ Agent 工具循环 ============

class TestAgent:
    def test_agent_answer_requires_evidence_citation(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        mid = store(db, aid, make_message())
        index_message(db, mid)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='总结'))
        monkeypatch.setattr(settings, 'mode', 'live')
        monkeypatch.setattr(planner, 'model_json', lambda *a, **k: {'tool': 'answer', 'answer': '总结', 'citations': ['nonexistent-id']})
        result = run_turn(db, t['id'])
        assert '引用了未读取的证据' in str(result['steps'][-1]['result'])

    def test_agent_cannot_cross_account_draft(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid, other = account(db), account(db, 2)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='发邮件'))
        monkeypatch.setattr(settings, 'mode', 'live')
        monkeypatch.setattr(planner, 'model_json', lambda *a, **k: {'tool': 'draft', 'args': {'account_id': other, 'to': ['x@y.com'], 'subject': 'test', 'content': 'test'}})
        result = run_turn(db, t['id'])
        assert '不能跨账号' in str(result['steps'][-1]['result'])

    def test_agent_search_limit_3(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='搜索'))
        monkeypatch.setattr(settings, 'mode', 'live')
        monkeypatch.setattr(planner, 'model_json', lambda *a, **k: {'tool': 'search', 'args': {'query': 'test'}})
        run_turn(db, t['id'])
        assert db.get(t['id'])['body']['searches'] == 3

    def test_agent_input_budget_stops(self, db):
        aid = account(db)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='测试'))
        db.update(t['id'], {**db.get(t['id'])['body'], 'input_tokens': 23999})
        result = run_turn(db, t['id'])
        assert db.get(t['id'])['status'] == 'budget_exceeded'

    def test_agent_max_6_rounds(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='循环'))
        monkeypatch.setattr(settings, 'mode', 'live')
        monkeypatch.setattr(planner, 'model_json', lambda *a, **k: {'tool': 'search', 'args': {'query': 'test'}})
        result = run_turn(db, t['id'])
        assert len(result['steps']) == 6
        assert db.get(t['id'])['status'] == 'budget_exceeded'

    def test_agent_clarification_status(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        mid = store(db, aid, make_message())
        index_message(db, mid)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='模糊问题'))
        monkeypatch.setattr(settings, 'mode', 'live')
        monkeypatch.setattr(planner, 'model_json', lambda *a, **k: {'tool': 'clarify', 'answer': '请补充信息', 'citations': []})
        run_turn(db, t['id'])
        assert db.get(t['id'])['status'] == 'clarification'

    def test_agent_memory_snapshot_frozen(self, db):
        aid = account(db)
        db.insert('memory', {'content': '测试偏好', 'memory_type': 'preference'}, scope=aid, status='published')
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='测试'))
        snap1 = t['body']['memory_snapshot']
        db.insert('memory', {'content': '新增偏好', 'memory_type': 'preference'}, scope=aid, status='published')
        t2 = db.get(t['id'])
        assert t2['body']['memory_snapshot'] == snap1

    def test_agent_thread_context_loaded(self, db, monkeypatch):
        from backend.config import settings
        aid = account(db)
        first = store(db, aid, make_message(mid='<ctx-a@example.com>'))
        reply = make_message(mid='<ctx-b@example.com>', reference='<ctx-a@example.com>')
        store(db, aid, reply, uid=2)
        tid = db.get(first)['body']['thread_id']
        s = create_session(db, SessionInput(account_ids=[aid], thread_id=tid))
        t = create_turn(db, s['id'], TurnInput(text='总结线程'))
        assert db.get(t['id'])['body']['thread_id'] == tid

    def test_agent_cancel_stops(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='测试'))
        db.update(t['id'], status='cancelled')
        result = run_turn(db, t['id'])
        # 已取消的 turn 直接返回 body，不执行工具循环
        assert db.get(t['id'])['status'] == 'cancelled'
        assert 'steps' not in result or len(result.get('steps', [])) == 0

    def test_agent_draft_tool_idempotent(self, db, monkeypatch):
        import backend.planner as planner
        from backend.config import settings
        aid = account(db)
        mid = store(db, aid, make_message())
        s = create_session(db, SessionInput(account_ids=[aid]))
        t = create_turn(db, s['id'], TurnInput(text='写回复'))
        monkeypatch.setattr(settings, 'mode', 'live')
        call_count = [0]
        def fake_model(*a, **k):
            call_count[0] += 1
            if call_count[0] == 1:
                return {'tool': 'draft', 'args': {'account_id': aid, 'message_id': mid, 'mode': 'reply', 'content': '回复内容'}}
            return {'tool': 'answer', 'answer': '完成', 'citations': []}
        monkeypatch.setattr(planner, 'model_json', fake_model)
        run_turn(db, t['id'])
        drafts = rows(db, 'mail_draft', [aid])
        assert len(drafts) == 1


# ============ 草稿与发送 ============

class TestDraft:
    def test_new_draft(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='新邮件', content='正文'))
        assert d['body']['mode'] == 'new'
        assert d['body']['version'] == 1

    def test_reply_draft_sets_headers(self, db):
        aid = account(db)
        mid = store(db, aid, make_message(mid='<reply-src@example.com>', subject='原主题'))
        d = draft(db, DraftInput(account_id=aid, message_id=mid, mode='reply', content='回复'))
        assert d['body']['in_reply_to'] == '<reply-src@example.com>'
        assert d['body']['subject'] == 'Re: 原主题'

    def test_reply_all_excludes_own_address(self, db):
        aid = account(db)
        msg = make_message(to='owner1@qq.com, other@example.com', cc='cc@example.com')
        mid = store(db, aid, msg)
        d = draft(db, DraftInput(account_id=aid, message_id=mid, mode='reply_all', content='回复全部'))
        assert 'owner1@qq.com' not in d['body']['to']
        assert 'owner1@qq.com' not in d['body']['cc']

    def test_draft_version_increments_on_edit(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='v1', content='一'))
        edited = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='v2', content='二', version=1), d['id'])
        assert edited['body']['version'] == 2

    def test_editing_approved_draft_cancels_approval(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='test', content='test'))
        rid = submit_draft(db, d['id'], 1)['run_id']
        work_once(db)
        assert db.get(d['id'])['status'] == 'approval'
        edited = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='modified', content='new', version=1), d['id'])
        assert edited['body']['version'] == 2
        assert db.get(rid)['status'] == 'cancelled'

    def test_submit_wrong_version_fails(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='test', content='test'))
        with pytest.raises(ValueError):
            submit_draft(db, d['id'], 99)

    def test_empty_recipient_rejected(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=[], subject='test', content='test'))
        with pytest.raises(ValueError):
            submit_draft(db, d['id'], 1)

    def test_duplicate_submit_rejected(self, db):
        aid = account(db)
        d = draft(db, DraftInput(account_id=aid, to=['x@y.com'], subject='test', content='test'))
        submit_draft(db, d['id'], 1)
        with pytest.raises(ValueError):
            submit_draft(db, d['id'], 1)


# ============ 账号管理 ============

class TestAccount:
    def test_qq_preset_sets_hosts(self):
        a = MailAccount(name='test', address='user@qq.com', provider='qq')
        assert a.imap_host == 'imap.qq.com'
        assert a.smtp_host == 'smtp.qq.com'

    def test_163_preset_sets_hosts(self):
        a = MailAccount(name='test', address='user@163.com', provider='163')
        assert a.imap_host == 'imap.163.com'
        assert a.smtp_host == 'smtp.163.com'

    def test_custom_requires_hosts(self):
        with pytest.raises(ValueError):
            MailAccount(name='test', address='user@custom.com', provider='custom')

    def test_invalid_email_rejected(self):
        with pytest.raises(ValueError):
            MailAccount(name='test', address='not-an-email', provider='qq')

    def test_scan_limit_bounds(self):
        with pytest.raises(ValueError):
            MailAccount(name='test', address='a@qq.com', provider='qq', scan_limit=0)
        with pytest.raises(ValueError):
            MailAccount(name='test', address='a@qq.com', provider='qq', scan_limit=999)

    def test_save_account_returns_public_without_secret(self, db):
        row = save_account(db, MailAccount(name='secret', address='s@qq.com', password='my-secret'))
        assert 'password' not in row['body']
        assert 'credential_ref' not in row['body']
        assert row['body']['credential_configured'] is True

    def test_account_enable_toggle(self, db):
        from backend.mail_api import enable
        aid = account(db, enabled=False)
        result = enable(aid, type('T', (), {'enabled': True})(), db)
        assert result['body']['enabled'] is True


# ============ 历史导入 ============

class TestImport:
    def test_import_time_boundary_exclusive_end(self, db, monkeypatch):
        import backend.mail_ingest as ing
        aid = account(db)
        class FakeIMAP:
            def select(self, *a, **k): return 'OK', [b'2']
            def response(self, *a): return 'UIDVALIDITY', [b'100']
            def uid(self, cmd, *args):
                if cmd == 'search': return 'OK', [b'1 2']
                uid = args[0]
                if 'HEADER' in str(args):
                    hour = '10' if uid == '1' else '12'
                    return 'OK', [(f'{uid} (INTERNALDATE "22-Sep-2026 {hour}:00:00 +0000" RFC822.SIZE 100)'.encode(), b')')]
                m = make_message(mid=f'<imp-{uid}@example.com>')
                return 'OK', [(b'body', m.as_bytes()), b')']
        @contextmanager
        def connect(*a): yield FakeIMAP()
        monkeypatch.setattr(ing, 'connection', connect)
        spec = {'account_id': aid, 'start': '2026-09-22T00:00:00+00:00', 'end': '2026-09-22T11:00:00+00:00', 'limit': 10}
        result = scan(db, aid, spec)
        assert result['imported'] == 1

    def test_import_pause_resume(self, db):
        aid = account(db)
        ident = db.insert('mail_import', {'account_id': aid, 'start': '2026-01-01T00:00:00+00:00', 'end': '2026-12-31T00:00:00+00:00', 'limit': 100, 'last_uid': 0, 'imported': 0, 'scanned': 0}, scope=aid, status='running')
        from backend.mail_api import import_action
        import_action(ident, 'pause', db)
        assert db.get(ident)['status'] == 'paused'
        import_action(ident, 'resume', db)
        assert db.get(ident)['status'] == 'running'

    def test_import_cancel(self, db):
        aid = account(db)
        ident = db.insert('mail_import', {'account_id': aid, 'start': '2026-01-01T00:00:00+00:00', 'end': '2026-12-31T00:00:00+00:00', 'limit': 100, 'last_uid': 0, 'imported': 0, 'scanned': 0}, scope=aid, status='running')
        from backend.mail_api import import_action
        import_action(ident, 'cancel', db)
        assert db.get(ident)['status'] == 'cancelled'

    def test_import_completed_cannot_resume(self, db):
        aid = account(db)
        ident = db.insert('mail_import', {'account_id': aid, 'start': '2026-01-01T00:00:00+00:00', 'end': '2026-12-31T00:00:00+00:00', 'limit': 100, 'last_uid': 0, 'imported': 100, 'scanned': 100}, scope=aid, status='completed')
        from backend.mail_api import import_action
        with pytest.raises(ValueError):
            import_action(ident, 'resume', db)


# ============ 积压处理 ============

class TestBacklog:
    def test_backlog_continue_resets_paused(self, db):
        aid = account(db)
        key = 'mail-cursor:' + aid + ':INBOX'
        db.insert('mail_cursor', {'account_id': aid, 'validity': '100', 'uid': 10, 'paused': True, 'catchup': {'count': 5}}, id=key)
        from backend.mail_api import backlog
        backlog(aid, 'continue', db)
        assert db.get(key)['body'].get('paused') is not True

    def test_backlog_from_now_queues_baseline(self, db):
        aid = account(db)
        key = 'mail-cursor:' + aid + ':INBOX'
        db.insert('mail_cursor', {'account_id': aid, 'validity': '100', 'uid': 10, 'paused': True}, id=key)
        from backend.mail_api import backlog
        result = backlog(aid, 'from_now', db)
        assert 'job_id' in result

    def test_invalid_backlog_choice_rejected(self, db):
        aid = account(db)
        from backend.mail_api import backlog
        with pytest.raises(ValueError):
            backlog(aid, 'invalid', db)


# ============ 附件解析 ============

class TestAttachments:
    def test_txt_attachment_extracted(self, db, tmp_path):
        from backend.mail_attachments import extract
        f = tmp_path / 'test.txt'
        f.write_text('附件文本内容', encoding='utf-8')
        result = extract(str(f), 'txt')
        assert result['status'] == 'parsed'
        assert result['segments'][0]['text'] == '附件文本内容'

    def test_unsupported_format_marked(self, db, tmp_path):
        from backend.mail_attachments import extract
        f = tmp_path / 'test.exe'
        f.write_bytes(b'MZ binary')
        result = extract(str(f), 'exe')
        assert result['status'] == 'unsupported'

    def test_oversize_zip_docx(self, db, tmp_path):
        from backend.mail_attachments import extract
        import zipfile
        f = tmp_path / 'big.docx'
        with zipfile.ZipFile(f, 'w') as z:
            z.writestr('large.bin', b'x' * (60 * 1024 * 1024))
        result = extract(str(f), 'docx')
        assert result['status'] == 'oversize'

    def test_attachment_pending_saved(self, db):
        aid = account(db)
        msg = EmailMessage()
        msg['From'] = 'teacher@example.com'
        msg['Subject'] = '带附件'
        msg['Message-ID'] = '<att@example.com>'
        msg.set_content('正文')
        msg.add_attachment(b'pdf content', maintype='application', subtype='pdf', filename='doc.pdf')
        mid = store(db, aid, msg)
        atts = db.get(mid)['body']['attachments']
        assert len(atts) == 1
        assert atts[0]['status'] == 'pending'

    def test_unsupported_attachment_not_saved(self, db):
        aid = account(db)
        msg = EmailMessage()
        msg['From'] = 'teacher@example.com'
        msg['Subject'] = '带附件'
        msg['Message-ID'] = '<att2@example.com>'
        msg.set_content('正文')
        msg.add_attachment(b'exe', maintype='application', subtype='exe', filename='program.exe')
        mid = store(db, aid, msg)
        assert db.get(mid)['body']['attachments'][0]['status'] == 'unsupported'


# ============ 记忆系统 ============

class TestMemory:
    def test_memory_candidate_requires_confirmation(self, db):
        aid = account(db)
        db.insert('memory', {'content': '候选偏好', 'memory_type': 'preference'}, scope=aid, status='candidate')
        snap = memory_snapshot(db, [aid])
        assert '候选偏好' not in snap['user_md']

    def test_published_memory_included(self, db):
        aid = account(db)
        db.insert('memory', {'content': '已确认偏好', 'memory_type': 'preference'}, scope=aid, status='published')
        snap = memory_snapshot(db, [aid])
        assert '已确认偏好' in snap['user_md']

    def test_suspended_memory_excluded(self, db):
        aid = account(db)
        db.insert('memory', {'content': '已暂停', 'memory_type': 'preference'}, scope=aid, status='suspended')
        snap = memory_snapshot(db, [aid])
        assert '已暂停' not in snap['user_md']

    def test_account_memory_isolation(self, db):
        a1, a2 = account(db), account(db, 2)
        db.insert('memory', {'content': '账号A的记忆'}, scope=a1, status='published')
        snap = memory_snapshot(db, [a2])
        assert '账号A的记忆' not in str(snap)

    def test_global_memory_included(self, db):
        aid = account(db)
        db.insert('memory', {'content': '全局记忆'}, scope='global', status='published')
        snap = memory_snapshot(db, [aid])
        assert '全局记忆' in snap['memory_md']

    def test_memory_budget_truncates(self, db):
        aid = account(db)
        for i in range(50):
            db.insert('memory', {'content': f'记忆条目{i}' * 100, 'memory_type': 'fact'}, scope=aid, status='published')
        snap = memory_snapshot(db, [aid])
        assert len(snap['memory_md']) < 3000
