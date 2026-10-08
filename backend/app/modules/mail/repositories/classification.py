"""Versioned, account-scoped human labels and frozen classification artifacts."""
from backend.app.modules.mail.classification import fingerprint,template_key,predicted_labels
from backend.app.persistence.store import now
from backend.app.modules.mail.repository import require


def current_label(db,message_id,conn=None):
    try:return db.get('classification-label:'+message_id,conn)
    except KeyError:return None


def correct_mail(db,feedback):
    """Commit mail overrides and the label revision together, with conflict checks."""
    from backend.app.persistence.transactions import begin_immediate
    confirmed={k:getattr(feedback,k) for k in ('category','priority','needs_reply') if getattr(feedback,k) is not None}
    spam=feedback.spam_label
    if feedback.spam_score is not None:
        inferred='normal' if feedback.spam_score<.5 else 'spam' if feedback.spam_score>=.8 else 'uncertain'
        if spam and spam!=inferred:raise ValueError('垃圾标签与评分冲突')
        spam=spam or inferred
    if spam is not None:confirmed['spam_label']=spam
    if not confirmed:raise ValueError('请至少确认一个判断字段')
    with db.engine.connect() as conn:
        begin_immediate(conn);mail=db.get(feedback.message_id,conn)
        if mail['kind']!='mail_message':raise ValueError('只能纠正邮件')
        old=current_label(db,mail['id'],conn);revision=old['body']['revision'] if old else 0
        if feedback.expected_revision is not None and feedback.expected_revision!=revision:
            raise ValueError('标注已被更新，请刷新后再保存')
        body=dict(mail['body']);previous=dict(body.get('perception') or {})
        raw=dict(body.get('perception_model') or previous);overrides=dict(body.get('perception_overrides') or {})
        overrides.update(confirmed)
        if spam in ('normal','spam'):overrides['spam_score']=0 if spam=='normal' else .95
        elif spam=='uncertain':overrides.pop('spam_score',None)
        perception={**raw,**overrides,'user_feedback':{'at':now(),'note':feedback.note,'remember_requested':feedback.remember}}
        body.update(perception_model=raw,perception_overrides=overrides,perception=perception)
        status=mail['status']
        if status in ('active','filtered','review'):
            if spam=='normal':status='active'
            elif spam=='spam':status='filtered'
            elif spam=='uncertain':status='review'
        if spam is not None:
            body['classification_decision']={'action':status,'reason':'用户明确确认','source':'human'}
            if spam=='spam':body['filter']={**body.get('filter',{}),'reason':'用户手动标记垃圾'}
        label=save_label(db,mail,confirmed,feedback.note,conn)
        body['classification_label_revision']=label['body']['revision']
        db.update(mail['id'],body,status,conn=conn);conn.commit()
    return mail,previous,body,perception,status


def save_label(db,message,confirmed,note,conn):
    """Called in the same transaction as the mail's human override update."""
    ident='classification-label:'+message['id'];old=current_label(db,message['id'],conn)
    body=message['body'];digest=fingerprint(body)
    previous=old['body'] if old and old['status']=='confirmed' and old['body']['content_hash']==digest else {}
    labels={**previous.get('labels',{}),**confirmed}
    if old and labels==previous.get('labels') and note==previous.get('note',''):
        return old
    revision=(old['body'].get('revision',0) if old else 0)+1
    sample={'message_id':message['id'],'account_id':message['scope'],'thread_id':body['thread_id'],
            'content_hash':digest,'template_key':template_key(body),'revision':revision,
            'labels':labels,'confirmed_fields':list(labels),'note':note,
            'mail':{k:body.get(k,'') for k in ('sender','subject','text','received_at')},
            'model_prediction':body.get('perception_model') or {},
            'predicted_labels':predicted_labels(body.get('perception_model') or {}),
            'decision':body.get('classification_decision') or {'action':message['status']},
            'versions':body.get('classification_context',{}).get('versions',{}),
            'source':'human_feedback','confirmed_at':now()}
    if old:db.update(ident,sample,'confirmed',conn=conn)
    else:db.insert('mail_classification_label',sample,id=ident,scope=message['scope'],status='confirmed',conn=conn)
    db.insert('mail_classification_label_revision',sample,id=ident+':'+str(revision),
              scope=message['scope'],status='confirmed',conn=conn)
    return db.get(ident,conn)


def eligible_labels(db,account_id):
    require(db,account_id,'mail_account')
    result=[]
    for row in db.list('mail_classification_label',scope=account_id,status='confirmed',limit=10000):
        try:mail=require(db,row['body']['message_id'],'mail_message',[account_id])
        except (KeyError,ValueError):continue
        if mail['status'] not in ('active','filtered','review','archived'):continue
        if fingerprint(mail['body'])!=row['body']['content_hash']:continue
        result.append(row)
    return result


def policy(db,account_id):
    try:pointer=db.get('classification-policy:'+account_id)['body']['policy_id']
    except KeyError:return {'id':'builtin-v2','body':{'guidance':'','version':1}}
    if not pointer:return {'id':'builtin-v2','body':{'guidance':'','version':1}}
    return require(db,pointer,'mail_classification_policy',[account_id])


def activate_policy(db,account_id,candidate,expected_current=None,published_body=None):
    from backend.app.persistence.transactions import begin_immediate
    with db.engine.connect() as conn:
        begin_immediate(conn)
        ident='classification-policy:'+account_id
        try:old=db.get(ident,conn)
        except KeyError:old=None
        current=(old['body'].get('policy_id') if old else None) or 'builtin-v2'
        if expected_current is not None and current!=expected_current:
            raise ValueError('当前分类版本已改变，必须重新评测')
        if published_body is not None:db.update(candidate['id'],published_body,'published',conn=conn)
        body={'policy_id':candidate['id'],'previous_id':current}
        if old:db.update(ident,body,conn=conn)
        else:db.insert('setting',body,id=ident,scope=account_id,conn=conn)
        conn.commit()
