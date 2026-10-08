"""HTTP adapter for per-account settings, memory and migration review."""

from email.message import EmailMessage
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.api.dependencies import get_store
from backend.app.modules.mail.repository import save_account
from backend.app.modules.mail.schemas import MailAccount
from backend.app.modules.mail.services import settings as settings_service
from backend.app.persistence.store import now


router = APIRouter()
database = get_store


class FilterRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    classification_mode: Literal['model','rules'] = 'model'
    whitelist_senders: list[str] = Field(default_factory=list, max_length=200)
    blacklist_senders: list[str] = Field(default_factory=list, max_length=200)
    blacklist_domains: list[str] = Field(default_factory=list, max_length=200)
    subject_keywords: list[str] = Field(default_factory=list, max_length=200)
    content_keywords: list[str] = Field(default_factory=list, max_length=200)
    auto_filter_categories: list[str] = Field(default_factory=lambda: ["ad", "subscription"])

    @field_validator(
        "whitelist_senders",
        "blacklist_senders",
        "blacklist_domains",
        "subject_keywords",
        "content_keywords",
    )
    @classmethod
    def bounded_rules(cls, values):
        if any(not value.strip() or len(value) > 256 for value in values):
            raise ValueError("过滤条件须为 1—256 字符")
        return list(dict.fromkeys(value.strip() for value in values))


@router.get("/mail/accounts/{ident}/filters")
def get_filters(ident: str, db=Depends(database)):
    return settings_service.get_filter_rules(db, ident, FilterRules().model_dump())


@router.post("/mail/accounts/{ident}/filters")
def filters(ident: str, body: FilterRules, db=Depends(database)):
    return settings_service.update_filter_rules(db, ident, body.model_dump())


class MemoryCandidate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    account_id: str
    memory_type: str = "preference"


@router.post("/mail/memory")
def candidate_memory(body: MemoryCandidate, db=Depends(database)):
    return settings_service.create_memory_candidate(db, body)


@router.get("/mail/migration")
def migration_status(db=Depends(database)):
    return settings_service.migration_status(db)


@router.post("/mail/legacy/{ident}/resume")
def resume_legacy(ident: str, db=Depends(database)):
    return settings_service.resume_legacy(db, ident)


@router.post("/mail/demo")
def demo(db=Depends(database)):
    from backend.app.core.config import settings
    from backend.app.modules.mail.ingestion import store_message

    if settings.mode != "demo":
        raise ValueError("演示资料仅能在 demo 模式导入")
    account = save_account(
        db,
        MailAccount(
            name="演示邮箱",
            address="demo@example.com",
            provider="custom",
            imap_host="example.com",
            smtp_host="example.com",
        ),
    )
    message = EmailMessage()
    message["From"] = "teacher@example.com"
    message["To"] = "demo@example.com"
    message["Subject"] = "实验报告修改"
    message["Message-ID"] = "<demo-experiment@example.com>"
    message.set_content("请在周五前补充实验对比，并回复最终报告。")
    message_id = store_message(db, account["id"], "demo", 1, message.as_bytes(), now())
    return {"account_id": account["id"], "message_id": message_id}
