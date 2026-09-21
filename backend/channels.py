import email
import imaplib
import json
import smtplib
import ssl
from email.message import EmailMessage
from email.policy import default
from datetime import datetime, timezone
import httpx
from .config import settings
from .db import now

class ExternalUnknown(Exception):
    pass

class Feishu:
    def __init__(self):
        self.base = 'https://open.feishu.cn/open-apis'

    def request(self, method, path, body=None):
        if not settings.feishu_app_id or not settings.feishu_app_secret:
            raise ValueError('飞书凭证未配置')
        with httpx.Client(timeout=30, trust_env=False) as c:
            auth = c.post(self.base+'/auth/v3/tenant_access_token/internal',json={'app_id':settings.feishu_app_id,'app_secret':settings.feishu_app_secret})
            auth.raise_for_status()
            token = auth.json().get('tenant_access_token')
            if not token:
                raise ValueError('飞书认证失败')
            try:
                r = c.request(method,self.base+path,json=body,headers={'Authorization':'Bearer '+token})
                r.raise_for_status()
                data = r.json()
            except (httpx.TransportError, httpx.HTTPStatusError) as e:
                if method != 'GET':
                    raise ExternalUnknown('飞书写入结果不明，请核对') from e
                raise
            if data.get('code'):
                raise ValueError('飞书接口拒绝：'+str(data.get('code')))
            return data.get('data',{})

    def send(self, recipient, content, action_id, card=False):
        return self.request('POST','/im/v1/messages?receive_id_type=open_id', {'receive_id':recipient,'msg_type':'interactive' if card else 'text','content':json.dumps(content if card else {'text':content},ensure_ascii=False),'uuid':action_id})

    def sync(self, db, kind, item_id, action_id):
        item = db.get(item_id)
        if item['kind'] != kind:
            raise ValueError('同步对象类型错误')
        b = item['body']
        external = b.get('external_id')
        if kind == 'todo':
            endpoint = '/task/v2/tasks'
            payload = {'summary':b['title'],'description':b.get('description',''),'client_token':action_id}
            if settings.feishu_owner:
                payload['members']=[{'id':settings.feishu_owner,'type':'user','role':'assignee'}]
            if item['status']=='completed':
                payload['completed_at']=int(datetime.now(timezone.utc).timestamp()*1000)
            if b.get('deadline'):
                payload['due'] = {'timestamp':str(int(datetime.fromisoformat(b['deadline']).timestamp()*1000)),'is_all_day':False}
            get_path = endpoint+'/'+external if external else ''
            wrapper = 'task'
        else:
            if not settings.feishu_calendar_id:
                raise ValueError('请配置有写权限的飞书日历 ID')
            endpoint = '/calendar/v4/calendars/'+settings.feishu_calendar_id+'/events'
            payload = {'summary':b['title'],'start_time':{'timestamp':str(int(datetime.fromisoformat(b['start']).timestamp())),'timezone':'Asia/Shanghai'},'end_time':{'timestamp':str(int(datetime.fromisoformat(b['end']).timestamp())),'timezone':'Asia/Shanghai'}}
            get_path = endpoint+'/'+external if external else ''
            wrapper = 'event'
        if external:
            current = self.request('GET',get_path).get(wrapper,{})
            if b.get('external_snapshot') != current:
                db.update(item_id,{**b,'sync_status':'conflict'})
                raise ValueError('飞书内容已修改，请核对冲突后重新同步')
        if external and kind == 'todo':
            update = dict(payload)
            update.pop('client_token',None)
            result = self.request('PATCH', get_path, {'task':update,'update_fields':list(update)})
        else:
            result = self.request('PATCH' if external else 'POST',get_path if external else endpoint,payload)
        value = result.get(wrapper,{})
        ext = external or value.get('guid') or value.get('event_id')
        if not ext:
            raise ExternalUnknown('接口未返回外部 ID，请核对')
        try:
            snapshot = self.request('GET',endpoint+'/'+ext).get(wrapper,{})
        except Exception as e:
            db.update(item_id,{**b,'external_id':ext,'sync_status':'unknown'})
            raise ExternalUnknown('写入后读取确认失败，请核对外部对象') from e
        db.update(item_id,{**b,'external_id':ext,'external_snapshot':snapshot,'sync_status':'synced'})
        return {'external_id':ext,'sync_status':'synced'}

