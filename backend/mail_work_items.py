"""Deterministic promotion of model suggestions into reversible local work."""
import re
import json
from datetime import datetime, timezone, timedelta
from sqlalchemy import text

from .db import now
from .mail_store import require

POLICY_VERSION = 'mail-work-v1'
_ACTION = re.compile(r'请|需要|务必|提交|发送|完成|整理|准备|安排|确认|please|submit|send|finish|prepare|confirm', re.I)
_SENSITIVE = re.compile(r'密码|验证码|授权码|转账|付款|支付|点击链接|登录账号|删除记录|绕过审批|忽略.{0,8}审批|password|one.time.code|pay\b|transfer\b', re.I)
_TIME = re.compile(r'\d{1,2}[:：]\d{2}|\d{1,2}\s*[点时]|周[一二三四五六日天]|星期[一二三四五六日天]|明天|后天|下周|tomorrow|next\s+(mon|tue|wed|thu|fri|sat|sun)', re.I)
_SHANGHAI = timezone(timedelta(hours=8))


def _quoted(message, quote):
    compact = lambda value: re.sub(r'\s+', '', value or '').lower()
    quoted = compact(quote)
    return len(quoted) >= 6 and quoted in compact(message.get('raw_text') or message.get('text'))


def _date_matches(quote, value, message):
    local = value.astimezone(_SHANGHAI)
    absolute = re.search(r'(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})', quote)
    if absolute:
        return (local.year, local.month, local.day) == tuple(map(int, absolute.groups()))
    try:
        received = datetime.fromisoformat(message['received_at']).astimezone(_SHANGHAI)
    except (KeyError, TypeError, ValueError):
        return False
    if '后天' in quote:
        return local.date() == (received + timedelta(days=2)).date()
    if '明天' in quote or 'tomorrow' in quote.lower():
        return local.date() == (received + timedelta(days=1)).date()
    weekday = re.search(r'(?:下周|周|星期)([一二三四五六日天])', quote)
    if weekday:
        target = {'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6}[weekday[1]]
        if local.weekday() != target:
            return False
        if '下周' in quote:
            next_monday = received.date() + timedelta(days=7-received.weekday())
            return next_monday <= local.date() < next_monday + timedelta(days=7)
        return 0 <= (local.date() - received.date()).days <= 7
    return False


def _start_time_matches(quote, value):
    match = re.search(r'(?<!\d)(\d{1,2})[:：](\d{2})', quote)
    if match:
        return (value.astimezone(_SHANGHAI).hour, value.astimezone(_SHANGHAI).minute) == tuple(map(int, match.groups()))
    match = re.search(r'(?<!\d)(\d{1,2})\s*[点时]', quote)
    return bool(match and value.astimezone(_SHANGHAI).hour == int(match[1]))


def _end_time_matches(quote, value):
    times = re.findall(r'(?<!\d)(\d{1,2})[:：](\d{2})', quote)
    if len(times) < 2:
        return False
    return (value.astimezone(_SHANGHAI).hour, value.astimezone(_SHANGHAI).minute) == tuple(map(int, times[1]))


def todo_decision(result, message, item):
    """Model proposes; this gate decides whether a local todo is safe to auto-save."""
    if result.get('confidence', 0) < 0.85:
        return False, '模型置信度不足'
    if result.get('spam_score', 1) > 0.2 or result.get('category') not in {'work', 'personal'}:
        return False, '邮件分类或垃圾评分不适合自动建待办'
    quote = item.get('source_quote', '')
    if not _quoted(message, quote) or not _ACTION.search(quote):
        return False, '缺少可核对的明确行动原文'
    if _SENSITIVE.search(quote) or _SENSITIVE.search(item.get('action', '')):
        return False, '涉及账号、安全或资金操作'
    deadline = item.get('deadline')
    if deadline:
        try:
            due = datetime.fromisoformat(str(deadline))
            if not due.tzinfo or due <= datetime.now(timezone.utc):
                return False, '截止时间已过或不明确'
            if not _date_matches(quote, due, message):
                return False, '截止日期无法从原文核对'
        except ValueError:
            return False, '截止时间格式不明确'
    return True, '明确行动、原文可核对，且只写入可撤销的本地待办'


def calendar_decision(result, message, event):
    if result.get('confidence', 0) < 0.9 or result.get('spam_score', 1) > 0.2:
        return False, '置信度或垃圾评分不满足自动日程条件'
    if result.get('category') not in {'work', 'personal'}:
        return False, '只自动保存工作或个人日程'
    quote = event.get('source_quote', '')
    if not _quoted(message, quote) or not _TIME.search(quote):
        return False, '原文缺少可核对的时间依据'
    try:
        start = datetime.fromisoformat(str(event['start']))
        end = datetime.fromisoformat(str(event['end']))
        if not start.tzinfo or not end.tzinfo or start <= datetime.now(timezone.utc) or not start < end <= start + timedelta(hours=8):
            return False, '日程起止时间不完整或异常'
        if not _date_matches(quote, start, message) or not _start_time_matches(quote, start) or not _end_time_matches(quote, end):
            return False, '模型日期或时间与原文不一致'
    except (KeyError, TypeError, ValueError):
        return False, '日程起止时间不完整'
    return True, '时间和原文明确；仅保存本地、无邀请或外部同步'


def ensure_reply_followup(db, account_id, message_id, result):
    if not result.get('needs_reply') or result.get('spam_score', 1) >= 0.5:
        return None
    message = require(db, message_id, 'mail_message', [account_id])
    if message['status'] in {'filtered', 'trashed', 'deleted'}:
        return None
    ident = 'mail-followup:' + message_id
    try:
        return require(db, ident, 'mail_followup', ['web:mail:' + account_id])
    except KeyError:
        pass
    db.insert('mail_followup', {
        'title': '回复：' + (message['body'].get('subject') or '无主题'),
        'thread_id': message['body'].get('thread_id'), 'source_message': message_id,
        'source': 'perception', 'direction': 'reply_needed',
        'reason': '邮件要求回复或确认', 'created_at': now(),
    }, id=ident, scope='web:mail:' + account_id, status='active')
    return db.get(ident)


def mark_replied(db, message_id, draft_id):
    if not message_id:
        return
    try:
        row = require(db, 'mail-followup:' + message_id, 'mail_followup')
    except KeyError:
        return
    if row['status'] == 'active':
        db.update(row['id'], {**row['body'], 'draft_id': draft_id, 'replied_at': now()}, 'waiting')


def sync_todo_reminder(db, todo_id):
    """Keep the derived reminder aligned with a local todo's lifecycle."""
    todo=require(db,todo_id,'todo');reminder_id='todo-reminder:'+todo_id
    try:reminder=require(db,reminder_id,'reminder')
    except KeyError:reminder=None
    deadline=todo['body'].get('deadline')
    if todo['status']!='active' or not deadline:
        if reminder and reminder['status']=='active':
            db.update(reminder_id,{**reminder['body'],'cancelled_by_todo':todo_id},'cancelled')
        return None
    try:due=datetime.fromisoformat(str(deadline))-timedelta(hours=1)
    except (TypeError,ValueError):return None
    body={'title':todo['body'].get('title','待办即将截止'),'due_at':max(due,datetime.now(timezone.utc)).isoformat(),
          'source_todo':todo_id,'source_message':todo['body'].get('source_message')}
    if reminder:db.update(reminder_id,body,'active')
    else:db.insert('reminder',body,id=reminder_id,scope=todo['scope'])
    return reminder_id


def resolve_thread_followups(db,account_id,thread_id,message_id,sender):
    """An inbound reply closes follow-ups that were waiting on the same thread."""
    account=require(db,account_id,'mail_account')['body']
    if not thread_id or sender.lower() in {account.get('address','').lower(),account.get('username','').lower()}:
        return []
    closed=[]
    with db.engine.connect() as c:
        found=c.execute(text("""SELECT id,body FROM records WHERE kind='mail_followup'
            AND scope=:scope AND status='waiting' AND json_extract(body,'$.thread_id')=:thread"""),
            {'scope':'web:mail:'+account_id,'thread':thread_id}).mappings().all()
    for row in found:
        body=json.loads(row['body']);body.update({'resolved_by_message':message_id,'resolved_at':now()})
        db.update(row['id'],body,'resolved');closed.append(row['id'])
    return closed
