from typing import Any, Literal, TypedDict
from pydantic import BaseModel, Field, model_validator

class NormalizedMessage(BaseModel):
    message_id: str = Field(min_length=1, max_length=300)
    source: Literal['web', 'email', 'demo'] = 'web'
    sender_id: str = 'owner'
    conversation_id: str = 'inbox'
    text: str = Field(min_length=1, max_length=30000)
    timestamp: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class PlannedAction(BaseModel):
    id: str = ''
    tool: Literal['create_todo','update_todo','create_calendar','update_calendar','create_reminder','create_memory_candidate','draft_email','summarize','send_email','delete_item']
    args: dict[str, Any]
    depends_on: list[str] = Field(default_factory=list)
    scenario: str = 'general'
    confidence: float = Field(default=0.8, ge=0, le=1)
    clarification: str | None = None

class ActionPlan(BaseModel):
    summary: str
    actions: list[PlannedAction] = Field(max_length=20)

    @model_validator(mode='after')
    def check_dependencies(self):
        seen = set()
        for i, action in enumerate(self.actions):
            action.id = action.id or str(i)
            if action.id in seen or any(d not in seen for d in action.depends_on):
                raise ValueError('动作依赖必须指向前面的动作且 ID 唯一')
            seen.add(action.id)
        return self

class Approval(BaseModel):
    decision: Literal['approve','reject','edit']
    version: int = 1
    args: dict[str, Any] | None = None

class ToolResult(BaseModel):
    status: str
    data: dict[str, Any] = Field(default_factory=dict)

class ZhiXingState(TypedDict, total=False):
    run_id: str
    message: dict
    actions: list[dict]
    index: int
    outcomes: dict
    decision: str
    versions: dict
