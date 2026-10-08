"""Classification semantics, bounded examples and evaluation metrics.

Category (what a mail is about) and spam (whether it should be filtered) are
independent. A human label is authoritative only for fields they confirmed.
"""
import hashlib
import json
import re
from pathlib import Path

PROMPT_PATH = Path(__file__).parent / 'prompts' / 'perception.md'
PROMPT_VERSION = 'mail-perception-v2'
FIELDS = ('category', 'spam_label', 'priority', 'needs_reply')


def fingerprint(body):
    return hashlib.sha256(json.dumps({k:body.get(k,'') for k in ('sender','subject','text')},
                                    ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def template_key(body):
    """Conservative template grouping: remove addresses, IDs and punctuation.

    This excludes exact structural duplicates; it is not a semantic leakage
    detector. Near-template datasets still require independent human review.
    """
    value=(body.get('subject','')+'\n'+body.get('text','')).lower()
    value=re.sub(r'[\w.+-]+@[\w.-]+','<address>',value)
    value=re.sub(r'[a-z]*\d[\w.:-]*','<id>',value)
    value=re.sub(r'\W+','',value)
    return hashlib.sha256(value.encode()).hexdigest()


def predicted_labels(result):
    value={k:result[k] for k in ('category','priority','needs_reply') if k in result}
    score=result.get('spam_score',0)
    value['spam_label']='spam' if score>=.9 and result.get('confidence',0)>=.85 else 'normal' if score<.5 else 'uncertain'
    return value


def summarize_metrics(results):
    counts={field:{'confirmed':0,'correct':0} for field in FIELDS}
    normal=spam=misfiltered=missed=review=0
    for row in results:
        labels=row['labels'];pred=row['prediction']
        for field in FIELDS:
            if field in labels and not (field=='spam_label' and labels[field]=='uncertain'):
                counts[field]['confirmed']+=1
                counts[field]['correct']+=int(pred.get(field)==labels[field])
        label=labels.get('spam_label')
        if label in ('normal','spam'):
            normal+=int(label=='normal');spam+=int(label=='spam')
            filtered=row.get('decision',{}).get('action')=='filtered'
            misfiltered+=int(label=='normal' and filtered)
            missed+=int(label=='spam' and not filtered)
            review+=int(row.get('decision',{}).get('action')=='review')
    def rate(a,b):return round(a/b,4) if b else None
    return {'fields':{k:{**v,'accuracy':rate(v['correct'],v['confirmed'])} for k,v in counts.items()},
            'normal_count':normal,'spam_count':spam,'false_positive_count':misfiltered,
            'false_negative_count':missed,'false_positive_rate':rate(misfiltered,normal),
            'false_negative_rate':rate(missed,spam),'review_rate':rate(review,normal+spam)}


def filter_decision(body,result,rules):
    from backend.app.modules.mail.filtering import _match_sender,should_filter
    if not rules.get('enabled',True):return {'action':'active','reason':'过滤已禁用'}
    if _match_sender(body.get('sender',''),rules.get('whitelist_senders',[])):
        return {'action':'active','reason':'用户白名单保护'}
    entry_rules=rules if rules.get('classification_mode','model')=='rules' else {**rules,'auto_filter_categories':[]}
    blocked,reason,_,_=should_filter({'sender_id':body.get('sender',''),
                                    'text':body.get('subject','')+'\n'+body.get('text','')},rules=entry_rules)
    if blocked:return {'action':'filtered','reason':reason}
    if rules.get('classification_mode','model')=='rules':
        return {'action':'active','reason':'规则模式未命中过滤条件'}
    score=result.get('spam_score',0);confidence=result.get('confidence',0)
    protected=result.get('needs_reply') or result.get('todos') or result.get('calendar_events')
    if result.get('spam_label')=='uncertain':return {'action':'review','reason':'用户暂不能确定，不自动过滤'}
    if score>=.9 and confidence>=.85 and not protected:
        return {'action':'filtered','reason':'AI 高分疑似垃圾'}
    if score>=.5:
        return {'action':'review','reason':'判断不足或与业务事项冲突，保留邮件待复核'}
    return {'action':'active','reason':'未满足垃圾过滤条件'}
