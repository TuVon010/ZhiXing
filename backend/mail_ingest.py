"""Account-scoped IMAP collection. Collection never invokes the language model."""
import email
from email import policy
from email.utils import getaddresses, parsedate_to_datetime
import hashlib
from html.parser import HTMLParser
import imaplib
import json
import re
import ssl
import time
import threading
from datetime import datetime,timezone,timedelta
from contextlib import contextmanager
from sqlalchemy import text
from .db import now,uid,encode
from .mail_store import require,secret,enqueue,initialize
from .mail_models import ImportRequest


class TextHTML(HTMLParser):
    def __init__(self):
        super().__init__();self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','head'}:self.hidden+=1
        if tag in {'p','br','div','li'}:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','head'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def clean(body):
    lines=[]
    for line in body.splitlines():
        if re.match(r'^(On .+wrote:|在.+写道[：:]|-----Original Message-----|--\s*$)',line):
            break
        if not line.lstrip().startswith('>'):lines.append(line)
    return '\n'.join(lines).strip()


@contextmanager
def connection(db,account):
    ctx=ssl.create_default_context()
    imap=imaplib.IMAP4_SSL(account['imap_host'],account['imap_port'],ssl_context=ctx,timeout=30) if account['imap_tls']=='ssl' else imaplib.IMAP4(account['imap_host'],account['imap_port'],timeout=30)
    try:
        if account['imap_tls']=='starttls':imap.starttls(ssl_context=ctx)
        imap.login(account['username'],secret(db,account))
        yield imap
    finally:
        try:imap.logout()
        except Exception:pass


@contextmanager
def lease(db,account_id):
    initialize(db);owner=uid();key='imap:'+account_id
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        old=c.execute(text('SELECT * FROM mail_leases WHERE name=:key'),{'key':key}).mappings().first()
        if old and old['until']>time.time():raise ValueError('该账号正在同步，请稍后重试')
        c.execute(text('INSERT OR REPLACE INTO mail_leases VALUES(:key,:owner,:until)'),{'key':key,'owner':owner,'until':time.time()+120});c.commit()
    stopped=threading.Event()
    def renew():
        while not stopped.wait(20):
            with db.engine.begin() as c:c.execute(text('UPDATE mail_leases SET until=:until WHERE name=:key AND owner=:owner'),{'until':time.time()+120,'key':key,'owner':owner})
    heartbeat=threading.Thread(target=renew,daemon=True);heartbeat.start()
    try:yield
    finally:
        stopped.set();heartbeat.join()
        with db.engine.begin() as c:c.execute(text('DELETE FROM mail_leases WHERE name=:key AND owner=:owner'),{'key':key,'owner':owner})


def put(db,ident,kind,body,status='active'):
    try:db.get(ident);db.update(ident,body,status)
    except KeyError:db.insert(kind,body,id=ident,status=status)


def date_header(value):
    try:
        d=parsedate_to_datetime(value)
        return d.astimezone(timezone.utc).isoformat() if d.tzinfo else None
    except Exception:return None


