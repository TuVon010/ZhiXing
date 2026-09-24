"""Record manager: account scope, reversible cleanup and legacy provenance."""
from email.message import EmailMessage
from pathlib import Path

import pytest

from backend.mail_api import messages, followups
from backend.mail_ingest import store_message
from backend.mail_models import MailAccount
from backend.mail_observability import activity, notifications, trace
from backend.mail_records import (overview, list_records, trash_record, restore_record,
                                  hide_activity, unhide_activity, backup_legacy)
from backend.mail_store import initialize, save_account
from backend.runtime import work_once, approve, ingest


def account(db, number=1):
    initialize(db)
    return save_account(db, MailAccount(name=f'邮箱{number}', address=f'owner{number}@qq.com'))['id']


def email(db, aid, number=1):
    msg = EmailMessage()
    msg['From'] = 'teacher@example.com'
    msg['To'] = 'owner1@qq.com'
    msg['Subject'] = '项目报告'
    msg['Message-ID'] = f'<record-{number}@example.com>'
    msg.set_content('请提交项目报告。')
    return store_message(db, aid, '100', number, msg.as_bytes(), '2026-09-22T10:00:00+00:00')


def test_old_project_records_are_identified_but_not_assumed_to_be_tests(db):
    old = db.insert('message', {'source': 'email', 'text': '来源未知'})
    db.insert('run', {'message': {'source': 'email'}}, status='completed')
    aid = account(db)
    email(db, aid)
    summary = overview(db)
    assert summary['legacy_messages'] == 1
    assert summary['legacy_runs'] == 1
    assert summary['current_mail_messages'] == 1
    assert '无法' not in summary['legacy_note'] or '测试/真实' in summary['legacy_note']
    listed = list_records('legacy_message', '', False, 30, 0, db)
    assert listed['items'][0]['id'] == old
    assert '来源未知' not in str(listed)
    with pytest.raises(ValueError):
        trash_record(old, db)
    assert db.get(old)['status'] == 'active'


def test_mail_trash_restore_and_account_isolation(db):
    aid, other = account(db), account(db, 2)
    mid, other_mid = email(db, aid), email(db, other, 2)
    original = db.get(mid)['status']
    assert list_records('mail_message', aid, False, 30, 0, db)['total'] == 1
    assert list_records('mail_message', other, False, 30, 0, db)['items'][0]['id'] == other_mid
    assert trash_record(mid, db)['status'] == 'trashed'
    assert mid not in [r['id'] for r in messages(aid, 'inbox', 50, 0, db)['items']]
    assert list_records('mail_message', aid, True, 30, 0, db)['total'] == 1
    assert restore_record(mid, db)['status'] == original
    assert mid in [r['id'] for r in messages(aid, 'inbox', 50, 0, db)['items']]


def test_notification_and_draft_can_be_recovered_but_sent_draft_cannot_be_trashed(db):
    aid = account(db)
    notice = db.insert('notification', {'title': '本地提醒'}, status='local', scope='web:mail:' + aid)
    assert notifications(aid, 30, 0, db)['total'] == 1
    trash_record(notice, db)
    assert notifications(aid, 30, 0, db)['total'] == 0
    restore_record(notice, db)
    assert notifications(aid, 30, 0, db)['total'] == 1
    draft = db.insert('mail_draft', {'subject': '草稿'}, status='draft', scope=aid)
    trash_record(draft, db)
    assert db.get(draft)['status'] == 'trashed'
    restore_record(draft, db)
    assert db.get(draft)['status'] == 'draft'
    db.update(draft, status='smtp_accepted')
    with pytest.raises(ValueError):
        trash_record(draft, db)


def test_deleting_active_followup_still_requires_approval(db):
    aid = account(db)
    todo = db.insert('todo', {'title': '交报告'}, scope='web:mail:' + aid)
    request = trash_record(todo, db)
    assert request['status'] == 'approval_required'
    assert db.get(todo)['status'] == 'active'
    assert work_once(db)
    approval = db.list('approval', status='pending')[0]
    approve(approval['id'], {'decision': 'approve', 'version': approval['body']['version']}, db)
    assert work_once(db)
    assert db.get(todo)['status'] == 'deleted'
    assert followups(aid, db)['items'] == []
    assert list_records('todo', aid, True, 30, 0, db)['total'] == 1
    restore_record(todo, db)
    assert db.get(todo)['status'] == 'active'


def test_hiding_finished_run_preserves_trace(db):
    aid = account(db)
    rid = ingest({'message_id': 'record-review', 'source': 'web',
                  'conversation_id': 'mail:' + aid, 'text': '总结',
                  'metadata': {'mail_accounts': [aid]}}, db,
                 explicit_plan={'summary': '总结', 'actions': [{'tool': 'summarize', 'args': {'content': '测试'}, 'confidence': 1}]})
    work_once(db)
    assert activity(db, aid)['total'] == 1
    hide_activity(rid, db)
    assert activity(db, aid)['total'] == 0
    assert trace(db, rid)['runs']
    unhide_activity(rid, db)
    assert activity(db, aid)['total'] == 1


def test_legacy_backup_is_a_separate_sqlite_snapshot(db):
    old = db.insert('message', {'source': 'email', 'text': '保留原文'})
    saved = backup_legacy(db)
    snapshot = Path(saved['backup_path']) / db.path.name
    assert snapshot.exists()
    import sqlite3
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute('SELECT COUNT(*) FROM records WHERE id=?', (old,)).fetchone()[0] == 1
    assert db.get(old)['body']['text'] == '保留原文'
