import json
import hashlib
import regex
from backend.app.persistence.store import encode, now
from backend.app.agent.schemas import ActionPlan
from backend.app.agent.policy import HIGH, risk
from backend.app.agent.tools import validate

BUILTINS = {
 'todo-extraction':'提取任务、负责人、截止时间和来源；任务不代表已经完成。',
 'commitment-detection':'识别用户承诺和承诺对象；仅记录证据支持的内容，不猜测联系人身份。',
 'meeting-extraction':'识别会议日期、开始和结束时间；时间不明确时请求澄清，所有时间包含时区。',
 'email-reply':'先生成邮件草稿；发送必须独立规划 send_email 并交由风险策略审批。',
 'daily-summary':'只根据真实任务和消息生成每日摘要，明确已完成、待办、逾期和待审批。',
}

def seed(db):
    from sqlalchemy.exc import IntegrityError
    for name, content in BUILTINS.items():
        if not any(x['body']['name']==name for x in db.list('skill',limit=10000)):
            try:
                db.insert('skill',{'name':name,'version':1,'content':content,'builtin':True,'published_at':now()},status='published',id='builtin-'+name)
            except IntegrityError:
                pass

def snapshot_hash(body):
    return hashlib.sha256(encode({k:body.get(k) for k in ['name','content','rules']}).encode()).hexdigest()

def parse_rules(rules, message):
    """No eval/templates with expression access; only bounded named-group substitution."""
    if not isinstance(rules,dict) or len(encode(rules))>16000:
        raise ValueError('解析规则过大或格式无效')
    pattern=rules.get('pattern','')
    if not pattern or len(pattern)>500 or any(x in pattern for x in ['(?R','(?0','(?P=','\\g<','(?<=','(?<!']):
        raise ValueError('不支持的解析表达式')
    match=regex.fullmatch(pattern,message['text'][:30000],timeout=.02)
    if not match:
        return None
    groups=match.groupdict()
    def substitute(value):
        if isinstance(value,str):
            return regex.sub(r'\{([a-zA-Z_][a-zA-Z_0-9]*)\}',lambda m:groups.get(m[1]) or '',value)
        if isinstance(value,dict):
            return {k:substitute(v) for k,v in value.items()}
        if isinstance(value,list):
            return [substitute(v) for v in value]
        return value
    result=ActionPlan.model_validate(substitute(rules['plan']))
    if any(validate(a.model_dump()) for a in result.actions):
        return None
    return result

def parser_plan(db,message,versions):
    for ident in versions.get('parsers',[]):
        parser=db.get(ident)
        try:
            result=parse_rules(parser['body']['rules'],message)
        except (ValueError,KeyError,TimeoutError):
            result=None
        db.insert('parser_observation',{'parser_id':ident,'message_id':message['message_id'],'hit':bool(result)},scope=message['source']+':'+message['conversation_id'])
        if result:
            return result
    return None

def observe_shadow(db,message,reference):
    # Diagnostic comparison only. Human-confirmed shadow samples remain the release gate.
    for row in db.list('parser',status='candidate'):
        try:
            predicted=parse_rules(row['body']['rules'],message)
            matched=predicted is not None and comparable(predicted)==comparable(reference)
            db.insert('parser_shadow',{'parser_id':row['id'],'message':message,'reference':reference.model_dump(),'matched':matched,'human_confirmed':False},scope=message['source']+':'+message['conversation_id'])
        except (ValueError,KeyError,TimeoutError):
            continue

def candidate(db,kind,name,content=None,rules=None,evidence=None):
    if kind not in {'skill','parser'}:
        raise ValueError('无效候选类型')
    evidence=evidence or []
    if kind=='parser':
        confirmed=[db.get(i) for i in evidence]
        if len(set(evidence))<20 or any(r['kind']!='sample' or r['status']!='confirmed' or r['body']['split']!='train' for r in confirmed):
            raise ValueError('Parser 候选需要至少20条已确认训练样本')
        # Syntax and bounded interpreter are checked even before a match.
        parse_rules(rules,{'text':''})
    existing=[r for r in db.list(kind,limit=10000) if r['body']['name']==name]
    version=max([r['body']['version'] for r in existing],default=0)+1
    return db.insert(kind,{'name':name,'version':version,'content':content or '', 'rules':rules,'evidence':evidence},status='candidate')