def send_mail(args, action_id):
    if not settings.mail_address or not settings.mail_password:
        raise ValueError('QQ 邮箱尚未配置')
    msg = EmailMessage()
    msg['From'] = settings.mail_address
    msg['To'] = args['recipient']
    msg['Subject'] = args['subject']
    msg['Message-ID'] = '<'+action_id+'@zhixing.local>'
    msg.set_content(args['content'])
    with smtplib.SMTP_SSL('smtp.qq.com',465,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(settings.mail_address,settings.mail_password)
        try:
            smtp.send_message(msg)
        except (OSError,smtplib.SMTPException) as e:
            raise ExternalUnknown('SMTP 提交结果不明，请检查已发送记录') from e
    return {'message_id':str(msg['Message-ID'])}

def poll_mail(db, ingest, historical=False):
    if not settings.mail_address or not settings.mail_password:
        return
    cursor_id = 'mail-cursor'
    try:
        cursor = db.get(cursor_id)['body']
        if cursor.get('account',settings.mail_address)!=settings.mail_address:
            cursor=None
    except KeyError:
        cursor = None
    with imaplib.IMAP4_SSL('imap.qq.com',993,ssl_context=ssl.create_default_context(),timeout=30) as imap:
        imap.login(settings.mail_address,settings.mail_password)
        imap.select('INBOX',readonly=True)
        validity = str(imap.response('UIDVALIDITY')[1][0].decode())
        typ, data = imap.uid('search',None,'ALL')
        if typ != 'OK':
            raise ValueError('邮箱索引读取失败')
        uids = [int(x) for x in data[0].split()]
        if cursor is None:
            cursor = {'account':settings.mail_address,'validity':validity,'uid':0 if historical else max(uids,default=0)}
            try:
                db.get(cursor_id);db.update(cursor_id,cursor,'active')
            except KeyError:
                db.insert('channel',cursor,id=cursor_id)
        elif cursor['validity'] != validity:
            db.update(cursor_id,{**cursor,'error':'UIDVALIDITY 改变，需要确认历史导入'},'attention')
            if not historical:
                return
            cursor = {'account':settings.mail_address,'validity':validity,'uid':0}
        if historical:
            imported={r['body']['message_id'] for r in db.list('message',limit=100000) if r['body']['source']=='email'}
            selected=[u for u in uids if f'{settings.mail_address}:{validity}:{u}' not in imported][:100]
        else:
            selected=[u for u in uids if u>cursor['uid']][:100]
        for value in selected:
            typ, raw = imap.uid('fetch',str(value),'(BODY.PEEK[])')
            if typ != 'OK':
                raise ValueError('邮件读取失败，游标未推进')
            msg = email.message_from_bytes(next(x[1] for x in raw if isinstance(x,tuple)),policy=default)
            part = msg.get_body(preferencelist=('plain','html'))
            content = part.get_content() if part else ''
            if part and part.get_content_type() == 'text/html':
                from html.parser import HTMLParser
                class TextOnly(HTMLParser):
                    def __init__(self):
                        super().__init__(); self.parts=[]
                    def handle_data(self,data):
                        self.parts.append(data)
                p = TextOnly(); p.feed(content); content=' '.join(p.parts)
            ingest({'message_id':f'{settings.mail_address}:{validity}:{value}','source':'email','sender_id':str(msg.get('From','')),'conversation_id':str(msg.get('In-Reply-To') or msg.get('Message-ID') or value),'text':(str(msg.get('Subject',''))+'\n'+content)[:30000], 'timestamp':now(),'metadata':{'attachments':[{'name':p.get_filename(),'type':p.get_content_type()} for p in msg.iter_attachments()]}})
            cursor = {'account':settings.mail_address,'validity':validity,'uid':max(value,cursor['uid'])}
            db.update(cursor_id,cursor,'active')

def start_feishu(db, ingest, decide_approval):
    import lark_oapi as lark
    def receive(event):
        refresh_channel_settings(db)
        try:
            db.update('feishu-status',{'at':now(),'connection':'connected'})
        except KeyError:
            pass
        e = event.event
        m = e.message
        sender = e.sender.sender_id.open_id
        if m.chat_type == 'group' and m.chat_id not in settings.feishu_groups:
            return
        content = json.loads(m.content)
        message = content.get('text','')
        attachments=[]
        if m.message_type in {'file','image','audio','media'}:
            attachments=[{'name':content.get('file_name'), 'type':m.message_type, 'key':content.get('file_key') or content.get('image_key')}]
            message='收到附件（尚未解析内容）：'+str(content.get('file_name') or m.message_type)
        if not settings.feishu_owner:
            # Pairing can only be approved from the authenticated local console.
            db.insert('pairing',{'open_id':sender,'chat_id':m.chat_id},status='candidate',dedupe='pair:'+sender) if not any(r['body']['open_id']==sender for r in db.list('pairing')) else None
            return
        if m.chat_type != 'group' and sender != settings.feishu_owner:
            return
        if sender == settings.feishu_owner and message.startswith(('/approve ','/reject ','/edit ')):
            parts=message.split(' ',2)
            command,approval_id=parts[:2]
            row=db.get(approval_id)
            payload={'decision':{'/approve':'approve','/reject':'reject','/edit':'edit'}[command],'version':row['body']['version']}
            if command=='/edit':
                if len(parts)!=3:
                    raise ValueError('修改命令需要完整 JSON 参数')
                payload['args']=json.loads(parts[2])
            decide_approval(approval_id,payload)
            return
        if message:
            ingest({'message_id':m.message_id,'source':'feishu','sender_id':sender,'conversation_id':m.chat_id,'text':message,'timestamp':datetime.fromtimestamp(int(m.create_time)/1000,timezone.utc).isoformat(),'metadata':{'chat_type':m.chat_type,'attachments':attachments}})
    def card(event):
        refresh_channel_settings(db)
        from lark_oapi.event.callback.model.p2_card_action_trigger import P2CardActionTriggerResponse
        e = event.event
        if e.operator.open_id != settings.feishu_owner:
            return P2CardActionTriggerResponse({'toast':{'type':'error','content':'只有绑定用户可以审批'}})
        value = e.action.value
        try:
            decide_approval(value['approval_id'],{'decision':value['decision'],'version':int(value['version'])})
            return P2CardActionTriggerResponse({'toast':{'type':'success','content':'已记录'}})
        except Exception:
            return P2CardActionTriggerResponse({'toast':{'type':'error','content':'审批已变更，请打开控制台'}})
    handler = lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(receive).register_p2_card_action_trigger(card).build()
    def status(value):
        try:
            db.get('feishu-status');db.update('feishu-status',{'at':now(),'connection':value})
        except KeyError:
            db.insert('channel',{'at':now(),'connection':value},id='feishu-status')
    client=lark.ws.Client(settings.feishu_app_id,settings.feishu_app_secret,event_handler=handler,log_level=lark.LogLevel.ERROR)
    client.on_reconnecting=lambda:status('reconnecting')
    client.on_reconnected=lambda:status('connected')
    status('connecting')
    client.start()

def refresh_channel_settings(db):
    try:
        settings.feishu_owner=db.get('feishu-binding')['body']['open_id']
    except KeyError:
        pass
    try:
        settings.feishu_groups=db.get('runtime-settings')['body']['feishu_groups']
    except KeyError:
        pass
