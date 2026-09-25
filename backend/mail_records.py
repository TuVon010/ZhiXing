"""Local record management. Legacy data is inventoried, never inferred to be test data."""
import json
import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from .mail_store import enqueue, require

router = APIRouter(prefix='/mail/records')


def database():
    from .main import store
    return store


MANAGED = {'mail_message', 'mail_draft', 'todo', 'calendar', 'reminder',
           'notification', 'memory', 'assistant_session'}
KINDS = MANAGED | {'run', 'assistant_turn'}


def overview(db):
    with db.engine.connect() as c:
        counts = {kind: count for kind, count in c.execute(text(
            "SELECT kind,COUNT(*) FROM records WHERE status!='trashed' GROUP BY kind"))}
        old_messages = c.execute(text("""SELECT COUNT(*) FROM records WHERE kind='message'
            AND json_extract(body,'$.source') IN ('email','demo')""")).scalar_one()
        old_runs = c.execute(text("""SELECT COUNT(*) FROM records WHERE kind='run'
            AND json_extract(body,'$.message.source') IN ('email','demo')""")).scalar_one()
        old_pending = c.execute(text("""SELECT COUNT(*) FROM records WHERE kind='run'
            AND status IN ('queued','running','waiting_approval')
            AND json_extract(body,'$.message.source') IN ('email','demo')""")).scalar_one()
        current_mail = c.execute(text("SELECT COUNT(*) FROM records WHERE kind='mail_message' AND status!='trashed'")).scalar_one()
        trash = c.execute(text("SELECT COUNT(*) FROM records WHERE status='trashed'")).scalar_one()
    return {'current_mail_messages': current_mail, 'legacy_messages': old_messages,
            'legacy_runs': old_runs, 'legacy_pending_runs': old_pending,
            'trash': trash, 'counts': counts,
            'legacy_note': '旧 email 来源没有可靠的测试/真实标记；旧资料保留且不自动清理。'}


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
    if kind == 'legacy_message':
        clause = "kind='message' AND json_extract(body,'$.source') IN ('email','demo')"
        args = {}
    else:
        clause = 'kind=:kind'
        args = {'kind': kind}
        if kind in {'todo','calendar','reminder','notification'}:
            clause += " AND scope LIKE 'web:mail:%'"
        elif kind == 'run':
            clause += " AND json_array_length(json_extract(body,'$.message.metadata.mail_accounts'))>0"
        elif kind == 'memory':
            clause += " AND (scope='global' OR scope IN (SELECT id FROM records WHERE kind='mail_account'))"
        if account_id:
            if kind in {'todo','calendar','reminder','notification'}:
                clause += ' AND scope IN (:account,:workspace)'
                args.update(account=account_id, workspace='web:mail:' + account_id)
            elif kind == 'assistant_session':
                clause += " AND EXISTS (SELECT 1 FROM json_each(json_extract(body,'$.account_ids')) WHERE value=:account)"
                args['account'] = account_id
            elif kind in {'run','assistant_turn'}:
                clause += " AND EXISTS (SELECT 1 FROM json_each(CASE WHEN kind='run' THEN json_extract(body,'$.message.metadata.mail_accounts') ELSE json_extract(body,'$.account_ids') END) WHERE value=:account)"
                args['account'] = account_id
            elif kind == 'memory':
                clause += ' AND scope IN (:account,:global_scope)'
                args.update(account=account_id, global_scope='global')
            else:
                clause += ' AND scope=:account'
                args['account'] = account_id
    if kind != 'legacy_message':
        if kind in {'todo','calendar','reminder'}:
            clause += " AND status IN ('trashed','deleted')" if trashed else " AND status NOT IN ('trashed','deleted')"
        else:
            clause += " AND status='trashed'" if trashed else " AND status!='trashed'"
    args.update(limit=limit, offset=offset)
    with db.engine.connect() as c:
        total = c.execute(text('SELECT COUNT(*) FROM records WHERE ' + clause), args).scalar_one()
        records = c.execute(text('SELECT id,kind,status,scope,body,created_at FROM records WHERE '
                                 + clause + ' ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset'), args).mappings().all()
    items = []
    for row in records:
        body = json.loads(row['body'])
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
        with db.engine.connect() as c:
            pending = c.execute(text("SELECT 1 FROM records WHERE kind='assistant_turn' AND scope=:id AND status IN ('queued','running') LIMIT 1"), {'id': ident}).first()
        if pending:
            raise ValueError('会话仍有运行中的任务')
    if row['kind'] in {'todo','calendar','reminder'}:
        if not row['scope'].startswith('web:mail:'):
            raise ValueError('旧项目事项保留在历史资料中')
    if row['kind'] == 'notification' and not row['scope'].startswith('web:mail:'):
        raise ValueError('旧项目通知保留在历史资料中')
    db.update(ident, {**row['body'], '_record_previous_status': row['status']}, 'trashed')
    if row['kind'] in {'todo','calendar'}:
        source = 'source_todo' if row['kind'] == 'todo' else 'source_calendar'
        with db.engine.begin() as c:
            c.execute(text("""UPDATE records SET status='cancelled'
                WHERE kind='reminder' AND scope=:scope AND status='active'
                AND json_extract(body,:path)=:id"""),
                {'scope':row['scope'],'path':'$.'+source,'id':ident})
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
        from .mail_work_items import sync_todo_reminder
        sync_todo_reminder(db, ident)
    if row['kind'] == 'calendar':
        from .mail_schedule import sync_calendar_reminder
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
