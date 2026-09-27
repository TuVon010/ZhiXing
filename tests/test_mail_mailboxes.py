"""Mailbox state projections stay mutually exclusive and account scoped."""

from email.message import EmailMessage

from backend.app.modules.mail.ingestion import store_message
from backend.app.modules.mail.repository import initialize, save_account
from backend.app.modules.mail.schemas import MailAccount
from backend.app.modules.mail.services.messages import list_messages


def _account(db, name: str, address: str, test: bool = False) -> str:
    initialize(db)
    account_id = save_account(db, MailAccount(
        name=name,
        address=address,
        enabled=True,
    ))['id']
    if test:
        row = db.get(account_id)
        db.update(account_id, {**row['body'], 'test_account': True})
    return account_id


def _message(db, account_id: str, uid: int, subject: str) -> str:
    message = EmailMessage()
    message['From'] = 'sender@example.com'
    message['To'] = 'owner@example.com'
    message['Subject'] = subject
    message['Message-ID'] = f'<mailbox-{account_id}-{uid}@example.com>'
    message.set_content('mailbox state test')
    return store_message(db, account_id, '100', uid, message.as_bytes(), '2026-09-27T01:00:00+00:00')


def test_mailboxes_do_not_leak_between_states(db):
    account_id = _account(db, 'main', 'owner@example.com')
    active = _message(db, account_id, 1, 'active')
    archived = _message(db, account_id, 2, 'archived')
    review = _message(db, account_id, 3, 'review')
    filtered = _message(db, account_id, 4, 'filtered')
    trashed = _message(db, account_id, 5, 'trashed')
    db.update(active, status='active')
    db.update(archived, status='archived')
    db.update(review, status='review')
    db.update(filtered, status='filtered')
    db.update(trashed, status='trashed')

    inbox = list_messages(db, account_id=account_id, status='inbox')['items']
    archive = list_messages(db, account_id=account_id, status='archived')['items']
    filterbox = list_messages(db, account_id=account_id, status='filterbox')['items']
    trash = list_messages(db, account_id=account_id, status='trashed')['items']

    assert [row['id'] for row in inbox] == [active]
    assert [row['id'] for row in archive] == [archived]
    assert {row['id'] for row in filterbox} == {review, filtered}
    assert [row['id'] for row in trash] == [trashed]


def test_unified_mailboxes_hide_test_accounts_by_default(db):
    real = _account(db, 'real', 'real@example.com')
    test = _account(db, 'demo', 'demo@example.com', test=True)
    real_mail = _message(db, real, 1, 'real')
    demo_mail = _message(db, test, 1, 'demo')
    db.update(real_mail, status='active')
    db.update(demo_mail, status='active')
    visible = list_messages(db, status='inbox', include_test=False)['items']
    assert [row['id'] for row in visible] == [real_mail]
