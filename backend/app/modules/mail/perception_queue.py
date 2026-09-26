"""Account-scoped visibility and bounded, explicit backfill for mail perception."""
from pydantic import BaseModel, Field
from sqlalchemy import text

from backend.app.modules.mail.repository import enqueue, require


class BatchRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    retry_failed: bool = False


def _state(db, account_id: str):
    require(db, account_id, 'mail_account')
    with db.engine.connect() as conn:
        messages = conn.execute(text("""
            SELECT id, status, json_type(body, '$.perception') AS perception
            FROM records WHERE kind='mail_message' AND scope=:account
            ORDER BY created_at DESC, id DESC
        """), {'account': account_id}).mappings().all()
        jobs = conn.execute(text("""
            SELECT id, status, json_extract(payload, '$.message_id') AS message_id
            FROM mail_jobs WHERE kind='perception' AND account_id=:account
            ORDER BY created_at DESC, id DESC
        """), {'account': account_id}).mappings().all()
    latest = {}
    for job in jobs:
        if job['message_id'] and job['message_id'] not in latest:
            latest[job['message_id']] = dict(job)
    return messages, latest


def summary(db, account_id: str):
    messages, latest = _state(db, account_id)
    counts = {'total': 0, 'filtered': 0, 'eligible': 0, 'queued': 0,
              'running': 0, 'failed': 0, 'ready': 0}
    for mail in messages:
        if mail['status'] in {'trashed', 'deleted', 'legacy'}:
            continue
        counts['total'] += 1
        if mail['perception']:
            counts['ready'] += 1
        if mail['status'] == 'filtered':
            counts['filtered'] += 1
        elif mail['status'] in {'active', 'review'} and not mail['perception']:
            status = latest.get(mail['id'], {}).get('status')
            counts[status if status in {'queued', 'running', 'failed'} else 'eligible'] += 1
    return counts


def queue_batch(db, account_id: str, request: BatchRequest):
    messages, latest = _state(db, account_id)
    queued = []
    for mail in messages:
        if len(queued) >= request.limit:
            break
        if mail['status'] not in {'active', 'review'} or mail['perception']:
            continue
        previous = latest.get(mail['id'])
        if previous and (previous['status'] != 'failed' or not request.retry_failed):
            continue
        dedupe = ('perception-retry:' + previous['id'] if previous
                  else 'perception-backfill:' + mail['id'])
        queued.append(enqueue(db, 'perception', {'message_id': mail['id'], 'manual': True},
                              account_id, priority=5, dedupe=dedupe))
    db.audit('system', 'MAIL_PERCEPTION_BATCH', account_id=account_id,
             count=len(queued), retry_failed=request.retry_failed)
    return {'queued': len(queued), 'job_ids': queued, 'summary': summary(db, account_id)}
