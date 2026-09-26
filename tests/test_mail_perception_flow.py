"""End-to-end contracts for opt-in perception, review, and local follow-up work."""
from email.message import EmailMessage

from fastapi.testclient import TestClient
from sqlalchemy import text

from backend import main
from backend.app.core.config import settings
from backend.app.modules.mail.routes.perception import confirm_perception_todo, confirm_perception_calendar
from backend.app.modules.mail.routes.work_items import followups
from backend.app.modules.mail.routes.messages import messages
from backend.app.modules.mail.digest import generate_digest
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.schemas import MailAccount
from backend.app.observability.mail import trace
from backend.app.modules.mail.perception import perceive, _store_result
from backend.app.modules.mail.calendar import detect_conflicts
from backend.app.modules.mail.repository import initialize, save_account
from backend.app.workers.mail_jobs import execute_job


def account(db, *, auto=False, limit=20):
    initialize(db)
    return save_account(db, MailAccount(name='合成邮箱', address='owner@qq.com', enabled=True,
                                        auto_analyze=auto, hourly_analysis_limit=limit))['id']


def message(db, aid, number, received='2026-09-22T10:00:00+00:00', subject='报告'):
    mail = EmailMessage()
    mail['From'] = 'teacher@example.com'
    mail['To'] = 'owner@qq.com'
    mail['Subject'] = subject
    mail['Message-ID'] = f'<flow-{number}@example.com>'
    mail.set_content('请在周五前提交报告。')
    return store_message(db, aid, '100', number, mail.as_bytes(), received)


def test_perception_is_opt_in_and_shares_hourly_limit(db):
    aid = account(db, limit=1)
    message(db, aid, 1)
    with db.engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM mail_jobs WHERE kind='perception'")).scalar_one() == 0
    row = db.get(aid)
    db.update(aid, {**row['body'], 'auto_analyze': True})
    first, second = message(db, aid, 2), message(db, aid, 3)
    with db.engine.connect() as conn:
        jobs = [dict(r) for r in conn.execute(text("SELECT * FROM mail_jobs WHERE kind='perception' ORDER BY created_at,id")).mappings()]
    results = [execute_job(db, job) for job in jobs]
    assert len(results) == 2
    assert sum('summary' in result for result in results) == 1
    assert sum(result.get('skipped') == 'hourly_analysis_limit' for result in results) == 1
    assert sum(bool(db.get(mid)['body'].get('perception')) for mid in (first, second)) == 1
    assert any(job['kind'] == 'perception' for job in trace(db, first)['jobs'])


def test_feedback_http_contract_and_confirmed_memory(db, monkeypatch):
    aid = account(db)
    mid = message(db, aid, 1)
    perceive(db, mid)
    monkeypatch.setattr('backend.app.persistence.store.store', db)
    with TestClient(main.app) as client:
        assert client.get('/api/session').status_code == 200
        path = f'/api/mail/messages/{mid}/perception/feedback'
        response = client.post(path, headers={'X-ZhiXing-Local': '1'}, json={'category': 'personal'})
        assert response.status_code == 200
        assert response.json()['category'] == 'personal'
        memories = db.list('memory', scope=aid)
        assert len(memories) == 1 and memories[0]['status'] == 'candidate'
        assert client.post(path, headers={'X-ZhiXing-Local': '1'}, json={'category': 'personal'}).status_code == 200
        assert len(db.list('memory', scope=aid)) == 1
        assert client.post(path, headers={'X-ZhiXing-Local': '1'}, json={'spam_score': 0.9}).status_code == 200
        assert db.get(mid)['status'] == 'filtered'
        assert mid not in [r['id'] for r in messages(aid, 'inbox', 50, 0, db)['items']]
        assert client.post(path, headers={'X-ZhiXing-Local': '1'}, json={'spam_score': 0.0}).status_code == 200
        assert db.get(mid)['status'] == 'active'
        assert mid in [r['id'] for r in messages(aid, 'inbox', 50, 0, db)['items']]


def test_ai_filter_respects_whitelist_and_actionable_mail(db):
    aid = account(db)
    db.insert('setting', {'whitelist_senders': ['teacher@example.com']}, id='mail-filter:' + aid)
    mid = message(db, aid, 1)
    _store_result(db, mid, {'spam_score': 0.99, 'confidence': 0.99, 'summary': '测试'})
    assert db.get(mid)['status'] == 'active'
    db.update('mail-filter:' + aid, {'whitelist_senders': []})
    _store_result(db, mid, {'spam_score': 0.99, 'confidence': 0.99, 'todos': [{'action': '提交报告'}]})
    assert db.get(mid)['status'] == 'active'
    _store_result(db, mid, {'spam_score': 0.99, 'confidence': 0.99})
    assert db.get(mid)['status'] == 'filtered'


def test_live_result_creates_reviewable_work_and_reuses_existing_calendar(db, monkeypatch):
    aid = account(db)
    mid = message(db, aid, 1)
    db.insert('calendar', {'title': '原有会议', 'start': '2026-09-25T14:00:00+08:00',
                           'end': '2026-09-25T15:00:00+08:00'},
              scope='web:mail:' + aid)
    assert len(detect_conflicts(db, aid, '2026-09-25T14:30:00+08:00')) == 1
    monkeypatch.setattr(settings, 'mode', 'live')
    from backend.app.agent import model_client as planner
    seen = []
    def fake_model_json(db, messages, schema, purpose):
        seen.append((planner.trace_run.get(), purpose))
        return {'summary': '提交报告并参加评审', 'todos': [{'action': '提交报告'}],
                'calendar_events': [{'title': '评审', 'start': '2026-09-25T14:30:00+08:00'}]}
    monkeypatch.setattr(planner, 'model_json', fake_model_json)
    perceive(db, mid)
    perceive(db, mid)
    assert seen == [(mid, 'mail_perception')] * 2
    items = followups(aid, db)['items']
    candidates = [r for r in items if r['status'] == 'candidate']
    assert len(candidates) == 2
    todo = next(r for r in candidates if r['kind'] == 'todo')
    calendar = next(r for r in candidates if r['kind'] == 'calendar')
    assert calendar['body']['has_conflict']
    confirm_perception_todo(todo['id'], db)
    confirm_perception_calendar(calendar['id'], db)
    assert all(r['status'] == 'active' and r['scope'] == 'web:mail:' + aid
               for r in (db.get(todo['id']), db.get(calendar['id'])))
    assert db.get('perception-reminder:' + calendar['id'])['scope'] == 'web:mail:' + aid
    assert confirm_perception_calendar(calendar['id'], db)['already_confirmed']


def test_digest_uses_shanghai_day_for_utc_timestamps(db):
    aid = account(db)
    message(db, aid, 1, '2026-09-21T18:00:00+00:00')  # 9/22 02:00 Shanghai
    message(db, aid, 2, '2026-09-22T20:00:00+00:00')  # 9/23 04:00 Shanghai
    assert generate_digest(db, aid, '2026-09-22')['stats']['total'] == 1
    assert generate_digest(db, aid, '2026-09-23')['stats']['total'] == 1
