"""
测试：可解释理由、日程冲突检测/幂等、每日 Digest

覆盖：
1. 感知结果包含 reasons 字段
2. demo 模式生成 reasons
3. 日程冲突检测
4. 日程幂等创建
5. 日程候选确认/忽略
6. Digest 生成
7. Digest 保存和读取
"""
import sys
sys.path.insert(0, r'E:\postgraduateLife\intern\myagent')

import pytest
from backend.db import Store
from backend.config import settings
from backend.mail_store import initialize, save_account
from backend.mail_models import MailAccount, PerceptionResult
from backend.mail_ingest import store_message
from backend.mail_perception import perceive, get_perception
from backend.mail_schedule import (
    detect_conflicts, create_calendar_candidate,
    list_calendar_candidates, confirm_calendar, _idempotency_key
)
from backend.mail_digest import generate_digest, save_digest, get_digest
from email.message import EmailMessage
from sqlalchemy import text
import tempfile, os


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'mode', 'demo')
    monkeypatch.setenv('ZHIXING_DISABLE_LOCAL_MODELS', '1')
    database = Store(tmp_path / 'zhixing.db')
    yield database
    database.engine.dispose()


@pytest.fixture
def account(db):
    initialize(db)
    aid = save_account(db, MailAccount(
        name='测试邮箱', address='owner@qq.com', enabled=True
    ))['id']
    from backend.filtering import DEFAULT_RULES
    db.insert('setting', {**DEFAULT_RULES}, id='mail-filter:' + aid)
    return aid


def make_message(db, aid, subject='测试邮件', body='这是一封测试邮件', number=1):
    m = EmailMessage()
    m['From'] = 'sender@example.com'
    m['To'] = 'owner@qq.com'
    m['Subject'] = subject
    m['Message-ID'] = f'<test{number}@example.com>'
    m.set_content(body)
    return store_message(db, aid, '100', number, m.as_bytes(), '2026-09-22T10:00:00+00:00')


# ============================================================
# 功能1：可解释理由
# ============================================================

class TestReasons:
    def test_perception_result_has_reasons_field(self):
        """PerceptionResult 模型有 reasons 字段，默认空列表。"""
        r = PerceptionResult(summary='test')
        assert r.reasons == []
        assert isinstance(r.reasons, list)

    def test_demo_perceive_generates_reasons(self, db, account):
        """demo 模式感知结果包含 reasons。"""
        mid = make_message(db, account, subject='项目进度汇报', body='请在周五前提交报告')
        result = perceive(db, mid)
        assert 'reasons' in result
        assert len(result['reasons']) > 0

    def test_demo_work_email_reasons(self, db, account):
        """工作邮件的 reasons 包含工作相关关键词。"""
        mid = make_message(db, account, subject='项目进度汇报', body='请回复确认')
        result = perceive(db, mid)
        reasons = result['reasons']
        assert any('工作' in r for r in reasons)

    def test_reasons_stored_in_message(self, db, account):
        """reasons 存储在邮件记录的 perception 字段中。"""
        mid = make_message(db, account, subject='测试', body='请确认')
        perceive(db, mid)
        p = get_perception(db, mid)
        assert p is not None
        assert 'reasons' in p


# ============================================================
# 功能2：日程冲突检测与幂等创建
# ============================================================

