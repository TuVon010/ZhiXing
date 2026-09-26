"""Model pricing profiles and aggregate billing endpoints."""
import json
from fastapi import APIRouter, HTTPException, Request, Response, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from backend.app.core.config import settings
from backend.app.persistence.store import store, now, uid
from backend.app.agent.schemas import NormalizedMessage, Approval, ActionPlan, PlannedAction
from backend.app.agent.graph import ingest, approve
from backend.app.agent import evolution
from backend.app.observability.tracing import RunDetail, detail as trace_detail
from backend.app.observability import billing

router = APIRouter()
@router.get('/api/pricing',response_model=list[billing.PricingProfile])
def pricing():
    return billing.profiles(store)

@router.post('/api/pricing/{profile_id}',response_model=billing.PricingProfile)
def update_pricing(profile_id:str,body:billing.PricingUpdate):
    return billing.save_profile(store,profile_id,body)

@router.get('/api/billing-summary')
def billing_summary():
    with store.engine.connect() as conn:
        rows=conn.execute(text("SELECT body,status FROM records WHERE kind='model_call'")).mappings()
        return billing.summarize({'body':json.loads(row['body']),'status':row['status']} for row in rows)

