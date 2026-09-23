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