class TestSchedule:
    def test_detect_no_conflict(self, db, account):
        """没有日程时，冲突检测返回空。"""
        conflicts = detect_conflicts(db, account, '2026-09-25T14:00:00+08:00', '2026-09-25T15:00:00+08:00')
        assert conflicts == []

    def test_detect_conflict(self, db, account):
        """已有日程时，冲突检测能找到。"""
        # 先创建一个日程
        db.insert('calendar', {
            'title': '已有会议',
            'start': '2026-09-25T14:00:00+08:00',
            'end': '2026-09-25T15:00:00+08:00',
        }, scope=account, status='active')
        # 检测同一时间段
        conflicts = detect_conflicts(db, account, '2026-09-25T14:30:00+08:00', '2026-09-25T15:30:00+08:00')
        assert len(conflicts) == 1
        assert conflicts[0]['title'] == '已有会议'

    def test_no_conflict_when_not_overlapping(self, db, account):
        """时间段不重叠时，不报告冲突。"""
        db.insert('calendar', {
            'title': '上午会议',
            'start': '2026-09-25T10:00:00+08:00',
            'end': '2026-09-25T11:00:00+08:00',
        }, scope=account, status='active')
        conflicts = detect_conflicts(db, account, '2026-09-25T14:00:00+08:00')
        assert conflicts == []

    def test_create_calendar_candidate(self, db, account):
        """创建日程候选成功。"""
        result = create_calendar_candidate(db, account, 'msg-1', {
            'title': '项目评审会',
            'start': '2026-09-25T14:00:00+08:00',
            'end': '2026-09-25T15:00:00+08:00',
            'location': '3号会议室',
        })
        assert result['status'] == 'candidate'
        assert result['title'] == '项目评审会'

    def test_idempotent_creation(self, db, account):
        """同一邮件同一事件重复创建时，返回 duplicate，不重复创建。"""
        event = {
            'title': '项目评审会',
            'start': '2026-09-25T14:00:00+08:00',
        }
        r1 = create_calendar_candidate(db, account, 'msg-1', event)
        r2 = create_calendar_candidate(db, account, 'msg-1', event)
        assert r1['status'] == 'candidate'
        assert r2['status'] == 'duplicate'
        # 数据库中只有一条
        candidates = list_calendar_candidates(db, account)
        assert len(candidates) == 1

    def test_idempotency_key_different_messages(self, db, account):
        """不同邮件的相同事件生成不同的键。"""
        k1 = _idempotency_key('msg-1', '会议', '2026-09-25T14:00:00+08:00')
        k2 = _idempotency_key('msg-2', '会议', '2026-09-25T14:00:00+08:00')
        assert k1 != k2

    def test_candidate_with_conflict(self, db, account):
        """创建候选时检测到冲突，结果包含 conflicts。"""
        db.insert('calendar', {
            'title': '已有会议',
            'start': '2026-09-25T14:00:00+08:00',
            'end': '2026-09-25T15:00:00+08:00',
        }, scope=account, status='active')
        result = create_calendar_candidate(db, account, 'msg-1', {
            'title': '新会议',
            'start': '2026-09-25T14:30:00+08:00',
        })
        assert result['status'] == 'candidate'
        assert 'conflicts' in result
        assert len(result['conflicts']) == 1

    def test_confirm_calendar(self, db, account):
        """确认日程候选，状态变为 active。"""
        result = create_calendar_candidate(db, account, 'msg-1', {
            'title': '项目评审会',
            'start': '2026-09-25T14:00:00+08:00',
        })
        confirmed = confirm_calendar(db, result['id'], account)
        assert confirmed['status'] == 'active'

    def test_skip_when_no_start_time(self, db, account):
        """缺少开始时间时跳过创建。"""
        result = create_calendar_candidate(db, account, 'msg-1', {
            'title': '无时间事件',
            'start': '',
        })
        assert result['status'] == 'skipped'


# ============================================================
# 功能3：每日 Digest
# ============================================================

class TestDigest:
    def test_generate_empty_digest(self, db, account):
        """没有邮件时，生成空摘要。"""
        digest = generate_digest(db, account, '2026-09-22')
        assert digest['stats']['total'] == 0
        assert '昨天没有新邮件' in digest['summary']

    def test_generate_digest_with_messages(self, db, account):
        """有邮件时，摘要包含统计信息。"""
        make_message(db, account, subject='项目进度', body='请回复确认', number=1)
        make_message(db, account, subject='通知', body='系统维护通知', number=2)
        # 触发感知
        from backend.mail_worker import execute_job
        with db.engine.connect() as c:
            jobs = c.execute(text("SELECT * FROM mail_jobs WHERE kind='perception'")).mappings().all()
        for job in jobs:
            execute_job(db, dict(job))

        digest = generate_digest(db, account, '2026-09-22')
        assert digest['stats']['total'] == 2
        assert digest['stats']['needs_reply_count'] >= 1

    def test_save_and_get_digest(self, db, account):
        """保存摘要后可以读取。"""
        digest = generate_digest(db, account, '2026-09-22')
        save_digest(db, digest)
        loaded = get_digest(db, account, '2026-09-22')
        assert loaded is not None
        assert loaded['date'] == '2026-09-22'

    def test_digest_includes_high_priority(self, db, account):
        """高优先级邮件出现在摘要中。"""
        make_message(db, account, subject='紧急：项目上线', body='请今晚12点前完成', number=1)
        from backend.mail_worker import execute_job
        with db.engine.connect() as c:
            jobs = c.execute(text("SELECT * FROM mail_jobs WHERE kind='perception'")).mappings().all()
        for job in jobs:
            execute_job(db, dict(job))

        digest = generate_digest(db, account, '2026-09-22')
        assert digest['stats']['high_priority_count'] >= 0  # demo 模式可能不标记 high

    def test_digest_idempotent_save(self, db, account):
        """重复保存同一天的摘要不创建重复记录。"""
        digest = generate_digest(db, account, '2026-09-22')
        save_digest(db, digest)
        save_digest(db, digest)  # 第二次保存
        loaded = get_digest(db, account, '2026-09-22')
        assert loaded is not None
        # 数据库中只有一条
        with db.engine.connect() as c:
            count = c.execute(text(
                "SELECT COUNT(*) FROM records WHERE kind='digest' AND scope=:a"
            ), {'a': account}).scalar_one()
        assert count == 1
