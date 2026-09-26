"""Local durable execution traces. No external telemetry or reasoning-token capture."""
from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field
from backend.app.observability.billing import summarize

class TraceSummary(BaseModel):
    trace_id: str
    status: str
    event_count: int
    model_call_count: int
    duration_ms: float
    model_latency_ms: float | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost: float | None
    billing: dict[str, Any] = Field(default_factory=dict)

class RunDetail(BaseModel):
    run: dict[str, Any]
    audit: list[dict[str, Any]]
    model_calls: list[dict[str, Any]]
    trace: TraceSummary

def detail(db, run_id):
    run=db.get(run_id)
    if run['kind']!='run':
        raise ValueError('不是运行记录')
    audit=db.for_run('audit',run_id)
    calls=db.for_run('model_call',run_id)
    # Older failed records lost run_id but still have linked MODEL_FAILED audit events.
    linked={r['body'].get('call_id') for r in audit if r['body'].get('call_id')}
    known={r['id'] for r in calls}
    for ident in linked-known:
        try:
            row=db.get(ident)
            if row['kind']=='model_call':
                calls.append(row)
        except KeyError:
            pass
    calls.sort(key=lambda r:(r['created_at'],r['id']))
    def total(key, container=None):
        values=[(r['body'].get(container) or {}).get(key) if container else r['body'].get(key) for r in calls]
        return sum(values) if all(isinstance(v,(int,float)) for v in values) else None
    elapsed=run['body'].get('duration_ms')
    if elapsed is None:
        end=datetime.now(timezone.utc) if run['status'] in {'running','queued','waiting_approval'} else datetime.fromisoformat(run['updated_at'])
        elapsed=max(0,(end-datetime.fromisoformat(run['created_at'])).total_seconds()*1000)
    summary=TraceSummary(trace_id=run_id,status=run['status'],event_count=len(audit),model_call_count=len(calls),duration_ms=elapsed,model_latency_ms=total('latency_ms'),input_tokens=total('prompt_tokens','usage'),output_tokens=total('completion_tokens','usage'),total_tokens=total('total_tokens','usage'),cost=total('cost') if calls else None)
    # Keep the prior newest-first audit response contract. The UI renders chronological order.
    summary.billing=summarize(calls)
    # Compatibility cost never adds unlike or unidentified currencies.
    currencies={(r['body'].get('billing') or {}).get('currency') for r in calls}
    if None in currencies or len(currencies)!=1:
        summary.cost=None
    return RunDetail(run=run,audit=list(reversed(audit)),model_calls=calls,trace=summary)