def curate(db,kind,name,evidence):
    from backend.app.agent.model_client import model_json
    rows=[db.get(i) for i in evidence]
    if not rows:
        raise ValueError('请选择真实运行或确认样本作为证据')
    if any(r['kind'] not in {'run','sample'} for r in rows):
        raise ValueError('不支持的证据类型')
    existing=[r['body'] for r in db.list(kind,status='published') if r['body']['name']==name]
    prompt='根据失败案例改进个人助理规则。只返回 JSON {content:文本}。' if kind=='skill' else '生成声明式解析器，只返回 JSON {rules:{pattern:带命名捕获的全匹配正则,plan:{summary:文本,actions:动作数组}}}。字段替换仅支持 {捕获名}。不得生成代码。'
    result=model_json(db,[{'role':'system','content':prompt},{'role':'user','content':encode({'baseline':existing,'evidence':[r['body'] for r in rows]})}],purpose='evolution')
    return candidate(db,kind,name,result.get('content'),result.get('rules'),evidence)

def comparable(plan):
    return [{'tool':a.tool,'args':a.args,'clarification':bool(a.clarification)} for a in plan.actions]

def evaluate(db,ident):
    from backend.app.agent.model_client import model_json
    row=db.get(ident); body=row['body'];kind=row['kind']
    if kind not in {'skill','parser'}:
        raise ValueError('只能评测 Skill 或 Parser')
    samples=[r for r in db.list('sample',limit=10000,status='confirmed') if r['body']['split']=='holdout' and r['body'].get('name')==body['name']]
    baseline=next((r for r in db.list(kind,status='published') if r['body']['name']==body['name']),None)
    scores={'candidate':0,'baseline':0};safety=0;errors=[]
    def predict(b,message):
        if kind=='parser':
            return parse_rules(b['rules'],message)
        return ActionPlan.model_validate(model_json(db,[{'role':'system','content':b['content']},{'role':'user','content':encode(message)}],ActionPlan.model_json_schema(),'evaluation'))
    for sample in samples:
        b=sample['body']; expected=ActionPlan.model_validate(b['expected'])
        for label, version in [('candidate',body),('baseline',baseline['body'] if baseline else None)]:
            if version is None:
                continue
            try:
                predicted=predict(version,b['message'])
                exact=predicted is not None and comparable(predicted)==comparable(expected)
                scores[label]+=int(exact)
                if label=='candidate' and b.get('safety'):
                    safety+=int(exact)
            except Exception as e:
                errors.append({'sample_id':sample['id'],'version':label,'error':type(e).__name__})
    safety_total=sum(bool(r['body'].get('safety')) for r in samples)
    result={'artifact_id':ident,'hash':snapshot_hash(body),'samples':len(samples),'safety_total':safety_total,'safety_passed':safety,'scores':scores,'errors':errors,'passed':len(samples)>=20 and safety_total>=5 and safety==safety_total and scores['candidate']>=scores['baseline'] and scores['candidate']/max(1,len(samples))>=.9 and not errors}
    eid=db.insert('evaluation',result,status='passed' if result['passed'] else 'failed')
    db.update(ident,{**body,'evaluation_id':eid})
    return result

def shadow(db,ident):
    row=db.get(ident)
    if row['kind']!='parser':
        raise ValueError('仅 Parser 支持影子比较')
    samples=[r for r in db.list('sample',limit=10000,status='confirmed') if r['body']['split']=='shadow' and r['body'].get('name')==row['body']['name']]
    hits=0
    for sample in samples:
        try:
            p=parse_rules(row['body']['rules'],sample['body']['message'])
            success=p is not None and comparable(p)==comparable(ActionPlan.model_validate(sample['body']['expected']))
        except (ValueError,TimeoutError,KeyError):
            success=False
        hits+=int(success)
    result={'count':len(samples),'passed':hits,'hash':snapshot_hash(row['body'])}
    db.update(ident,{**row['body'],'shadow':result})
    return result

def publish(db,ident,rollback=False):
    row=db.get(ident);b=row['body']
    if row['kind'] not in {'skill','parser'}:
        raise ValueError('无效发布对象')
    if rollback:
        if row['status']!='archived' or not b.get('published_at'):
            raise ValueError('只能回滚到曾发布版本')
    elif not b.get('builtin'):
        result=db.get(b.get('evaluation_id',''))['body']
        if not result['passed'] or result['hash']!=snapshot_hash(b):
            raise ValueError('评测未通过或候选内容已改变')
        if row['kind']=='parser':
            sh=b.get('shadow',{})
            if sh.get('hash')!=snapshot_hash(b) or sh.get('count',0)<20 or sh.get('passed')!=sh.get('count'):
                raise ValueError('需要至少20次全部通过的独立影子比较')
    with db.engine.begin() as c:
        for old in db.list(row['kind'],status='published'):
            if old['body']['name']==b['name']:
                db.update(old['id'],status='archived',conn=c)
        db.update(ident,{**b,'published_at':now()},'published',conn=c)
    db.audit(ident,'ARTIFACT_PUBLISHED',rollback=rollback,kind=row['kind'])
