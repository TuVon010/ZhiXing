"""Account-scoped human-label, API-evaluation and prompt publication endpoints."""
from typing import Literal
from fastapi import APIRouter,Depends,Query
from pydantic import BaseModel,ConfigDict,Field
from backend.app.api.dependencies import get_store
from backend.app.modules.mail.repository import require
from backend.app.modules.mail.services import classification as service

router=APIRouter()


class ReferenceConfig(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled:bool=True
    max_examples:int=Field(default=3,ge=0,le=5)


class EvaluationInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    split:Literal['optimization','holdout']='optimization'
    limit:int=Field(default=20,ge=1,le=30)
    candidate_id:str|None=None


class PublishInput(BaseModel):
    evaluation_id:str


@router.get('/mail/accounts/{account_id}/classification')
def classification(account_id:str,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0),db=Depends(get_store)):
    return service.overview(db,account_id,limit,offset)


@router.post('/mail/accounts/{account_id}/classification/config')
def configure(account_id:str,body:ReferenceConfig,db=Depends(get_store)):
    return service.configure(db,account_id,body.model_dump())


@router.post('/mail/accounts/{account_id}/classification/labels/{ident}/withdraw')
def withdraw(account_id:str,ident:str,db=Depends(get_store)):
    row=require(db,ident,'mail_classification_label',[account_id])
    db.update(ident,row['body'],'withdrawn')
    db.audit(ident,'MAIL_CLASSIFICATION_LABEL_WITHDRAWN',account_id=account_id)
    return {'withdrawn':True,'mail_override_preserved':True}


@router.post('/mail/accounts/{account_id}/classification/labels/backfill')
def backfill(account_id:str,db=Depends(get_store)):
    require(db,account_id,'mail_account')
    from backend.app.modules.mail.schemas import PerceptionFeedback
    from backend.app.modules.mail.repositories.classification import current_label,correct_mail
    from backend.app.modules.mail.repository import rows
    count=0
    for mail in rows(db,'mail_message',[account_id],limit=10000):
        if mail['status'] not in ('active','filtered','review','archived') or current_label(db,mail['id']):continue
        overrides=mail['body'].get('perception_overrides') or {}
        fields={k:overrides[k] for k in ('category','priority','needs_reply','spam_label','spam_score') if k in overrides}
        if not fields:continue
        correct_mail(db,PerceptionFeedback(message_id=mail['id'],**fields))
        count+=1
    return {'imported':count,'note':'仅迁移明确人工覆盖的字段，不推断其他标签'}


@router.post('/mail/accounts/{account_id}/classification/candidates')
def candidate(account_id:str,db=Depends(get_store)):
    return service.create_candidate(db,account_id)


@router.post('/mail/accounts/{account_id}/classification/evaluations')
def evaluate(account_id:str,body:EvaluationInput,db=Depends(get_store)):
    return service.create_evaluation(db,account_id,body.split,body.limit,body.candidate_id)


@router.get('/mail/accounts/{account_id}/classification/evaluations/{ident}')
def evaluation(account_id:str,ident:str,db=Depends(get_store)):
    return require(db,ident,'mail_classification_evaluation',[account_id])


@router.post('/mail/accounts/{account_id}/classification/evaluations/{ident}/export')
def export(account_id:str,ident:str,db=Depends(get_store)):
    import json,hashlib,time
    row=require(db,ident,'mail_classification_evaluation',[account_id])
    folder=db.path.parent/'mail-evaluations';folder.mkdir(parents=True,exist_ok=True)
    path=folder/(hashlib.sha256(ident.encode()).hexdigest()+f'-{time.time_ns()}.json')
    path.write_text(json.dumps(row,ensure_ascii=False,indent=2),encoding='utf-8')
    return {'path':str(path),'contains_private_mail':True}


@router.post('/mail/accounts/{account_id}/classification/evaluations/{ident}/cancel')
def cancel(account_id:str,ident:str,db=Depends(get_store)):
    return service.cancel_evaluation(db,account_id,ident)


@router.post('/mail/accounts/{account_id}/classification/candidates/{ident}/publish')
def publish(account_id:str,ident:str,body:PublishInput,db=Depends(get_store)):
    return service.publish(db,account_id,ident,body.evaluation_id)


@router.post('/mail/accounts/{account_id}/classification/rollback')
def rollback(account_id:str,db=Depends(get_store)):
    return service.rollback(db,account_id)
