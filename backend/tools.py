import re
from datetime import datetime
from sqlalchemy import text
from .channels import ExternalUnknown
from .config import settings
from .db import encode, now

EXTERNAL = {'send_email'}

def validate(action):
    tool, a = action['tool'], action['args']
    required = {'create_todo':['title'],'update_todo':['id'],'create_calendar':['title','start','end'],'update_calendar':['id','title','start','end'],'create_reminder':['title','due_at'],'draft_email':['subject','content'],'summarize':['content'],'send_email':['recipient','subject','content'],'delete_item':['id']}
    required['create_memory_candidate']=['content']
    for field in required[tool]:
        if not a.get(field):
            return '请补充 '+field
    if tool == 'send_email' and not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+',a['recipient']):
        return '请输入单个有效收件邮箱'
    for field in ['due_at','deadline','start','end']:
        if a.get(field):
            try:
                dt = datetime.fromisoformat(a[field])
                if dt.tzinfo is None:
                    return '时间必须带时区'
            except (ValueError,TypeError):
                return '时间格式无效：'+field
    if tool in {'create_calendar','update_calendar'} and datetime.fromisoformat(a['end']) <= datetime.fromisoformat(a['start']):
        return '结束时间必须晚于开始时间'
    return None

def execute(db, action, run_id, scope, dry_run=False):
    aid, tool, args = action['id'],action['tool'],action['args']
    issue = validate(action)
    if issue:
        raise ValueError(issue)
    if dry_run:
        return {'simulated':True,'replay':True,'id':'replay-'+aid,'tool':tool,'args':args}
    if tool == 'send_email' and settings.mode != 'demo' and not args.get('draft_id'):
        raise ValueError('旧版直接发送已停用；请在邮箱工作台创建草稿并提交审批')
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        row = c.execute(text('SELECT * FROM ledger WHERE action_id=:id'),{'id':aid}).mappings().first()
        if row:
            import json
            c.rollback()
            if row['status'] == 'completed':
                return json.loads(row['result'])
            raise ExternalUnknown('此前执行结果未确认，禁止自动重复执行')
        if args.get('draft_id'):
            from .mail_send import check_draft
            run=db.get(run_id,c)
            accounts=run['body']['message'].get('metadata',{}).get('mail_accounts',[])
            check_draft(db,args,accounts,conn=c)
        c.execute(text("INSERT INTO ledger VALUES(:id,'executing',NULL,:at)"),{'id':aid,'at':now()})
        if tool not in EXTERNAL:
            result = local_tool(db,tool,args,run_id,scope,c)
            c.execute(text("UPDATE ledger SET status='completed',result=:result,updated_at=:at WHERE action_id=:id"),{'result':encode(result),'at':now(),'id':aid})
            c.commit()
            return result
        c.commit()
    try:
        synthetic_draft = bool(args.get('draft_id') and db.get(args['account_id'])['body'].get('test_account'))
        if settings.mode == 'demo' or synthetic_draft:
            result = {'simulated':True,'tool':tool,'args':args}
            if args.get('draft_id'):
                db.update(args['draft_id'],status='simulated')
                from .mail_work_items import mark_replied
                mark_replied(db,args.get('message_id'),args['draft_id'])
        elif tool == 'send_email':
            if args.get('draft_id'):
                from .mail_send import send
                result=send(db,args,aid)
        with db.engine.begin() as c:
            c.execute(text("UPDATE ledger SET status='completed',result=:result,updated_at=:at WHERE action_id=:id"),{'result':encode(result),'at':now(),'id':aid})
        return result
    except Exception:
        # Keep the reservation on every external failure. Retrying requires human reconciliation.
        raise

def local_tool(db,tool,args,run_id,scope,conn):
    if tool=='create_memory_candidate':
        ident=db.insert('memory',{'content':args['content'],'run_id':run_id},scope=scope,status='candidate',conn=conn)
        return {'id':ident,'status':'candidate'}
    if tool in {'create_todo','create_calendar','create_reminder','draft_email'}:
        kind = {'create_todo':'todo','create_calendar':'calendar','create_reminder':'reminder','draft_email':'draft'}[tool]
        ident = db.insert(kind,{**args,'run_id':run_id,'sync_status':'local'},scope=scope,conn=conn)
        return {'id':ident,**args}
    if tool in {'update_todo','update_calendar','delete_item'}:
        item = db.get(args['id'],conn)
        if item['kind'] not in {'todo','calendar','reminder','draft'}:
            raise ValueError('不允许修改该对象')
        if item['scope'] != scope:
            raise ValueError('不能跨会话修改对象')
        if tool == 'update_todo' and item['kind'] != 'todo':
            raise ValueError('仅允许修改 Todo')
        if tool == 'update_calendar' and item['kind'] != 'calendar':
            raise ValueError('仅允许修改日程')
        allowed = {k:v for k,v in args.items() if k in {'title','deadline','owner','description','commitment_target','start','end'}}
        status = 'deleted' if tool=='delete_item' else args.get('status',item['status'])
        if status not in {'active','completed','cancelled','deleted'}:
            raise ValueError('无效任务状态')
        db.update(item['id'],{**item['body'],**allowed,'sync_status':'local'},status,conn)
        return {'id':item['id'],'status':status}
    return {'content':args['content']}
