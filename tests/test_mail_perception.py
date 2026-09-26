"""主动感知 Agent（Mail Perception Agent）测试。

覆盖：
- 数据模型验证
- demo 模式感知
- 感知结果存储
- 用户纠偏与记忆学习
- 待办候选创建与确认
- 垃圾邮件自动标记
"""
from datetime import datetime, timezone
from email.message import EmailMessage
import pytest
from backend.app.modules.mail.repository import initialize, save_account, rows
from backend.app.modules.mail.schemas import (
    MailAccount, PerceptionResult, PerceptionFeedback,
    PerceivedTodo, PerceivedEvent,
)
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.perception import (
    perceive, apply_feedback, get_perception,
    list_pending_todos, _demo_perceive,
)


def account(db):
    initialize(db)
    aid = save_account(db, MailAccount(
        name='测试邮箱', address='owner@qq.com', enabled=True, auto_analyze=True
    ))['id']
    from backend.app.modules.mail.filtering import DEFAULT_RULES
    db.insert('setting', {**DEFAULT_RULES}, id='mail-filter:' + aid)
    return aid


def make_message(db, aid, subject='实验计划', body='请明天提交实验报告', number=1):
    m = EmailMessage()
    m['From'] = 'teacher@example.com'
    m['To'] = 'owner@qq.com'
    m['Subject'] = subject
    m['Message-ID'] = f'<test{number}@example.com>'
    m.set_content(body)
    return store_message(db, aid, '100', number, m.as_bytes(), '2026-09-22T10:00:00+00:00')


# ============================================================
# 数据模型测试
# ============================================================

