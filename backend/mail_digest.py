"""
每日收件箱摘要（Daily Digest）。

每天定时生成前一天的邮件汇总，包含：
- 邮件总数、分类统计
- 需要回复的邮件
- 有截止日期的待办
- 高优先级邮件
- 最紧急的事项

设计原则：
1. 只读不写：Digest 只是汇总，不修改邮件状态
2. 基于感知结果：利用主动感知的结果，不重复调用模型
3. 可追溯：每条汇总都引用具体邮件 ID
"""
import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from .db import now, uid
from .mail_store import require, initialize


def _get_shanghai_now() -> datetime:
    """获取当前上海时间。"""
    return datetime.now(timezone(timedelta(hours=8)))


def _parse_time(time_str: str) -> datetime | None:
    """安全解析时间字符串。"""
    if not time_str:
        return None
    try:
        return datetime.fromisoformat(time_str)
    except (ValueError, TypeError):
        return None


def generate_digest(db, account_id: str, date: str | None = None) -> dict:
    """
    生成指定日期的邮件摘要。

    Args:
        db: 数据库连接
        account_id: 账号 ID
        date: 日期字符串（YYYY-MM-DD），默认今天（上海时间）

    Returns:
        摘要 dict，包含统计、待回复、待办、高优先级等
    """
    initialize(db)

    # 确定日期范围（上海时间）
    if date:
        target_date = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=8)))
    else:
        target_date = _get_shanghai_now()

    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # 查询该日期范围内的邮件
    with db.engine.connect() as c:
        rows = c.execute(
            text("""
                SELECT id, body, status, created_at FROM records
                WHERE kind='mail_message'
                  AND scope=:account_id
                  AND status IN ('active', 'archived', 'review', 'filtered')
                  AND datetime(json_extract(body, '$.received_at')) >= :start
                  AND datetime(json_extract(body, '$.received_at')) < :end
                ORDER BY json_extract(body, '$.received_at') DESC
            """),
            {
                'account_id': account_id,
                'start': day_start.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                'end': day_end.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
            }
        ).mappings().all()

    messages = []
    for row in rows:
        body = row['body'] if isinstance(row['body'], dict) else json.loads(row['body'])
        messages.append({
            'id': row['id'],
            'body': body,
            'status': row['status'],
            'perception': body.get('perception', {}),
        })

    # 统计
    total = len(messages)
    filtered_count = sum(msg['status'] == 'filtered' for msg in messages)
    by_category = {}
    needs_reply = []
    high_priority = []
    todos_with_deadline = []
    calendar_events = []

    for msg in messages:
        if msg['status'] == 'filtered':
            continue
        p = msg['perception']
        if not p:
            continue

        # 分类统计
        cat = p.get('category', 'other')
        by_category[cat] = by_category.get(cat, 0) + 1

        # 需要回复
        if p.get('needs_reply'):
            needs_reply.append({
                'message_id': msg['id'],
                'subject': msg['body'].get('subject', ''),
                'sender': msg['body'].get('sender_display', msg['body'].get('sender', '')),
                'summary': p.get('summary', ''),
                'priority': p.get('priority', 'normal'),
            })

        # 高优先级
        if p.get('priority') == 'high':
            high_priority.append({
                'message_id': msg['id'],
                'subject': msg['body'].get('subject', ''),
                'summary': p.get('summary', ''),
                'reasons': p.get('reasons', []),
            })

        # 有截止日期的待办
        for todo in p.get('todos', []):
            if todo.get('deadline'):
                todos_with_deadline.append({
                    'message_id': msg['id'],
                    'action': todo.get('action', ''),
                    'deadline': todo['deadline'],
                    'source_quote': todo.get('source_quote', ''),
                })

        # 日程事件
        for event in p.get('calendar_events', []):
            calendar_events.append({
                'message_id': msg['id'],
                'title': event.get('title', ''),
                'start': event.get('start', ''),
                'location': event.get('location', ''),
            })

    # 按截止日期排序待办
    todos_with_deadline.sort(key=lambda x: x['deadline'])

    # 生成自然语言摘要
    summary_parts = []
    if total == 0:
        summary_parts.append('当天没有新邮件。')
    else:
        summary_parts.append(f'当天共收到 {total} 封邮件。')
        if filtered_count:
            summary_parts.append(f'{filtered_count} 封在本地过滤箱。')
        if needs_reply:
            summary_parts.append(f'{len(needs_reply)} 封需要回复。')
        if high_priority:
            summary_parts.append(f'{len(high_priority)} 封高优先级。')
        if todos_with_deadline:
            nearest = todos_with_deadline[0]
            summary_parts.append(f'最紧急的待办：{nearest["action"]}（{nearest["deadline"][:10]} 截止）。')

    digest = {
        'date': target_date.strftime('%Y-%m-%d'),
        'account_id': account_id,
        'generated_at': now(),
        'summary': ' '.join(summary_parts),
        'stats': {
            'total': total,
            'filtered_count': filtered_count,
            'by_category': by_category,
            'needs_reply_count': len(needs_reply),
            'high_priority_count': len(high_priority),
            'todos_with_deadline_count': len(todos_with_deadline),
            'calendar_events_count': len(calendar_events),
        },
        'needs_reply': needs_reply,
        'high_priority': high_priority,
        'todos_with_deadline': todos_with_deadline,
        'calendar_events': calendar_events,
    }

    return digest


def save_digest(db, digest: dict) -> str:
    """保存摘要到数据库。"""
    initialize(db)
    key = f"digest:{digest['account_id']}:{digest['date']}"
    try:
        existing = require(db, key, 'digest')
        db.update(key, digest, existing['status'])
        return key
    except KeyError:
        db.insert('digest', digest, id=key, scope=digest['account_id'], status='published')
        return key


def get_digest(db, account_id: str, date: str | None = None) -> dict | None:
    """获取指定日期的摘要，没有则返回 None。"""
    if date is None:
        date = _get_shanghai_now().strftime('%Y-%m-%d')
    key = f"digest:{account_id}:{date}"
    try:
        return require(db, key, 'digest')['body']
    except KeyError:
        return None


def get_latest_digest(db, account_id: str) -> dict | None:
    """获取最新的摘要。"""
    initialize(db)
    with db.engine.connect() as c:
        row = c.execute(
            text("""
                SELECT body FROM records
                WHERE kind='digest' AND scope=:account_id AND status='published'
                ORDER BY json_extract(body, '$.date') DESC
                LIMIT 1
            """),
            {'account_id': account_id}
        ).first()
    if row:
        return row[0] if isinstance(row[0], dict) else json.loads(row[0])
    return None


def generate_and_save_digest(db, account_id: str, date: str | None = None) -> dict:
    """生成并保存摘要。"""
    digest = generate_digest(db, account_id, date)
    save_digest(db, digest)
    return digest
