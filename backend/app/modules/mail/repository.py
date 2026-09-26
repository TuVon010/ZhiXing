"""Mail schema and account-scoped records; no network access at import time."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
from sqlalchemy import text
from backend.app.persistence.store import now, uid, encode
from backend.app.modules.mail.schemas import MailAccount


def initialize(db):
    with db.engine.begin() as c:
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_refs(account_id TEXT,reference TEXT,thread_id TEXT,PRIMARY KEY(account_id,reference))')
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_jobs(id TEXT PRIMARY KEY,kind TEXT,account_id TEXT,scope TEXT,priority INTEGER,status TEXT,lease REAL DEFAULT 0,attempts INTEGER DEFAULT 0,dedupe TEXT UNIQUE,payload TEXT,result TEXT,created_at TEXT,updated_at TEXT)')
        c.exec_driver_sql('CREATE INDEX IF NOT EXISTS mail_jobs_pending ON mail_jobs(status,priority,created_at)')
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_chunks(id TEXT PRIMARY KEY,account_id TEXT,message_id TEXT,thread_id TEXT,location TEXT,content TEXT,content_hash TEXT,embedding BLOB,model_version TEXT,received_at TEXT,sender TEXT)')
        c.exec_driver_sql('CREATE INDEX IF NOT EXISTS mail_chunks_account ON mail_chunks(account_id,message_id)')
        c.exec_driver_sql('CREATE VIRTUAL TABLE IF NOT EXISTS mail_fts USING fts5(chunk_id UNINDEXED, content)')
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_vectors(content_hash TEXT,model_version TEXT,embedding BLOB,PRIMARY KEY(content_hash,model_version))')
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_leases(name TEXT PRIMARY KEY,owner TEXT,until REAL)')
        c.exec_driver_sql('CREATE TABLE IF NOT EXISTS mail_source_fingerprints(account_id TEXT,folder TEXT,fingerprint TEXT,record_id TEXT,PRIMARY KEY(account_id,folder,fingerprint))')
        columns={row[1] for row in c.exec_driver_sql('PRAGMA table_info(mail_source_fingerprints)')}
        if 'message_id' in columns and 'record_id' not in columns:
            c.exec_driver_sql('ALTER TABLE mail_source_fingerprints RENAME COLUMN message_id TO record_id')


def rows(db,kind,account_ids=None,status=None,limit=100,offset=0):
    sql='SELECT * FROM records WHERE kind=:kind'; args={'kind':kind,'limit':limit,'offset':offset}
    if account_ids is not None:
        if not account_ids:
            return []
        slots=[]
        for i,aid in enumerate(account_ids):
            slots.append(':a'+str(i));args['a'+str(i)]=aid
        sql+=' AND scope IN ('+','.join(slots)+')'
    if status:
        sql+=' AND status=:status';args['status']=status
    with db.engine.connect() as c:
        data=c.execute(text(sql+' ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset'),args).mappings().all()
    return [{**dict(r),'body':json.loads(r['body'])} for r in data]


def require(db,ident,kind,accounts=None,conn=None):
    r=db.get(ident,conn)
    if r['kind']!=kind or (accounts is not None and r['scope'] not in accounts):
        raise ValueError('对象类型或账号范围不匹配')
    return r


def validate_accounts(db,ids):
    for aid in ids:
        require(db,aid,'mail_account')
    return list(dict.fromkeys(ids))


class Blob(ctypes.Structure):
    _fields_=[('size',ctypes.c_ulong),('data',ctypes.POINTER(ctypes.c_ubyte))]


def protect(value,decrypt=False):
    if os.name!='nt':
        raise ValueError('凭证存储需要 Windows DPAPI；此环境可运行无凭证测试')
    buf=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(buf,ctypes.POINTER(ctypes.c_ubyte)));out=Blob()
    func=ctypes.windll.crypt32.CryptUnprotectData if decrypt else ctypes.windll.crypt32.CryptProtectData
    if not func(ctypes.byref(source),None,None,None,None,1,ctypes.byref(out)):
        raise ValueError('凭证无法加密或解密，请在当前 Windows 用户下重新填写')
    try:
        return ctypes.string_at(out.data,out.size)
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(out.data,ctypes.c_void_p))


def save_secret(db,password):
    ident=uid();folder=db.path.parent/'credentials';folder.mkdir(exist_ok=True)
    (folder/(ident+'.dpapi')).write_bytes(protect(password.encode()))
    return ident


def secret(db,account):
    ref=account.get('credential_ref')
    if ref=='legacy-env':
        from backend.app.core.config import settings
        if account['address']!=settings.mail_address.lower():
            raise ValueError('旧账号凭证不匹配')
        return settings.mail_password
    if not ref or not ref.isalnum():
        raise ValueError('请配置邮箱授权码')
    return protect((db.path.parent/'credentials'/(ref+'.dpapi')).read_bytes(),True).decode()


def public_account(row):
    b=dict(row['body']);b.setdefault('auto_import_enabled',False);b.setdefault('auto_import_days',7);b.setdefault('poll_interval_seconds',15)
    b['credential_configured']=bool(b.pop('credential_ref',None));b.pop('password',None)
    return {**row,'body':b}


def save_account(db,value:MailAccount,ident=None):
    body=value.model_dump(exclude={'password'})
    if ident:
        old=require(db,ident,'mail_account')['body']
        if old.get('test_account') and (value.enabled or value.password):
            raise ValueError('测试邮箱不可配置真实收发或凭证')
        if any(body[k]!=old[k] for k in ('address','username','imap_host','imap_port','imap_tls')):
            raise ValueError('账号或收件服务器变更请新建账号，避免混用游标')
        body={**old,**body}
    if value.password is not None:
        body['credential_ref']=save_secret(db,value.password) if value.password else None
    if ident:
        db.update(ident,body)
    else:
        ident=db.insert('mail_account',body)
    return public_account(db.get(ident))


def enqueue(db,kind,payload,account_id='',scope=None,priority=10,dedupe=None):
    initialize(db)
    ident=uid()
    with db.engine.begin() as c:
        c.execute(text('INSERT OR IGNORE INTO mail_jobs(id,kind,account_id,scope,priority,status,dedupe,payload,created_at,updated_at) VALUES(:id,:kind,:account,:scope,:priority,\'queued\',:dedupe,:payload,:at,:at)'),
                  {'id':ident,'kind':kind,'account':account_id,'scope':scope or account_id,'priority':priority,'dedupe':dedupe,'payload':encode(payload),'at':now()})
        if dedupe:
            ident=c.execute(text('SELECT id FROM mail_jobs WHERE dedupe=:key'),{'key':dedupe}).scalar_one()
    return ident


def job(db,ident):
    with db.engine.connect() as c:
        row=c.execute(text('SELECT * FROM mail_jobs WHERE id=:id'),{'id':ident}).mappings().first()
    if not row:
        raise KeyError(ident)
    return {**dict(row),'payload':json.loads(row['payload']),'result':json.loads(row['result']) if row['result'] else None}


def migrate(db):
    """Idempotent, explicit migration. Existing checkpoints stay byte-for-byte intact."""
    initialize(db)
    from backend.app.core.config import settings
    with db.engine.connect() as c:
        c.exec_driver_sql('BEGIN IMMEDIATE')
        if c.execute(text('SELECT 1 FROM migrations WHERE version=2')).first():
            return
        account_map={}
        if settings.mail_address:
            aid='mail-legacy-'+hashlib.sha256(settings.mail_address.lower().encode()).hexdigest()[:16]
            b=MailAccount(name='原 QQ 邮箱',address=settings.mail_address).model_dump(exclude={'password'})
            db.insert('mail_account',{**b,'credential_ref':'legacy-env','migration_review':True},id=aid,conn=c)
            account_map[settings.mail_address.lower()]=aid
        old=c.execute(text("SELECT * FROM records WHERE kind='message' AND json_extract(body,'$.source')='email'")).mappings().all()
        for record in old:
            body=json.loads(record['body']);rawid=body.get('message_id','');address=rawid.rsplit(':',2)[0].lower()
            aid=account_map.get(address,'legacy-unassigned');ident='mail-old-'+record['id'];tid='thread-old-'+record['id']
            db.insert('mail_thread',{'account_id':aid,'subject':body['text'].split('\n')[0]},scope=aid,id=tid,conn=c)
            db.insert('mail_message',{'account_id':aid,'thread_id':tid,'subject':body['text'].split('\n')[0],
                'text':body['text'],'raw_text':body['text'],'sender':body.get('sender_id',''),'to':[],'cc':[],
                'received_at':body.get('timestamp') or record['created_at'],'declared_at':None,'fetched_at':record['created_at'],
                'legacy_id':record['id'],'incomplete':True,'headers':{},'attachments':[],'index_status':'pending'},
                scope=aid,id=ident,status='legacy',conn=c)
        c.execute(text("UPDATE jobs SET status='migration_review' WHERE status NOT IN ('done','failed')"))
        c.execute(text("UPDATE records SET body=json_set(body,'$.migration_previous_status',status),status='migration_review' WHERE kind='run' AND status IN ('queued','running','waiting_approval')"))
        c.execute(text("UPDATE records SET status='migration_review' WHERE kind='notification' AND status IN ('pending','queued','failed')"))
        c.execute(text('INSERT INTO migrations VALUES(2,:at)'),{'at':now()});c.commit()
