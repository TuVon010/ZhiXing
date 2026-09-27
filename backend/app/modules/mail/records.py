"""Local record management. Legacy data is inventoried, never inferred to be test data."""
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from backend.app.api.dependencies import get_store
from backend.app.modules.mail.repositories.records import (
    cancel_linked_reminders,
    has_active_turn,
    list_rows,
    overview_counts,
)
from backend.app.modules.mail.repository import enqueue, require

router = APIRouter(prefix='/mail/records')


def database():
    return get_store()


MANAGED = {'mail_message', 'mail_draft', 'todo', 'calendar', 'reminder',
           'notification', 'memory', 'assistant_session'}
KINDS = MANAGED | {'run', 'assistant_turn'}


def overview(db):
    return overview_counts(db) | {
        'legacy_note': '旧 email 来源没有可靠的测试/真实标记；旧资料保留且不自动清理。'
    }


@router.get('/overview')
def get_overview(db=Depends(database)):
    return overview(db)


@router.get('/list')
def list_records(kind: str, account_id: str = '', trashed: bool = False,
                 limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0),
                 db=Depends(database)):
    if kind not in KINDS | {'legacy_message'}:
        raise ValueError('不支持管理该记录类型')
    if account_id:
        require(db, account_id, 'mail_account')
    total, records = list_rows(db,kind,account_id,trashed,limit,offset)
    items = []
    for row in records:
        body = row['body']
        title = (body.get('subject') or body.get('title') or body.get('text')
                 or body.get('content') or body.get('name') or row['id'])
        if kind == 'legacy_message':
            title = '旧来源记录（内容未展开）'
        items.append({'id': row['id'], 'kind': row['kind'], 'status': row['status'],
                      'scope': row['scope'], 'title': str(title)[:120],
                      'created_at': row['created_at'],
                      'origin': body.get('source') or body.get('origin') or '',
                      'hidden': bool(body.get('ui_hidden'))})
    return {'items': items, 'total': total, 'limit': limit, 'offset': offset}


@router.post('/{ident}/trash')
def trash_record(ident: str, db=Depends(database)):
    row = db.get(ident)
    if row['kind'] not in MANAGED or row['status'] == 'trashed':
        raise ValueError('该记录不能移入回收站')
    if row['kind'] == 'mail_draft' and row['status'] != 'draft':
        raise ValueError('只有未提交的草稿可移入回收站；已确认或已发送记录需保留审计')
    if row['kind'] in {'mail_message','mail_draft'}:
        require(db,row['scope'],'mail_account')
    if row['kind'] == 'memory' and row['status'] == 'published':
        raise ValueError('已生效记忆请先撤销，再移入回收站')
    if row['kind'] == 'memory' and row['scope'] != 'global':
        require(db,row['scope'],'mail_account')
    if row['kind'] == 'assistant_session':
        if has_active_turn(db,ident):
            raise ValueError('会话仍有运行中的任务')
    if row['kind'] in {'todo','calendar','reminder'}:
        if not row['scope'].startswith('web:mail:'):
            raise ValueError('旧项目事项保留在历史资料中')
    if row['kind'] == 'notification' and not row['scope'].startswith('web:mail:'):
        raise ValueError('旧项目通知保留在历史资料中')
    db.update(ident, {**row['body'], '_record_previous_status': row['status']}, 'trashed')
    if row['kind'] in {'todo','calendar'}:
        source = 'source_todo' if row['kind'] == 'todo' else 'source_calendar'
        cancel_linked_reminders(db,row['scope'],source,ident)
    db.audit('system', 'MAIL_RECORD_TRASHED', record_id=ident, record_kind=row['kind'])
    return {'status': 'trashed', 'id': ident}


@router.post('/{ident}/restore')
def restore_record(ident: str, db=Depends(database)):
    row = db.get(ident)
    if row['kind'] not in MANAGED or (row['status'] != 'trashed' and
                                    not (row['kind'] in {'todo','calendar','reminder'} and row['status'] == 'deleted')):
        raise ValueError('该记录不在回收站')
    body = dict(row['body'])
    previous = body.pop('_record_previous_status', 'active')
    db.update(ident, body, previous)
    if row['kind'] == 'todo':
        from backend.app.modules.mail.work_items import sync_todo_reminder
        sync_todo_reminder(db, ident)
    if row['kind'] == 'calendar':
        from backend.app.modules.mail.calendar import sync_calendar_reminder
        sync_calendar_reminder(db, ident)
    if row['kind'] == 'mail_message' and previous in {'active','archived'}:
        enqueue(db, 'index', {'message_id': ident}, row['scope'], priority=30)
    db.audit('system', 'MAIL_RECORD_RESTORED', record_id=ident, record_kind=row['kind'])
    return {'status': previous, 'id': ident}


@router.post('/{ident}/hide')
def hide_activity(ident: str, db=Depends(database)):
    row = db.get(ident)
    if row['kind'] not in {'run','assistant_turn'} or row['status'] in {'queued','running','waiting_approval'}:
        raise ValueError('仅能隐藏已结束的运行；Trace 和审计仍会保留')
    db.update(ident, {**row['body'], 'ui_hidden': True})
    return {'hidden': True, 'id': ident}


@router.post('/{ident}/unhide')
def unhide_activity(ident: str, db=Depends(database)):
    row = db.get(ident)
    if row['kind'] not in {'run','assistant_turn'}:
        raise ValueError('不是运行记录')
    body = dict(row['body'])
    body.pop('ui_hidden', None)
    db.update(ident, body)
    return {'hidden': False, 'id': ident}


@router.post('/legacy/backup')
def backup_legacy(db=Depends(database)):
    """Create an online SQLite snapshot before the user reviews any old records."""
    target = db.path.parent.parent / 'backups' / ('legacy-review-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    target.mkdir(parents=True, exist_ok=False)
    with sqlite3.connect(db.path) as src, sqlite3.connect(target / db.path.name) as dst:
        src.backup(dst)
    checkpoints = db.path.parent / 'checkpoints.db'
    if checkpoints.exists():
        with sqlite3.connect(checkpoints) as src, sqlite3.connect(target / checkpoints.name) as dst:
            src.backup(dst)
    return {'backup_path': str(target), 'legacy': overview(db)}
