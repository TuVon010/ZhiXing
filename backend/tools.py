import re
from datetime import datetime
from sqlalchemy import text
from .channels import Feishu, send_mail, ExternalUnknown
from .config import settings
from .db import encode, now

EXTERNAL = {'send_email','send_feishu','sync_todo','sync_calendar','invite_calendar'}

def validate(action):
    tool, a = action['tool'], action['args']
    required = {'create_todo':['title'],'update_todo':['id'],'create_calendar':['title','start','end'],'update_calendar':['id','title','start','end'],'create_reminder':['title','due_at'],'draft_email':['subject','content'],'summarize':['content'],'send_email':['recipient','subject','content'],'send_feishu':['recipient','content'],'sync_todo':['id'],'sync_calendar':['id'],'invite_calendar':['id','recipient'],'delete_item':['id']}
    required['create_memory_candidate']=['content']
    for field in required[tool]:
        if not a.get(field):
            return '请补充 '+field
    if tool == 'send_email' and not re.fullmatch(r'[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+',a['recipient']):
        return '请输入单个有效收件邮箱'
    if tool in {'send_feishu','invite_calendar'} and not re.fullmatch(r'ou_[A-Za-z0-9]+',a['recipient']):
        return '请输入有效飞书 open_id'
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
    if tool in {'sync_todo','sync_calendar','invite_calendar'} and db.get(args['id'])['scope']!=scope:
        raise ValueError('不能跨会话同步对象')
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        row = c.execute(text('SELECT * FROM ledger WHERE action_id=:id'),{'id':aid}).mappings().first()
        if row:
            import json
            c.rollback()
            if row['status'] == 'completed':
                return json.loads(row['result'])
            raise ExternalUnknown('此前执行结果未确认，禁止自动重复执行')
        c.execute(text("INSERT INTO ledger VALUES(:id,'executing',NULL,:at)"),{'id':aid,'at':now()})
        if tool not in EXTERNAL:
            result = local_tool(db,tool,args,run_id,scope,c)
            c.execute(text("UPDATE ledger SET status='completed',result=:result,updated_at=:at WHERE action_id=:id"),{'result':encode(result),'at':now(),'id':aid})
            c.commit()
            return result
        c.commit()
    try:
        if settings.mode == 'demo':
            result = {'simulated':True,'tool':tool,'args':args}
        elif tool == 'send_email':
            result = send_mail(args,aid)
        elif tool == 'send_feishu':
            result = Feishu().send(args['recipient'],args['content'],aid)
        elif tool == 'invite_calendar':
            item=db.get(args['id'])
            if item['kind']!='calendar' or not item['body'].get('external_id') or not settings.feishu_calendar_id:
                raise ValueError('日程需要先同步到飞书')
            result=Feishu().request('POST','/calendar/v4/calendars/'+settings.feishu_calendar_id+'/events/'+item['body']['external_id']+'/attendees?user_id_type=open_id',{'attendees':[{'type':'user','user_id':args['recipient']}]})
        else:
            result = Feishu().sync(db,'todo' if tool=='sync_todo' else 'calendar',args['id'],aid)
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
