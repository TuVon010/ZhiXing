"""Public contracts for the mail workspace. Credentials are write-only."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import datetime
import re


def address(value):
    if not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+', value):
        raise ValueError('邮箱地址格式无效')
    return value.lower()


class MailAccount(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1,max_length=100)
    address: str
    provider: Literal['qq','163','custom'] = 'qq'
    imap_host: str = ''
    imap_port: int = Field(default=993,ge=1,le=65535)
    imap_tls: Literal['ssl','starttls'] = 'ssl'
    smtp_host: str = ''
    smtp_port: int = Field(default=465,ge=1,le=65535)
    smtp_tls: Literal['ssl','starttls'] = 'ssl'
    username: str = ''
    enabled: bool = False
    auto_analyze: bool = False
    scan_limit: int = Field(default=20,ge=1,le=200)
    hourly_analysis_limit: int = Field(default=20,ge=1,le=200)
    password: str | None = Field(default=None,max_length=1000,json_schema_extra={'writeOnly':True})

    @field_validator('address')
    @classmethod
    def valid_address(cls,v):
        return address(v)

    @model_validator(mode='after')
    def hosts(self):
        if self.provider != 'custom':
            self.imap_host='imap.'+('qq.com' if self.provider=='qq' else '163.com')
            self.smtp_host='smtp.'+('qq.com' if self.provider=='qq' else '163.com')
        for host in [self.imap_host,self.smtp_host]:
            if not re.fullmatch(r'[A-Za-z0-9.-]+',host):
                raise ValueError('请输入服务器主机名，不含 URL 路径')
        self.username=self.username or self.address
        return self


class ImportRequest(BaseModel):
    account_id: str
    start: datetime
    end: datetime
    limit: int = Field(default=100,ge=1,le=10000)
    analyze_after_import: bool = False
    @model_validator(mode='after')
    def interval(self):
        if not self.start.tzinfo or not self.end.tzinfo or self.end<=self.start:
            raise ValueError('时间范围必须带时区且结束晚于开始')
        return self


class FollowupInput(BaseModel):
    account_id:str
    title:str=Field(min_length=1,max_length=500)
    deadline:datetime|None=None
    @field_validator('deadline')
    @classmethod
    def deadline_timezone(cls,value):
        if value and not value.tzinfo:raise ValueError('截止时间必须带时区')
        return value


class SearchRequest(BaseModel):
    account_ids: list[str] = Field(min_length=1,max_length=20)
    query: str = Field(min_length=1,max_length=2000)
    start: datetime | None = None
    end: datetime | None = None
    sender: str | None = None
    mode: Literal['hybrid','keyword','vector','fusion'] = 'hybrid'
    @field_validator('start','end')
    @classmethod
    def timezone_required(cls,v):
        if v and not v.tzinfo:
            raise ValueError('检索时间必须带时区')
        return v


class DraftInput(BaseModel):
    account_id: str
    message_id: str | None = None
    mode: Literal['new','reply','reply_all'] = 'new'
    to: list[str] = Field(default_factory=list,max_length=50)
    cc: list[str] = Field(default_factory=list,max_length=50)
    subject: str = Field(default='',max_length=500)
    content: str = Field(default='',max_length=50000)
    version: int = Field(default=0,ge=0)
    @field_validator('to','cc')
    @classmethod
    def addresses(cls, values):
        return list(dict.fromkeys(address(v) for v in values))
    @field_validator('subject')
    @classmethod
    def subject_header(cls,v):
        if '\r' in v or '\n' in v:
            raise ValueError('主题不能包含换行')
        return v


class SessionInput(BaseModel):
    account_ids: list[str] = Field(min_length=1,max_length=20)
    thread_id: str | None = None


class TurnInput(BaseModel):
    text: str = Field(min_length=1,max_length=10000)


class EvidenceRef(BaseModel):
    id: str
    account_id: str
    message_id: str
    thread_id: str
    text: str
    location: str


class MailMessage(BaseModel):
    account_id: str
    thread_id: str
    subject: str
    text: str
    received_at: str
    declared_at: str | None = None
    fetched_at: str


class MailThread(BaseModel):
    account_id: str
    subject: str


class ImportJob(ImportRequest):
    imported: int = 0
    scanned: int = 0


class RetrievalResult(BaseModel):
    mode: str
    degraded: bool
    evidence: list[EvidenceRef]


class MemorySnapshot(BaseModel):
    account_ids: list[str]
    user_md: str
    memory_md: str
    version_ids: list[str]


# ============================================================
# 主动感知 Agent（Mail Perception Agent）数据模型
# ============================================================

class PerceivedTodo(BaseModel):
    """从邮件中提取的待办事项。"""
    action: str = Field(min_length=1, max_length=500)
    deadline: datetime | None = None
    source_quote: str = Field(default='', max_length=300)

    @field_validator('deadline')
    @classmethod
    def deadline_tz(cls, v):
        if v and not v.tzinfo:
            raise ValueError('截止时间必须带时区')
        return v


class PerceivedEvent(BaseModel):
    """从邮件中提取的日程事件。"""
    title: str = Field(min_length=1, max_length=300)
    start: datetime | None = None
    end: datetime | None = None
    location: str = Field(default='', max_length=200)
    source_quote: str = Field(default='', max_length=300)

    @field_validator('start', 'end')
    @classmethod
    def event_tz(cls, v):
        if v and not v.tzinfo:
            raise ValueError('日程时间必须带时区')
        return v


class PerceptionResult(BaseModel):
    """
    主动感知 Agent 对一封邮件的分析结果。

    一次模型调用同时输出多个维度判断，避免多次调用。
    所有字段都有默认值，模型输出不完整时也能安全使用。
    """
    model_config = ConfigDict(extra='forbid')

    # 垃圾邮件评分：0=正常，1=确定垃圾
    spam_score: float = Field(default=0.0, ge=0, le=1)

    # 邮件分类
    category: Literal['work', 'personal', 'ad', 'notification', 'other'] = 'other'

    # 一句话摘要（不超过 100 字）
    summary: str = Field(default='', max_length=200)

    # 提取的待办事项
    todos: list[PerceivedTodo] = Field(default_factory=list)

    # 提取的日程事件
    calendar_events: list[PerceivedEvent] = Field(default_factory=list)

    # 是否需要回复
    needs_reply: bool = False

    # 优先级
    priority: Literal['high', 'normal', 'low'] = 'normal'

    # 可解释理由：为什么给出这个分类/优先级/判断
    # 比如 ["发件人是你的经理", "包含明确截止日期", "要求你采取行动"]
    reasons: list[str] = Field(default_factory=list)

    # 模型置信度（整体判断的可信度）
    confidence: float = Field(default=0.5, ge=0, le=1)

    # 模型版本（用于追溯）
    model_version: str = ''

    # 感知时间
    perceived_at: str = ''


class PerceptionFeedback(BaseModel):
    """
    用户对感知结果的纠偏反馈。

    用户可以纠正分类、优先级、垃圾评分等。
    反馈会写入记忆层，下次感知时参考。
    """
    model_config = ConfigDict(extra='forbid')

    message_id: str | None = None
    # 用户纠正后的分类（None 表示不纠正）
    category: Literal['work', 'personal', 'ad', 'notification', 'other'] | None = None
    # 用户纠正后的垃圾评分
    spam_score: float | None = Field(default=None, ge=0, le=1)
    # 用户纠正后的优先级
    priority: Literal['high', 'normal', 'low'] | None = None
    # 用户是否认为需要回复
    needs_reply: bool | None = None
    # 用户备注（可选，用于学习偏好）
    note: str = Field(default='', max_length=500)