def store_message(db,account_id,validity,mail_uid,raw,received_at,folder='INBOX'):
    initialize(db)
    identity=f'{account_id}:{folder}:{validity}:{mail_uid}'
    ident='mail-'+hashlib.sha256(identity.encode()).hexdigest()[:32]
    try:return require(db,ident,'mail_message')['id']
    except KeyError:pass
    message=email.message_from_bytes(raw,policy=policy.default)
    part=message.get_body(preferencelist=('plain','html'))
    body=part.get_content() if part else ''
    if not isinstance(body,str):body=''
    if part and part.get_content_type()=='text/html':
        parser=TextHTML();parser.feed(body);body=''.join(parser.parts)
    refs=re.findall(r'<[^<>\s]+>',str(message.get('References',''))+' '+str(message.get('In-Reply-To','')))
    mid=str(message.get('Message-ID',''));refs=list(dict.fromkeys(refs+[mid] if mid else refs))
    attachments=[];used=0
    for i,p in enumerate(message.iter_attachments()):
        data=p.get_payload(decode=True) or b'';size=len(data);used+=size
        name=str(p.get_filename() or f'attachment-{i}');suffix=name.rsplit('.',1)[-1].lower()
        status='pending' if suffix in {'pdf','docx','txt'} else 'unsupported'
        if size>10*1024**2 or used>20*1024**2:status='oversize'
        aid=ident+'-attachment-'+str(i)
        item={'id':aid,'name':name,'type':p.get_content_type(),'size':size,'format':suffix,'status':status}
        if status=='pending':
            folder_path=db.path.parent/'mail'/'attachments';folder_path.mkdir(parents=True,exist_ok=True)
            (folder_path/aid).write_bytes(data)
        attachments.append(item)
    from .filtering import should_filter,DEFAULT_RULES
    try:rules=db.get('mail-filter:'+account_id)['body']
    except KeyError:rules={**DEFAULT_RULES}
    # Parse actual addresses before applying the user's sender rules.
    sender=getaddresses([str(message.get('From',''))]);sender_address=sender[0][1].lower() if sender else ''
    subject=str(message.get('Subject',''))
    filtered,reason,category,evidence=should_filter({'sender_id':sender_address,'text':subject+'\n'+body},rules=rules)
    status='filtered' if filtered else 'review' if category=='manual' else 'active'
    tid='thread-'+uid()
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        # Duplicate event during a concurrent retry cannot create another thread.
        existing=c.execute(text('SELECT id FROM records WHERE id=:id'),{'id':ident}).first()
        if existing:return ident
        found=[]
        for ref in refs:
            r=c.execute(text('SELECT thread_id FROM mail_refs WHERE account_id=:a AND reference=:ref'),{'a':account_id,'ref':ref}).first()
            if r:found.append(r[0])
        if found:tid=found[0]
        else:db.insert('mail_thread',{'account_id':account_id,'subject':subject},id=tid,scope=account_id,conn=c)
        for old in set(found)-{tid}:
            c.execute(text("UPDATE records SET body=json_set(body,'$.thread_id',:tid) WHERE kind='mail_message' AND scope=:a AND json_extract(body,'$.thread_id')=:old"),{'tid':tid,'a':account_id,'old':old})
            c.execute(text('UPDATE mail_refs SET thread_id=:tid WHERE account_id=:a AND thread_id=:old'),{'tid':tid,'a':account_id,'old':old})
            c.execute(text('UPDATE mail_chunks SET thread_id=:tid WHERE account_id=:a AND thread_id=:old'),{'tid':tid,'a':account_id,'old':old})
            db.update(old,{'account_id':account_id,'merged_into':tid},'merged',conn=c)
        for ref in refs:c.execute(text('INSERT OR REPLACE INTO mail_refs VALUES(:a,:ref,:tid)'),{'a':account_id,'ref':ref,'tid':tid})
        value={'account_id':account_id,'thread_id':tid,'folder':folder,'uid':mail_uid,'uidvalidity':validity,
               'subject':subject,'sender':sender_address,'sender_display':str(message.get('From','')),
               'to':[a.lower() for _,a in getaddresses(message.get_all('To',[]))],
               'cc':[a.lower() for _,a in getaddresses(message.get_all('Cc',[]))],
               'reply_to':[a.lower() for _,a in getaddresses(message.get_all('Reply-To',[]))],
               'message_id':mid,'references':refs,'text':clean(body),'raw_text':body,
               'headers':dict((k,str(v)) for k,v in message.items()),'received_at':received_at,
               'declared_at':date_header(str(message.get('Date',''))),'fetched_at':now(),
               'attachments':attachments,'index_status':'pending','filter':{'reason':reason,'category':category,'evidence':evidence,'rules_version':rules.get('version',1)}}
        db.insert('mail_message',value,id=ident,scope=account_id,status=status,conn=c);c.commit()
    if status in ('active','review'):
        enqueue(db,'index',{'message_id':ident},account_id,priority=50,dedupe='index:'+ident)
        # 自动分析由用户逐账号启用；历史导入同样遵守此开关。
        account_options=require(db,account_id,'mail_account')['body']
        if account_options.get('enabled') and account_options.get('auto_analyze'):
            enqueue(db,'perception',{'message_id':ident},account_id,priority=40,dedupe='perception:'+ident)
    return ident


