"""
主动感知型邮件 Agent（Mail Perception Agent）。

每封新邮件进来后，自动触发一次感知任务，用大模型对邮件做全面分析：
- 垃圾邮件评分
- 邮件分类（工作/个人/广告/通知/其他）
- 一句话摘要
- 待办事项提取
- 日程事件提取
- 是否需要回复
- 优先级

设计原则：
1. 有界：每封邮件最多 1 次模型调用，有 token 预算
2. 异步：感知是后台任务，不阻塞邮件收取
3. 可学习：用户纠偏写入记忆，下次感知时参考
4. 不替代规则：规则过滤保留，AI 判断作为增强层
"""
import json
import re
import hashlib
from datetime import datetime, timezone
from sqlalchemy import text
from .db import now, uid
from .mail_store import require, initialize
from .mail_models import PerceptionResult, PerceptionFeedback


# ============================================================
# Prompt 构建
# ============================================================

_SYSTEM_PROMPT = """你是一个邮件感知助手。你的任务是对一封邮件进行全面分析，输出结构化 JSON。

分析维度：
1. spam_score: 垃圾邮件概率，0.0（正常）到 1.0（确定垃圾）。广告、营销、诈骗邮件分数高。
2. category: 邮件分类，只能是 work（工作）、personal（个人）、ad（广告/营销）、notification（系统通知）、other（其他）。
3. summary: 一句话摘要，不超过 50 字，概括邮件核心内容。
4. todos: 从邮件中提取的待办事项列表。每个待办包含 action（要做什么）、deadline（截止时间，ISO 8601 带时区，没有则为 null）、source_quote（邮件中的原文引用，不超过 100 字）。只有明确要求行动的才算待办，普通信息不算。
5. calendar_events: 从邮件中提取的日程事件。每个事件包含 title（事件标题）、start（开始时间，ISO 8601 带时区）、end（结束时间，没有则为 null）、location（地点，没有则为空字符串）、source_quote（原文引用）。只有明确的会议/约会/截止日期才算日程。
6. needs_reply: 是否需要回复。true 表示邮件明确要求回复或提问，false 表示纯通知或不需要回复。
7. priority: 优先级，high（紧急/重要）、normal（普通）、low（不重要）。
8. reasons: 可解释理由列表，说明你为什么给出这个分类和优先级。每条不超过30字。比如：["发件人是你的上级", "包含明确截止日期", "要求你采取行动", "纯通知不需要回复"]。至少给出1条理由。
9. confidence: 你对整体判断的置信度，0.0 到 1.0。

严格输出 JSON，不要输出任何其他文字。所有时间必须是 ISO 8601 格式并带时区（如 2026-09-25T15:00:00+08:00）。如果邮件中没有明确时间，deadline/start/end 设为 null。"""


def _build_user_prompt(message_body: dict, memory_text: str = '') -> str:
    """构建用户消息，包含邮件内容和用户偏好记忆。"""
    sender = message_body.get('sender_display', message_body.get('sender', ''))
    subject = message_body.get('subject', '')
    text = message_body.get('text', '')
    # 限制正文长度，避免 token 超限
    if len(text) > 3000:
        text = text[:3000] + '...（已截断）'

    parts = [
        f'发件人: {sender}',
        f'主题: {subject}',
        f'正文:\n{text}',
    ]
    if memory_text:
        parts.append(f'\n用户偏好（请参考这些偏好进行判断）:\n{memory_text}')

    return '\n\n'.join(parts)


def _get_perception_memory(db, account_id: str) -> str:
    """
    获取与感知相关的用户偏好记忆。

    只取 memory_type='preference' 且 status='published' 的记忆，
    限制总长度不超过 1000 字符。
    """
    initialize(db)
    memories = []
    total_len = 0
    with db.engine.connect() as c:
        rows = c.execute(
            text("SELECT body FROM records WHERE kind='memory' AND status='published' "
                 "AND (scope=:a OR scope='global') "
                 "ORDER BY updated_at DESC LIMIT 50"),
            {'a': account_id}
        ).fetchall()
    for row in rows:
        body = json.loads(row[0]) if isinstance(row[0], str) else row[0]
        if body.get('memory_type') != 'preference':
            continue
        content = str(body.get('content', ''))
        if not content:
            continue
        memories.append(f'- {content}')
        total_len += len(content)
        if total_len > 1000:
            break
    return '\n'.join(memories)


