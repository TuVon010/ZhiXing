"""
日程管理模块（Schedule Manager）。

提供日程冲突检测和幂等创建功能。
- 冲突检测：创建日程前检查该时间段是否已有日程
- 幂等创建：同一封邮件的同一事件不会重复创建

设计原则：
1. 只检测，不自动解决冲突——把冲突信息告诉用户，由用户决定
2. 幂等键 = message_id + event_title + start_time，确保重复收取不重复创建
3. 候选状态为 'candidate'，需要用户确认后才变为 'active'
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from .db import now, uid
from .mail_store import require, initialize


def _parse_iso(time_str: str) -> datetime | None:
    """安全解析 ISO 8601 时间字符串，失败返回 None。"""
    if not time_str:
        return None
    try:
        return datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return None


def detect_conflicts(db, account_id: str, start: str, end: str | None = None,
                     exclude_id: str | None = None) -> list[dict]:
    """
    检测指定时间段内的日程冲突。

    Args:
        db: 数据库连接
        account_id: 账号 ID
        start: 开始时间（ISO 8601）
        end: 结束时间（ISO 8601），为 None 时默认开始时间后 1 小时
        exclude_id: 排除的日程 ID（用于更新时排除自身）

    Returns:
        冲突的日程列表，每个包含 id、title、start、end
    """
    initialize(db)
    start_dt = _parse_iso(start)
    if not start_dt:
        return []

    # 默认时长 1 小时
    end_dt = _parse_iso(end) if end else start_dt + timedelta(hours=1)
    if not end_dt or end_dt <= start_dt:
        end_dt = start_dt + timedelta(hours=1)

    with db.engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT id, body FROM records
                WHERE kind='calendar'
                  AND scope IN (:account_id,:workspace)
                  AND status IN ('active', 'candidate')
                  AND json_extract(body, '$.start') IS NOT NULL
            """),
            {'account_id': account_id, 'workspace': 'web:mail:' + account_id}
        ).mappings().all()

    conflicts = []
    for row in rows:
        if exclude_id and row['id'] == exclude_id:
            continue
        body = row['body'] if isinstance(row['body'], dict) else __import__('json').loads(row['body'])
        event_start = _parse_iso(body.get('start'))
        event_end = _parse_iso(body.get('end'))
        if not event_start:
            continue
        if not event_end:
            event_end = event_start + timedelta(hours=1)

        # 时间段重叠检测：A.start < B.end AND B.start < A.end
        if start_dt < event_end and event_start < end_dt:
            conflicts.append({
                'id': row['id'],
                'title': body.get('title', '（无标题）'),
                'start': body.get('start', ''),
                'end': body.get('end', ''),
            })

    return conflicts


def _idempotency_key(message_id: str, title: str, start: str) -> str:
    """
    生成幂等键。

    同一封邮件、同一标题、同一开始时间的事件，生成相同的键，
    确保重复收取邮件时不会重复创建日程。
    """
    raw = f"{message_id}:{title.strip().lower()}:{start}"
    return 'perception-cal:' + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _prior_thread_events(db, account_id: str, message_id: str, start: str) -> list[dict]:
    """A changed time in the same thread needs review before it replaces a calendar entry."""
    try:
        message = require(db, message_id, 'mail_message', [account_id])
    except KeyError:
        # Direct callers may create a standalone local candidate without an imported mail.
        return []
    thread_id = message['body'].get('thread_id')
    if not thread_id:
        return []
    with db.engine.connect() as c:
        rows = c.execute(text("""
            SELECT id, body FROM records WHERE kind='calendar' AND status='active'
            AND scope=:scope AND json_extract(body,'$.source_message') IS NOT NULL
        """), {'scope': 'web:mail:' + account_id}).mappings().all()
    related = []
    for row in rows:
        body = json.loads(row['body'])
        source_id = body.get('source_message')
        if source_id == message_id or body.get('start') == start:
            continue
        try:
            source = require(db, source_id, 'mail_message', [account_id])
        except (KeyError, ValueError):
            continue
        if source['body'].get('thread_id') == thread_id:
            related.append({'id': row['id'], 'title': body.get('title'), 'start': body.get('start')})
    return related