def internal_date(raw):
    header=b' '.join(v[0] if isinstance(v,tuple) else v for v in raw if v)
    m=re.search(rb'INTERNALDATE "([^"]+)"',header)
    if not m:raise ValueError('服务器未返回 INTERNALDATE，不能猜测收件时间')
    d=parsedate_to_datetime(m[1].decode().replace('-', ' ',2))
    return d.astimezone(timezone.utc)


def scan(db,account_id,request=None,preview=False,import_id=None):
    account=require(db,account_id,'mail_account')['body'];cursor_id='mail-cursor:'+account_id+':INBOX'
    if not account.get('enabled') and not request: return {'paused':True}
    spec=ImportRequest.model_validate(request) if request else None
    with lease(db,account_id),connection(db,account) as imap:
        typ,_=imap.select('INBOX',readonly=True)
        if typ!='OK':raise ValueError('无法读取 INBOX')
        validity=imap.response('UIDVALIDITY')[1][0].decode()
        try:cursor=db.get(cursor_id)['body']
        except KeyError:cursor=None
        # IMAP dates ignore time zones. Fetch a one-day margin, then enforce
        # precise UTC INTERNALDATE boundaries below; never download all headers.
        if spec:
            months=('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')
            def imap_date(value):return f'{value.day:02d}-{months[value.month-1]}-{value.year}'
            start=spec.start.astimezone(timezone.utc)-timedelta(days=1)
            end=spec.end.astimezone(timezone.utc)+timedelta(days=1)
            typ,data=imap.uid('search',None,'SINCE',imap_date(start),'BEFORE',imap_date(end))
        else:
            typ,data=imap.uid('search',None,'ALL')
        if typ!='OK':raise ValueError('无法读取邮件索引')
        all_uids=sorted(int(v) for v in (data[0] or b'').split())
        if not spec:
            if cursor is None:
                put(db,cursor_id,'mail_cursor',{'account_id':account_id,'validity':validity,'uid':max(all_uids,default=0),'last_poll':now(),'catchup':None})
                return {'baseline':True,'imported':0}
            if cursor['validity']!=validity:
                put(db,cursor_id,'mail_cursor',{**cursor,'error':'UIDVALIDITY 改变，请重新建立基线或历史导入'},'attention')
                return {'attention':'uidvalidity_changed'}
            if cursor.get('paused'):return {'paused':True,'reason':'积压需要选择处理方式'}
            if not cursor.get('catchup') and (datetime.now(timezone.utc)-datetime.fromisoformat(cursor['last_poll'])).total_seconds()>90:
                cursor['catchup']={'ceiling':max(all_uids,default=0),'count':0,'after':(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat()}
            selected=[u for u in all_uids if u>cursor['uid']][:account['scan_limit']]
        else:
            selected=all_uids
            if import_id:
                batch=require(db,import_id,'mail_import')['body']
                if batch.get('validity') and batch['validity']!=validity:raise ValueError('历史导入 UIDVALIDITY 已改变，请创建新批次')
                if not batch.get('validity'):
                    batch={**batch,'validity':validity};db.update(import_id,batch)
                selected=[u for u in selected if u>batch.get('last_uid',0)]
        matched=0;imported=0;examined=0;done=True
        for mail_uid in selected:
            if import_id:
                batchrow=require(db,import_id,'mail_import')
                if batchrow['status']!='running':return {'paused':True,'imported':imported}
                if batchrow['body'].get('imported',0)>=spec.limit:done=False;break
                if examined>=account['scan_limit']:done=False;break
            typ,meta=imap.uid('fetch',str(mail_uid),'(INTERNALDATE RFC822.SIZE BODY.PEEK[HEADER])')
            if typ!='OK':raise ValueError('邮件元信息读取失败；游标未推进')
            received=internal_date(meta);examined+=1
            if spec and not spec.start<=received<spec.end:
                if import_id:
                    b=db.get(import_id)['body'];db.update(import_id,{**b,'last_uid':mail_uid,'scanned':b.get('scanned',0)+1})
                continue
            matched+=1
            if preview:continue
            if not spec and cursor.get('catchup'):
                catchup=cursor['catchup']
                if received<datetime.fromisoformat(catchup['after']) or catchup['count']>=50:
                    cursor['paused']=True;put(db,cursor_id,'mail_cursor',cursor,'attention');done=False;break
            size_match=re.search(rb'RFC822.SIZE (\d+)',b' '.join(x[0] if isinstance(x,tuple) else x for x in meta if x))
            if size_match and int(size_match[1])>30*1024**2:
                raw=next((x[1] for x in meta if isinstance(x,tuple)),b'')+b'\r\n[Oversize message: body not downloaded]'
            else:
                typ,full=imap.uid('fetch',str(mail_uid),'(BODY.PEEK[])')
                if typ!='OK':raise ValueError('邮件正文读取失败；游标未推进')
                raw=next(x[1] for x in full if isinstance(x,tuple))
            ident=store_message(db,account_id,validity,mail_uid,raw,received.isoformat());imported+=1
            import_body=db.get(import_id)['body'] if import_id else None
            if import_body and import_body.get('analyze_after_import') and import_body.get('analysis_queued',0)<20:
                item=require(db,ident,'mail_message',[account_id])
                if item['status'] in {'active','review'} and not item['body'].get('perception'):
                    with db.engine.connect() as c:
                        pending=c.execute(text("SELECT 1 FROM mail_jobs WHERE kind='perception' AND account_id=:a AND json_extract(payload,'$.message_id')=:m AND status IN ('queued','running') LIMIT 1"),{'a':account_id,'m':ident}).first()
                    if not pending:
                        enqueue(db,'perception',{'message_id':ident,'manual':True,'import_id':import_id},
                                account_id,priority=25,dedupe='perception-import:'+ident)
                    db.update(import_id,{**import_body,'analysis_queued':import_body.get('analysis_queued',0)+1})
            if size_match and int(size_match[1])>30*1024**2:
                item=db.get(ident);db.update(ident,{**item['body'],'incomplete':True,'body_status':'oversize_not_downloaded'})
            if spec and import_id:
                b=db.get(import_id)['body'];db.update(import_id,{**b,'last_uid':mail_uid,'scanned':b.get('scanned',0)+1,'imported':b.get('imported',0)+1})
            elif not spec:
                cursor['uid']=mail_uid
                if cursor.get('catchup'):
                    cursor['catchup']['count']+=1
                    if mail_uid>=cursor['catchup']['ceiling']:cursor['catchup']=None
                put(db,cursor_id,'mail_cursor',cursor)
        if not spec:
            cursor['last_poll']=now();put(db,cursor_id,'mail_cursor',cursor,'attention' if cursor.get('paused') else 'active')
        elif import_id:
            with db.engine.connect() as c:
                c.exec_driver_sql('BEGIN IMMEDIATE')
                batch=require(db,import_id,'mail_import',conn=c);b=batch['body']
                done=done or b.get('imported',0)>=spec.limit
                # Yield after one scan batch while preserving an explicit user pause.
                if batch['status']=='running':db.update(import_id,b,'completed' if done else 'running',conn=c)
                c.commit()
        return {'matched':matched,'imported':imported,'preview':preview,'complete':done}