class TestPerceptionModels:
    def test_perception_result_defaults(self):
        r = PerceptionResult()
        assert r.spam_score == 0.0
        assert r.category == 'other'
        assert r.summary == ''
        assert r.todos == []
        assert r.calendar_events == []
        assert r.needs_reply is False
        assert r.priority == 'normal'
        assert r.confidence == 0.5

    def test_perception_result_validation(self):
        with pytest.raises(Exception):
            PerceptionResult(spam_score=1.5)  # 超过 1.0
        with pytest.raises(Exception):
            PerceptionResult(category='invalid')  # 非法分类
        with pytest.raises(Exception):
            PerceptionResult(priority='urgent')  # 非法优先级

    def test_perceived_todo_deadline_requires_timezone(self):
        with pytest.raises(Exception):
            PerceivedTodo(action='测试', deadline=datetime(2026, 1, 1))  # 无时区

    def test_perceived_event_validation(self):
        e = PerceivedEvent(title='会议', start=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert e.title == '会议'
        assert e.location == ''


# ============================================================
# Demo 模式感知测试
# ============================================================

class TestDemoPerception:
    def test_demo_perceive_work_email(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='项目进度报告', body='请在周五前提交项目进度')
        result = perceive(db, mid)
        assert result['category'] == 'work'
        assert result['summary'] != ''
        assert result['model_version'] == 'demo'

    def test_demo_perceive_ad_email(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='限时优惠！', body='全场五折，免费领取，退订请回复')
        result = perceive(db, mid)
        assert result['spam_score'] >= 0.5
        assert result['category'] == 'ad'
        assert result['priority'] == 'low'

    def test_demo_perceive_notification(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='系统通知：账单已生成', body='您的本月账单已生成')
        result = perceive(db, mid)
        assert result['category'] == 'notification'

    def test_demo_perceive_needs_reply(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='请确认会议时间', body='请回复确认是否参加')
        result = perceive(db, mid)
        assert result['needs_reply'] is True

    def test_demo_perceive_stores_result(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        perceive(db, mid)
        stored = get_perception(db, mid)
        assert stored is not None
        assert stored['model_version'] == 'demo'

    def test_demo_perceive_nonexistent_message(self, db):
        with pytest.raises(KeyError):
            perceive(db, 'nonexistent-id')


# ============================================================
# 感知结果存储测试
# ============================================================

class TestPerceptionStorage:
    def test_get_perception_returns_none_when_not_perceived(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        # 不调用 perceive，直接查询
        result = get_perception(db, mid)
        assert result is None

    def test_perception_stored_in_message_body(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        perceive(db, mid)
        msg = db.get(mid)
        assert 'perception' in msg['body']
        assert msg['body']['perception']['summary'] != ''

    def test_high_spam_score_auto_filters(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='限时优惠免费领取', body='促销折扣退订')
        initial_status = db.get(mid)['status']
        perceive(db, mid)
        msg = db.get(mid)
        # 规则可能先过滤，但 Demo 感知不能再次改变邮件状态。
        assert msg['body']['perception']['spam_score'] == 0.7
        assert msg['status'] == initial_status


# ============================================================
# 用户纠偏与记忆学习测试
# ============================================================

class TestFeedback:
    def test_feedback_updates_category(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='限时优惠', body='促销')
        perceive(db, mid)
        # 用户纠正：这不是广告，是工作邮件
        result = apply_feedback(db, PerceptionFeedback(
            message_id=mid, category='work', spam_score=0.0
        ))
        assert result['category'] == 'work'
        assert result['spam_score'] == 0.0

    def test_feedback_restores_filtered_message(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='限时优惠免费领取', body='促销折扣退订')
        perceive(db, mid)
        # 用户纠正：这不是垃圾邮件
        apply_feedback(db, PerceptionFeedback(
            message_id=mid, spam_score=0.0
        ))
        msg = db.get(mid)
        assert msg['status'] == 'active'

    def test_feedback_creates_memory_candidates(self, db):
        aid = account(db)
        mid = make_message(db, aid, subject='限时优惠', body='促销')
        perceive(db, mid)
        before = len(rows(db, 'memory', [aid]))
        apply_feedback(db, PerceptionFeedback(
            message_id=mid, category='work', note='这封邮件其实是工作相关'
        ))
        after = len(rows(db, 'memory', [aid]))
        assert after > before

    def test_feedback_priority(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        perceive(db, mid)
        result = apply_feedback(db, PerceptionFeedback(
            message_id=mid, priority='high'
        ))
        assert result['priority'] == 'high'

    def test_feedback_needs_reply(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        perceive(db, mid)
        result = apply_feedback(db, PerceptionFeedback(
            message_id=mid, needs_reply=True
        ))
        assert result['needs_reply'] is True


# ============================================================
# 待办候选测试
# ============================================================

class TestTodoCandidates:
    def test_demo_perceive_creates_todo_candidates(self, db):
        aid = account(db)
        mid = make_message(db, aid, body='请在周五前提交项目进度报告')
        perceive(db, mid)
        todos = list_pending_todos(db, aid)
        assert len(todos) == 1
        assert todos[0]['body']['source_message'] == mid

    def test_list_pending_todos_empty(self, db):
        aid = account(db)
        todos = list_pending_todos(db, aid)
        assert todos == []

    def test_perception_todo_has_source(self, db):
        aid = account(db)
        mid = make_message(db, aid, body='请在周五前提交项目进度报告')
        perceive(db, mid)
        todos = list_pending_todos(db, aid)
        for t in todos:
            assert t['body'].get('source') == 'perception'
            assert t['body'].get('source_message') == mid


# ============================================================
# 集成测试：收取后自动触发感知
# ============================================================

class TestIntegration:
    def test_store_message_enqueues_perception_job(self, db):
        aid = account(db)
        mid = make_message(db, aid)
        # 检查是否有 perception 任务入队（任务存在 mail_jobs 表）
        from sqlalchemy import text
        with db.engine.connect() as c:
            rows = c.execute(
                text("SELECT * FROM mail_jobs WHERE account_id=:a AND kind='perception'"),
                {'a': aid}
            ).mappings().all()
        assert len(rows) >= 1
        import json
        payload = json.loads(rows[0]['payload'])
        assert payload['message_id'] == mid
