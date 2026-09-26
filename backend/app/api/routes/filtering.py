"""Filter rules, review box and classifier evaluation endpoints."""
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
class FilterRulesInput(BaseModel):
    whitelist_senders: list[str] = Field(default_factory=list)
    blacklist_senders: list[str] = Field(default_factory=list)
    blacklist_domains: list[str] = Field(default_factory=list)
    subject_keywords: list[str] = Field(default_factory=list)
    content_keywords: list[str] = Field(default_factory=list)
    auto_filter_categories: list[str] = Field(default_factory=lambda: ['ad','subscription'])
    enabled: bool = True

@router.get('/api/filter/rules')
def get_filter_rules():
    from backend.app.modules.mail import filtering
    return filtering.get_rules(store)

@router.post('/api/filter/rules')
def save_filter_rules(body: FilterRulesInput):
    from backend.app.modules.mail import filtering
    rules = filtering.save_rules(store, body.model_dump())
    return {'saved': True, 'version': rules['version']}

@router.get('/api/filter/box')
def list_filter_box(status: str = 'filtered', limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    from backend.app.modules.mail import filtering
    items = filtering.list_filter_box(store, status=status, limit=limit, offset=offset)
    return {'items': [{'id':r['id'],'status':r['status'],'reason':r['body'].get('reason'),'category':r['body'].get('category'),'category_label':r['body'].get('category_label'),'sender':r['body']['message'].get('sender_id'),'text_preview':r['body']['message'].get('text','')[:200],'filtered_at':r['body'].get('filtered_at'),'evidence':r['body'].get('evidence',[])} for r in items], 'limit': limit, 'offset': offset}

@router.post('/api/filter/box/{filter_id}/release')
def release_filter_item(filter_id: str):
    from backend.app.modules.mail import filtering
    result = filtering.release_from_filter_box(store, filter_id, ingest_func=ingest)
    return result

@router.get('/api/filter/stats')
def filter_stats():
    from backend.app.modules.mail import filtering
    return filtering.get_filter_stats(store)

# ========== 模型辅助分类与标注集 ==========

class ClassifyInput(BaseModel):
    text: str = Field(min_length=1, max_length=30000)
    sender_id: str = ''
    source: str = 'manual'

@router.post('/api/filter/classify')
def classify_message_api(body: ClassifyInput):
    from backend.app.modules.mail import filtering
    message = {'text': body.text, 'sender_id': body.sender_id, 'source': body.source}
    rule_cat, rule_conf, rule_ev = filtering.classify_message(message)
    model_cat, model_conf, model_reason, raw = filtering.classify_with_model(message)
    return {
        'rule': {'category': rule_cat, 'label': filtering.CATEGORY_LABELS.get(rule_cat), 'confidence': rule_conf, 'evidence': rule_ev},
        'model': {'category': model_cat, 'label': filtering.CATEGORY_LABELS.get(model_cat) if model_cat else None, 'confidence': model_conf, 'reason': model_reason},
        'agreement': (rule_cat == model_cat) if model_cat else None,
    }

class LabeledSampleInput(BaseModel):
    text: str = Field(min_length=1, max_length=30000)
    sender_id: str = ''
    expected_category: str
    source: str = 'manual'
    notes: str = ''

@router.post('/api/filter/labels')
def add_labeled_sample(body: LabeledSampleInput):
    from backend.app.modules.mail import filtering
    if body.expected_category not in filtering.CATEGORY_LABELS:
        raise ValueError(f'无效分类，可选: {list(filtering.CATEGORY_LABELS.keys())}')
    message = {'text': body.text, 'sender_id': body.sender_id, 'source': 'manual'}
    sample_id = filtering.add_labeled_sample(store, message, body.expected_category, source=body.source, notes=body.notes)
    return {'id': sample_id}

@router.get('/api/filter/labels')
def list_labeled_samples(category: str | None = None, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    from backend.app.modules.mail import filtering
    samples = filtering.list_labeled_samples(store, category=category, limit=limit, offset=offset)
    return {'items': [{'id':s['id'],'expected_category':s['body'].get('expected_category'),'expected_label':s['body'].get('expected_label'),'text_preview':s['body'].get('text_preview','')[:200],'source':s['body'].get('source'),'created_at':s['body'].get('created_at')} for s in samples], 'limit': limit, 'offset': offset}

@router.get('/api/filter/evaluate')
def evaluate_classifier():
    from backend.app.modules.mail import filtering
    return filtering.evaluate_classifier(store)

@router.post('/api/filter/shadow')
def shadow_classify_api(body: ClassifyInput):
    from backend.app.modules.mail import filtering
    message = {'text': body.text, 'sender_id': body.sender_id, 'source': body.source, 'message_id': uid()}
    return filtering.shadow_classify(store, message)