# ============================================================
# 核心感知逻辑
# ============================================================

def perceive(db, message_id: str) -> dict:
    """
    对一封邮件执行主动感知。

    流程：
    1. 读取邮件内容
    2. 获取用户偏好记忆
    3. 构建 prompt，调用模型
    4. 解析并验证结果
    5. 存储到邮件记录中
    6. 如果有待办/日程，自动创建待办/日程候选

    返回感知结果 dict。
    """
    initialize(db)
    message = require(db, message_id, 'mail_message')
    body = message['body']
    account_id = message['scope']

    # demo 模式：用规则生成模拟结果
    from .config import settings
    if settings.mode == 'demo':
        return _demo_perceive(db, message_id, body)

    # 获取记忆
    memory_text = _get_perception_memory(db, account_id)

    # 构建消息
    user_prompt = _build_user_prompt(body, memory_text)
    messages = [
        {'role': 'system', 'content': _SYSTEM_PROMPT},
        {'role': 'user', 'content': user_prompt},
    ]

    # 调用模型（使用统一入口，自动计费和预算控制）
    from .planner import model_json, trace_run
    trace_token = trace_run.set(message_id)
    try:
        raw = model_json(db, messages, schema=PerceptionResult.model_json_schema(), purpose='mail_perception')
    finally:
        trace_run.reset(trace_token)

    # 解析并验证结果
    try:
        result = PerceptionResult.model_validate(raw)
    except Exception:
        # 模型输出不合法时，用默认值兜底
        result = PerceptionResult(
            summary='（感知结果解析失败）',
            confidence=0.0,
        )

    result.model_version = settings.model_name or ''
    result.perceived_at = now()

    # 存储结果
    _store_result(db, message_id, result.model_dump())

    # 自动创建待办候选（不自动发布，需要用户确认）
    serial = result.model_dump(mode='json')
    if result.todos:
        _create_todo_candidates(db, account_id, message_id, serial['todos'])

    # 自动创建日程候选（带冲突检测和幂等，不自动发布）
    if result.calendar_events:
        _create_calendar_candidates(db, account_id, message_id, serial['calendar_events'])

    return result.model_dump()


def _demo_perceive(db, message_id: str, body: dict) -> dict:
    """演示模式：用简单规则生成感知结果，不调用模型。"""
    subject = body.get('subject', '')
    text = body.get('text', '')
    sender = body.get('sender', '')

    # 简单规则判断
    spam_score = 0.0
    category = 'other'
    priority = 'normal'
    needs_reply = False

    ad_keywords = ['优惠', '促销', '折扣', '免费领取', '限时', '广告', '推广', '退订']
    if any(k in subject or k in text for k in ad_keywords):
        spam_score = 0.7
        category = 'ad'
        priority = 'low'

    notification_keywords = ['通知', '账单', '验证码', '系统', '自动回复']
    if any(k in subject for k in notification_keywords):
        category = 'notification'
        priority = 'low'

    work_keywords = ['项目', '会议', '报告', '进度', '需求', '评审', '上线', '部署']
    if any(k in subject or k in text for k in work_keywords):
        category = 'work'
        priority = 'normal'

    if ('请回复' in text or '请确认' in text or '?' in subject or '？' in subject) and category != 'ad':
        needs_reply = True
        priority = 'high'

    # 简单摘要
    summary = subject[:50] if subject else text[:50]

    # 简单待办提取（关键词匹配）
    todos = []
    todo_patterns = [
        (r'请在(.{1,20}?)前(.{1,50}?)(?:。|，|$)', None),
        (r'需要(.{1,50}?)(?:。|，|$)', None),
    ]
    for pattern, _ in todo_patterns:
        for m in re.finditer(pattern, text):
            action = m.group(2).strip() if len(m.groups()) > 1 else m.group(1).strip()
            if action and len(action) < 100:
                todos.append({'action': action, 'deadline': None, 'source_quote': m.group(0)[:100]})
                break
        if todos:
            break

    # 生成可解释理由
    reasons = []
    if category == 'work':
        reasons.append('包含工作相关关键词')
    if category == 'ad':
        reasons.append('包含广告/营销关键词')
    if category == 'notification':
        reasons.append('系统通知类邮件')
    if needs_reply:
        reasons.append('邮件要求回复或确认')
    if priority == 'high':
        reasons.append('需要及时处理')
    if todos:
        reasons.append('包含待办事项')
    if not reasons:
        reasons.append('普通邮件')

    result = PerceptionResult(
        spam_score=spam_score,
        category=category,
        summary=summary,
        todos=todos,
        calendar_events=[],
        needs_reply=needs_reply,
        priority=priority,
        reasons=reasons,
        confidence=0.3,  # 演示模式置信度低
        model_version='demo',
        perceived_at=now(),
    )

    _store_result(db, message_id, result.model_dump())
    if result.todos:
        _create_todo_candidates(db, body['account_id'], message_id, result.model_dump(mode='json')['todos'])
    return result.model_dump()


