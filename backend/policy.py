from datetime import datetime, timedelta, timezone
from .db import now

LOW = {'create_todo','create_reminder','create_memory_candidate','draft_email','summarize'}
MEDIUM = {'update_todo','create_calendar','update_calendar','sync_todo','sync_calendar'}
HIGH = {'send_email','send_feishu','delete_item','invite_calendar'}

def key(action):
    a = action['args']
    return '|'.join([action['tool'], str(a.get('recipient') or a.get('id') or 'self'), action.get('scenario','general')])

def risk(action):
    return 'LOW' if action['tool'] in LOW else 'MEDIUM' if action['tool'] in MEDIUM else 'HIGH'

def decide(db, action, trusted_ids=None):
    if action['tool'] not in LOW | MEDIUM | HIGH:
        return 'DENY'
    if action.get('clarification') or action.get('confidence', 0) < .7:
        return 'CLARIFY'
    if risk(action) == 'HIGH':
        return 'ASK'
    if risk(action) == 'LOW':
        return 'ALLOW'
    for rule in db.list('trust', status='published'):
        if rule['body']['key'] == key(action) and (trusted_ids is None or rule['id'] in trusted_ids):
            return 'ALLOW'
    return 'ASK'

def suspend(db, action):
    for rule in db.list('trust', status='published'):
        if rule['body']['key'] == key(action):
            db.update(rule['id'], status='suspended')

def suggest(db):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    groups = {}
    for row in db.list('decision', limit=10000):
        b = row['body']
        if row['created_at'] >= cutoff and b['risk'] == 'MEDIUM':
            groups.setdefault(b['key'], []).append(b)
    existing = {r['body']['key'] for r in db.list('trust', limit=10000) if r['status'] in {'candidate','published'}}
    for k, rows in groups.items():
        success = sum(r['decision'] == 'approve' for r in rows) / len(rows)
        recent_good = all(r['decision'] == 'approve' and r.get('result') == 'completed' for r in rows[:10])
        if len(rows) >= 20 and success >= .95 and recent_good and k not in existing:
            db.insert('trust', {'key': k, 'sample_count':len(rows),'approval_rate':success,'policy_version':1,'evaluated_at':now()}, status='candidate')
