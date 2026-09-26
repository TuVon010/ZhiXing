"""Mail perception, candidate review and digest endpoints."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator, ConfigDict
from sqlalchemy import text
import json
from datetime import datetime, timezone, timedelta
from backend.app.modules.mail.schemas import MailAccount, ImportRequest, SearchRequest, DraftInput, SessionInput, TurnInput, PerceptionFeedback
from backend.app.modules.mail.perception_queue import BatchRequest, queue_batch, summary as perception_queue_summary
from backend.app.modules.mail.repository import initialize, rows, require, public_account, save_account, enqueue, job, validate_accounts
from backend.app.persistence.store import now

router = APIRouter()


def database():
    """Provide the shared local store while keeping route handlers injectable."""
    from backend.app.persistence.store import store
    initialize(store)
    return store
@router.get('/mail/messages/{ident}/perception')
def get_perception(ident:str,db=Depends(database)):
    """获取一封邮件的感知结果。"""
    from backend.app.modules.mail.perception import get_perception as gp
    result = gp(db, ident)
    if result is None:
        return {'perception': None, 'status': 'pending'}
    return {'perception': result, 'status': 'ready'}


@router.post('/mail/messages/{ident}/perception')
def request_perception(ident:str,db=Depends(database)):
    """用户显式分析或重试；仍受全局模型调用预算限制。"""
    mail=require(db,ident,'mail_message')
    require(db,mail['scope'],'mail_account')
    with db.engine.connect() as c:
        pending=c.execute(text("SELECT id FROM mail_jobs WHERE kind='perception' AND account_id=:account AND json_extract(payload,'$.message_id')=:message AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1"),
                          {'account':mail['scope'],'message':ident}).first()
    if pending:return {'job_id':pending[0]}
    return {'job_id':enqueue(db,'perception',{'message_id':ident,'manual':True},mail['scope'],priority=5)}


@router.post('/mail/messages/{ident}/perception/feedback')
def perception_feedback(ident:str,body:PerceptionFeedback,db=Depends(database)):
    """用户对感知结果的纠偏反馈，会写入记忆供下次感知参考。"""
    from backend.app.modules.mail.perception import apply_feedback
    body.message_id = ident
    return apply_feedback(db, body)


@router.get('/mail/perception/todos')
def list_perception_todos(account_id:str='',db=Depends(database)):
    """列出感知生成的待办候选（等待用户确认）。"""
    from backend.app.modules.mail.perception import list_pending_todos
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.extend(list_pending_todos(db, a['id']))
        return result
    require(db,account_id,'mail_account')
    return list_pending_todos(db, account_id)


@router.post('/mail/perception/todos/{ident}/confirm')
def confirm_perception_todo(ident:str,db=Depends(database)):
    """确认感知生成的待办，将其从 candidate 变为 active。"""
    row = require(db,ident,'todo')
    if row['status'] == 'active' and row['body'].get('source') == 'perception':
        return row
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'active')
    if not row['scope'].startswith('web:mail:'):
        # 迁移旧候选，保证后续完成动作使用现有工作区作用域。
        require(db,row['scope'],'mail_account')
        with db.engine.begin() as c:
            c.execute(text('UPDATE records SET scope=:scope WHERE id=:id'),
                      {'scope': 'web:mail:' + row['scope'], 'id': ident})
    from backend.app.modules.mail.work_items import sync_todo_reminder
    sync_todo_reminder(db,ident)
    return db.get(ident)


@router.post('/mail/perception/todos/{ident}/dismiss')
def dismiss_perception_todo(ident:str,db=Depends(database)):
    """忽略感知生成的待办。"""
    row = require(db,ident,'todo')
    if row['status'] != 'candidate':
        raise ValueError('该待办不是候选状态')
    db.update(ident, row['body'], 'dismissed')
    return db.get(ident)


@router.get('/mail/perception/calendars')
def list_perception_calendars(account_id:str='',db=Depends(database)):
    """列出感知生成的日程候选（等待用户确认），包含冲突检测信息。"""
    from backend.app.modules.mail.calendar import list_calendar_candidates
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.extend(list_calendar_candidates(db, a['id']))
        return result
    return list_calendar_candidates(db, account_id)


@router.post('/mail/perception/calendars/{ident}/confirm')
def confirm_perception_calendar(ident:str,db=Depends(database)):
    """确认感知生成的日程候选，将其从 candidate 变为 active。确认时再次检测冲突。"""
    from backend.app.modules.mail.calendar import confirm_calendar
    row = require(db,ident,'calendar')
    account_id = row['scope'].removeprefix('web:mail:')
    require(db,account_id,'mail_account')
    return confirm_calendar(db, ident, account_id)


@router.post('/mail/perception/calendars/{ident}/dismiss')
def dismiss_perception_calendar(ident:str,db=Depends(database)):
    """忽略感知生成的日程候选。"""
    row = require(db,ident,'calendar')
    if row['status'] != 'candidate':
        raise ValueError('该日程不是候选状态')
    db.update(ident, row['body'], 'dismissed')
    return db.get(ident)


@router.get('/mail/digest')
def get_digest(account_id:str='', date:str='', db=Depends(database)):
    """获取邮件动态摘要。date 格式 YYYY-MM-DD，默认今天。"""
    from backend.app.modules.mail.digest import get_digest, get_latest_digest
    if account_id:
        return get_digest(db, account_id, date or None) or {'message': '该日期暂无摘要'}
    # 没有指定账号时，返回所有账号的最新摘要
    accounts = rows(db,'mail_account',limit=100)
    result = []
    for a in accounts:
        d = get_latest_digest(db, a['id'])
        if d:
            result.append(d)
    return result


@router.post('/mail/digest/generate')
def generate_digest_now(account_id:str='', date:str='', db=Depends(database)):
    """立即生成指定日期的邮件摘要（手动触发）。"""
    from backend.app.modules.mail.digest import generate_and_save_digest
    if not account_id:
        accounts = rows(db,'mail_account',limit=100)
        result = []
        for a in accounts:
            result.append(generate_and_save_digest(db, a['id'], date or None))
        return result
    return generate_and_save_digest(db, account_id, date or None)