def _store_result(db, message_id: str, result: dict):
    """将感知结果存储到邮件记录的 body.perception 字段中。"""
    message = require(db, message_id, 'mail_message')
    body = message['body']
    body['perception'] = result
    # 白名单及明确待办/日程优先保护；模型自报分数不是校准概率。
    from .filtering import _match_sender, DEFAULT_RULES
    try:
        rules = db.get('mail-filter:' + message['scope'])['body']
    except KeyError:
        rules = DEFAULT_RULES
    protected = (_match_sender(body.get('sender', ''), rules.get('whitelist_senders', []))
                 or result.get('needs_reply') or result.get('todos') or result.get('calendar_events'))
    if (result.get('spam_score', 0) >= 0.9 and result.get('confidence', 0) >= 0.85
            and not protected and message['status'] == 'active'):
        body['filter'] = {**body.get('filter', {}), 'reason': 'AI 高分疑似垃圾',
                          'ai_spam_score': result['spam_score'], 'ai_confidence': result['confidence']}
        db.update(message_id, body, 'filtered')
    else:
        db.update(message_id, body, message['status'])


def _create_todo_candidates(db, account_id: str, message_id: str, todos: list):
    """
    从感知结果中创建待办候选。

    候选状态为 'candidate'，需要用户确认后才变为 'published'。
    这样不会自动创建一堆待办打扰用户。
    """
    for todo in todos:
        signature = json.dumps({'action': todo['action'], 'deadline': str(todo.get('deadline'))},
                               ensure_ascii=False, sort_keys=True)
        key = 'perception-todo:' + hashlib.sha256((message_id + signature).encode()).hexdigest()[:24]
        try:
            require(db, key, 'todo')
            continue  # 已存在
        except KeyError:
            pass
        db.insert('todo', {
            'title': todo['action'],
            'deadline': todo.get('deadline'),
            'source': 'perception',
            'source_message': message_id,
            'source_quote': todo.get('source_quote', ''),
        }, id=key, scope='web:mail:' + account_id, status='candidate')


def _create_calendar_candidates(db, account_id: str, message_id: str, events: list):
    """
    从感知结果中创建日程候选。

    使用 mail_schedule 模块，包含：
    - 冲突检测：检查该时间段是否已有日程
    - 幂等创建：同一邮件同一事件不重复创建

    候选状态为 'candidate'，需要用户确认后才变为 'active'。
    """
    from .mail_schedule import create_calendar_candidate
    for event in events:
        try:
            create_calendar_candidate(db, account_id, message_id, event)
        except Exception:
            # 日程候选创建失败不影响感知主流程
            pass


# ============================================================
# 用户纠偏与记忆学习
# ============================================================

