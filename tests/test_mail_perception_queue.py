"""Synthetic batch-analysis tests. No real mailbox or model is contacted."""
from email.message import EmailMessage

from backend.app.modules.mail.schemas import MailAccount
from backend.app.modules.mail.repository import save_account, job
from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.perception_queue import BatchRequest, summary, queue_batch


def _mail(db, account_id, uid):
    email = EmailMessage()
    email['From'] = 'teacher@example.com'
    email['To'] = 'owner@example.com'
    email['Subject'] = '请确认会议'
    email.set_content('请在周五前确认会议时间。')
    return store_message(db, account_id, '100', uid, email.as_bytes(),
                         '2026-09-22T10:00:00+00:00')


def test_explicit_batch_is_scoped_bounded_and_idempotent(db):
    a = save_account(db, MailAccount(name='甲', address='owner@example.com'))['id']
    b = save_account(db, MailAccount(name='乙', address='other@example.com'))['id']
    ids = [_mail(db, a, n) for n in range(3)]
    _mail(db, b, 1)
    filtered = db.get(ids[2]); db.update(ids[2], status='filtered')
    assert summary(db, a)['eligible'] == 2
    first = queue_batch(db, a, BatchRequest(limit=1))
    second = queue_batch(db, a, BatchRequest(limit=20))
    third = queue_batch(db, a, BatchRequest(limit=20))
    assert (first['queued'], second['queued'], third['queued']) == (1, 1, 0)
    assert summary(db, a)['queued'] == 2
    assert summary(db, b)['eligible'] == 1
    assert all(job(db, ident)['account_id'] == a for ident in first['job_ids'] + second['job_ids'])


def test_failed_batch_requires_explicit_retry(db):
    from sqlalchemy import text
    a = save_account(db, MailAccount(name='甲', address='owner@example.com'))['id']
    _mail(db, a, 1)
    first = queue_batch(db, a, BatchRequest())['job_ids'][0]
    with db.engine.begin() as conn:
        conn.execute(text("UPDATE mail_jobs SET status='failed' WHERE id=:id"), {'id': first})
    assert summary(db, a)['failed'] == 1
    assert queue_batch(db, a, BatchRequest())['queued'] == 0
    retry = queue_batch(db, a, BatchRequest(retry_failed=True))
    assert retry['queued'] == 1 and retry['job_ids'][0] != first
    assert queue_batch(db, a, BatchRequest(retry_failed=True))['queued'] == 0


def test_archived_mail_is_not_offered_for_batch_analysis(db):
    a = save_account(db, MailAccount(name='甲', address='owner@example.com'))['id']
    ident = _mail(db, a, 1)
    db.update(ident, status='archived')
    assert summary(db, a)['eligible'] == 0
    assert queue_batch(db, a, BatchRequest())['queued'] == 0