def create_calendar_candidate(db, account_id: str, message_id: str,
                              event: dict, auto_activate: bool = False,
                              decision_reason: str = '') -> dict:
    """
    从感知结果创建日程候选。

    流程：
    1. 幂等检查：如果已存在相同键的日程，直接返回
    2. 冲突检测：检查该时间段是否已有日程
    3. 创建候选（状态为 candidate，需要用户确认）

    Args:
        db: 数据库连接
        account_id: 账号 ID
        message_id: 来源邮件 ID
        event: 事件信息，包含 title、start、end、location、source_quote

    Returns:
        创建结果，包含 id、status、conflicts（如果有冲突）
    """
    initialize(db)
    title = (event.get('title') or '未命名事件').strip()[:200]
    start = event.get('start', '')
    end = event.get('end') or None
    location = (event.get('location') or '').strip()[:200]
    source_quote = (event.get('source_quote') or '').strip()[:500]

    if not start:
        return {'status': 'skipped', 'reason': '缺少开始时间'}

    # 1. 幂等检查
    idem_key = _idempotency_key(message_id, title, start)
    try:
        existing = require(db, idem_key, 'calendar')
        return {
            'id': idem_key,
            'status': 'duplicate',
            'message': '该日程已存在（幂等键命中）',
        }
    except KeyError:
        pass

    # 2. 冲突和同线程改期检测
    conflicts = detect_conflicts(db, account_id, start, end)
    prior_events = _prior_thread_events(db, account_id, message_id, start)
    if prior_events:
        auto_activate = False
        decision_reason = '同一邮件线程已有不同时间的日程，请核实改期并处理旧日程'

    # 3. 创建候选
    calendar_body = {
        'title': title,
        'start': start,
        'end': end,
        'location': location,
        'source': 'perception',
        'source_message': message_id,
        'source_quote': source_quote,
        'has_conflict': len(conflicts) > 0,
        'conflicts': conflicts,
        'prior_thread_events': prior_events,
        'created_at': now(),
        'decision': {'mode': 'auto' if auto_activate and not conflicts else 'review',
                     'reason': decision_reason if not conflicts else '存在日程冲突',
                     'policy_version': 'mail-work-v1', 'at': now()},
    }

    db.insert('calendar', calendar_body, id=idem_key, scope='web:mail:' + account_id, status='candidate')

    result = {
        'id': idem_key,
        'status': 'candidate',
        'title': title,
        'start': start,
        'end': end,
    }
    if conflicts:
        result['conflicts'] = conflicts
        result['warning'] = f'检测到 {len(conflicts)} 个时间冲突，请确认后再加入日程'

    if auto_activate and not conflicts:
        confirmed = confirm_calendar(db, idem_key, account_id)
        return {**result, **confirmed, 'auto_activated': True}

    return result


def list_calendar_candidates(db, account_id: str) -> list[dict]:
    """列出感知生成的日程候选（等待用户确认）。"""
    initialize(db)
    with db.engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT id, body, created_at FROM records
                WHERE kind='calendar'
                  AND status='candidate'
                  AND scope IN (:account_id,:workspace)
                  AND json_extract(body, '$.source')='perception'
                ORDER BY json_extract(body, '$.start') ASC
                LIMIT 50
            """),
            {'account_id': account_id, 'workspace': 'web:mail:' + account_id}
        ).mappings().all()
    return [{**dict(r), 'body': json.loads(r['body']) if isinstance(r['body'], str) else r['body']}
            for r in rows]


def confirm_calendar(db, calendar_id: str, account_id: str) -> dict:
    """
    用户确认日程候选，将状态从 candidate 变为 active。

    确认时再次检查冲突（因为候选创建后可能又有新日程）。
    """
    item = require(db, calendar_id, 'calendar', [account_id, 'web:mail:' + account_id])
    if item['status'] == 'active':
        return {'id': calendar_id, 'status': 'active', 'already_confirmed': True}
    if item['status'] != 'candidate':
        return {'status': 'error', 'message': '该日程不是候选状态'}

    body = item['body']
    conflicts = detect_conflicts(
        db, account_id,
        body.get('start', ''),
        body.get('end'),
        exclude_id=calendar_id
    )

    body['has_conflict'] = len(conflicts) > 0
    body['conflicts'] = conflicts
    body['confirmed_at'] = now()

    db.update(calendar_id, body, 'active')
    if item['scope'] == account_id:
        with db.engine.begin() as c:
            c.execute(text('UPDATE records SET scope=:scope WHERE id=:id'),
                      {'scope': 'web:mail:' + account_id, 'id': calendar_id})
    sync_calendar_reminder(db,calendar_id)

    replaced = []
    for previous in body.get('prior_thread_events', []):
        try:
            old = require(db, previous['id'], 'calendar', ['web:mail:' + account_id])
        except (KeyError, ValueError):
            continue
        if old['status'] != 'active' or old['body'].get('start') != previous.get('start'):
            continue
        db.update(old['id'], {**old['body'], 'replaced_by': calendar_id, 'replaced_at': now()}, 'cancelled')
        replaced.append(old['id'])
        try:
            reminder = require(db, 'perception-reminder:' + old['id'], 'reminder', ['web:mail:' + account_id])
            if reminder['status'] == 'active':
                db.update(reminder['id'], {**reminder['body'], 'cancelled_with_calendar': old['id']}, 'cancelled')
        except KeyError:
            pass

    result = {'id': calendar_id, 'status': 'active'}
    if replaced:
        result['replaced_events'] = replaced
    if conflicts:
        result['conflicts'] = conflicts
        result['warning'] = f'已加入日程，但存在 {len(conflicts)} 个时间冲突'
    return result


def sync_calendar_reminder(db,calendar_id):
    item=require(db,calendar_id,'calendar');reminder_id='calendar-reminder:'+calendar_id
    try:reminder=require(db,reminder_id,'reminder')
    except KeyError:
        try:
            reminder_id='perception-reminder:'+calendar_id;reminder=require(db,reminder_id,'reminder')
        except KeyError:
            reminder_id='calendar-reminder:'+calendar_id;reminder=None
    start=_parse_iso(item['body'].get('start'))
    if item['status']!='active' or not start or not start.tzinfo or start<=datetime.now(timezone.utc):
        if reminder and reminder['status']=='active':db.update(reminder_id,{**reminder['body'],'cancelled_by_calendar':calendar_id},'cancelled')
        return None
    body={'title':item['body'].get('title','日程即将开始'),'due_at':max(start-timedelta(minutes=30),datetime.now(timezone.utc)).isoformat(),
          'source_calendar':calendar_id,'source_message':item['body'].get('source_message')}
    if reminder:db.update(reminder_id,body,'active')
    else:db.insert('reminder',body,id=reminder_id,scope=item['scope'])
    return reminder_id