def apply_feedback(db, feedback: PerceptionFeedback) -> dict:
    """
    处理用户对感知结果的纠偏。

    1. 更新邮件记录中的感知结果
    2. 将纠偏写入记忆（作为用户偏好候选）
    3. 返回更新后的感知结果
    """
    initialize(db)
    message = require(db, feedback.message_id, 'mail_message')
    body = message['body']
    perception = body.get('perception', {})
    previous = perception.copy()

    # 应用纠偏
    if feedback.category is not None:
        perception['category'] = feedback.category
    if feedback.spam_score is not None:
        perception['spam_score'] = feedback.spam_score
    if feedback.priority is not None:
        perception['priority'] = feedback.priority
    if feedback.needs_reply is not None:
        perception['needs_reply'] = feedback.needs_reply

    perception['user_feedback'] = {
        'at': now(),
        'note': feedback.note,
    }
    body['perception'] = perception

    status = message['status']
    if feedback.spam_score is not None:
        if feedback.spam_score < 0.5 and status == 'filtered':
            status = 'active'
        elif feedback.spam_score >= 0.8 and status in {'active', 'review'}:
            status = 'filtered'
            body['filter'] = {**body.get('filter', {}), 'reason': '用户手动标记垃圾'}
    db.update(feedback.message_id, body, status)
    if status == 'active' and message['status'] == 'filtered':
        from .mail_store import enqueue
        enqueue(db, 'index', {'message_id': feedback.message_id}, message['scope'],
                priority=30, dedupe='feedback-index:' + feedback.message_id)

    # 将纠偏写入记忆候选
    _learn_from_feedback(db, message['scope'], feedback, previous, body)
    db.audit('user', 'MAIL_PERCEPTION_FEEDBACK', message_id=feedback.message_id,
             old=previous, new=perception, status=status)

    return perception


def _learn_from_feedback(db, account_id: str, feedback: PerceptionFeedback,
                         previous: dict, message_body: dict):
    """
    从用户纠偏中学习偏好，写入记忆候选。

    生成可读的偏好描述，比如：
    - "来自 example.com 的邮件不应被标记为广告"
    - "包含'项目进度'的邮件应标记为高优先级"
    """
    sender = message_body.get('sender', '')
    subject = message_body.get('subject', '')

    preferences = []

    if feedback.category is not None and previous.get('category') != feedback.category:
        preferences.append(
            f"主题包含'{subject[:30]}'的邮件应分类为{feedback.category}"
        )

    if feedback.spam_score is not None:
        if feedback.spam_score < 0.3 and previous.get('spam_score', 0) > 0.5:
            preferences.append(f"来自 {sender} 的邮件不是垃圾邮件")
        elif feedback.spam_score > 0.7 and previous.get('spam_score', 0) < 0.3:
            preferences.append(f"来自 {sender} 的邮件是垃圾邮件")

    if feedback.priority is not None and previous.get('priority') != feedback.priority:
        preferences.append(
            f"主题包含'{subject[:30]}'的邮件优先级应为{feedback.priority}"
        )

    if feedback.note:
        preferences.append(feedback.note)

    # 写入记忆候选（需要用户确认后才生效）
    for pref in preferences[:3]:  # 最多生成 3 条
        key = 'perception-memory:' + hashlib.sha256((account_id + ':' + feedback.message_id + ':' + pref).encode()).hexdigest()[:24]
        try:
            require(db, key, 'memory')
            continue
        except KeyError:
            pass
        db.insert('memory', {
            'content': pref,
            'memory_type': 'preference',
            'source': 'perception_feedback',
            'source_message': feedback.message_id,
        }, id=key, scope=account_id, status='candidate')


# ============================================================
# 查询接口
# ============================================================

def get_perception(db, message_id: str) -> dict | None:
    """获取一封邮件的感知结果，没有则返回 None。"""
    try:
        message = require(db, message_id, 'mail_message')
        return message['body'].get('perception')
    except KeyError:
        return None


def list_pending_todos(db, account_id: str) -> list:
    """列出感知生成的待办候选（等待用户确认）。"""
    initialize(db)
    with db.engine.connect() as c:
        rows = c.execute(
            text("SELECT id, body, created_at FROM records "
                 "WHERE kind='todo' AND status='candidate' AND scope IN (:a,:workspace) "
                 "AND json_extract(body,'$.source')='perception' "
                 "ORDER BY created_at DESC LIMIT 50"),
            {'a': account_id, 'workspace': 'web:mail:' + account_id}
        ).mappings().all()
    return [{**dict(r), 'body': json.loads(r['body']) if isinstance(r['body'], str) else r['body']}
            for r in rows]
