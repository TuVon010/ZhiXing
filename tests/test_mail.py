"""Synthetic mail regressions. No provider credentials or network are used."""
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from email.message import EmailMessage
import pytest
from sqlalchemy import text
from backend.app.modules.mail.repository import initialize, save_account, rows, migrate, protect, enqueue, job
from backend.app.modules.mail.schemas import MailAccount, DraftInput, SessionInput, TurnInput
from backend.app.modules.mail.ingestion import store_message, scan, lease
from backend.app.modules.mail.assistant import draft, submit_draft, create_session, create_turn, run_turn, thread_messages
from backend.app.modules.mail.retrieval import index_message, search
from backend.app.agent.graph import work_once, approve
from backend.app.agent.tools import execute
from backend.app.core.exceptions import ExternalUnknown


def account(db, number=1):
    initialize(db)
    aid=save_account(db,MailAccount(name=f'测试{number}',address=f'owner{number}@qq.com',enabled=True))['id']
    from backend.app.modules.mail.filtering import DEFAULT_RULES
    db.insert('setting',{**DEFAULT_RULES,'whitelist_senders':['teacher@example.com']},id='mail-filter:'+aid)
    return aid


def message(db,aid,number=1,reference=None,subject='实验计划',body='请明天提交实验报告',mid=None):
    m=EmailMessage();m['From']='teacher@example.com';m['To']='owner1@qq.com, colleague@example.com'
    m['Cc']='colleague@example.com, another@example.com';m['Subject']=subject
    m['Message-ID']=mid or f'<m{number}@example.com>'
    if reference:m['In-Reply-To']=reference
    m.set_content(body)
    return store_message(db,aid,'100',number,m.as_bytes(),'2026-09-22T10:00:00+00:00')


def test_three_accounts_same_uid_and_message_id_are_isolated(db):
    accounts=[account(db,n) for n in range(3)]
    mails=[message(db,a) for a in accounts]
    assert len(set(mails))==3
    for aid,mid in zip(accounts,mails):
        assert message(db,aid)==mid
        assert len(rows(db,'mail_message',[aid]))==1
    assert len({db.get(m)['body']['thread_id'] for m in mails})==3


def test_headers_join_threads_but_subject_does_not(db):
    aid=account(db);first=message(db,aid);second=message(db,aid,2)
    reply=message(db,aid,3,reference='<m1@example.com>')
    tid=db.get(first)['body']['thread_id']
    assert db.get(second)['body']['thread_id']!=tid
    assert {m['id'] for m in thread_messages(db,tid,[aid])}=={first,reply}
    with pytest.raises(ValueError):thread_messages(db,tid,[account(db,2)])


def test_reply_all_excludes_self_and_duplicates(db):
    aid=account(db);mid=message(db,aid)
    d=draft(db,DraftInput(account_id=aid,message_id=mid,mode='reply_all',content='收到'))
    assert d['body']['to']==['teacher@example.com','colleague@example.com']
    assert d['body']['cc']==['another@example.com']
    assert d['body']['in_reply_to']=='<m1@example.com>'


def test_send_requires_approval_and_completed_ledger_replays(db):
    aid=account(db)
    d=draft(db,DraftInput(account_id=aid,to=['teacher@example.com'],subject='结果',content='实验已完成'))
    rid=submit_draft(db,d['id'],1)['run_id']
    assert db.get(d['id'])['status']=='approval'
    with pytest.raises(ValueError):submit_draft(db,d['id'],1)
    work_once(db);assert db.get(rid)['status']=='waiting_approval'
    a=db.for_run('approval',rid)[0]
    with pytest.raises(ValueError):approve(a['id'],{'decision':'edit','args':a['body']['action']['args']},db)
    approve(a['id'],{'decision':'approve'},db);work_once(db)
    assert db.get(d['id'])['status']=='simulated'
    # Crash replay must consult the ledger before checking the now-terminal draft.
    result=execute(db,a['body']['action'],rid,'web:mail:'+aid)
    assert result['simulated'] is True
    with pytest.raises(ValueError):approve(a['id'],{'decision':'approve'},db)


def test_edit_invalidates_approval(db):
    aid=account(db);v=DraftInput(account_id=aid,to=['x@example.com'],subject='原稿',content='一')
    d=draft(db,v);rid=submit_draft(db,d['id'],1)['run_id'];work_once(db)
    a=db.for_run('approval',rid)[0]
    edited=draft(db,v.model_copy(update={'content':'二','version':1}),d['id'])
    assert edited['body']['version']==2 and db.get(rid)['status']=='cancelled'
    with pytest.raises(ValueError):approve(a['id'],{'decision':'approve'},db)
    assert submit_draft(db,d['id'],2)['run_id']!=rid


def test_smtp_unknown_never_retries(db,monkeypatch):
    from backend.app.core.config import settings
    import backend.app.modules.mail.sending as sender
    aid=account(db);d=draft(db,DraftInput(account_id=aid,to=['x@example.com'],subject='测试',content='内容'))
    rid=submit_draft(db,d['id'],1)['run_id'];work_once(db)
    action=db.for_run('approval',rid)[0]['body']['action'];calls=[]
    class SMTP:
        def send_message(self,msg):calls.append(msg);raise OSError('lost after DATA')
    @contextmanager
    def fake(*args):yield SMTP()
    monkeypatch.setattr(sender,'smtp_connection',fake);monkeypatch.setattr(settings,'mode','live')
    with pytest.raises(ExternalUnknown):execute(db,action,rid,'web:mail:'+aid)
    with pytest.raises(ExternalUnknown):execute(db,action,rid,'web:mail:'+aid)
    assert len(calls)==1 and db.get(d['id'])['status']=='unknown'


def test_keyword_scope_visibility_timezone_and_fallback(db):
    a,b=account(db),account(db,2);m,n=message(db,a),message(db,b)
    index_message(db,m);index_message(db,n)
    result=search(db,{'account_ids':[a],'query':'实验报告','start':'2026-09-22T17:00:00+08:00','end':'2026-09-22T19:00:00+08:00'})
    assert result['degraded'] and result['mode']=='keyword'
    assert {e['message_id'] for e in result['evidence']}=={m}
    db.update(m,status='filtered')
    assert not search(db,{'account_ids':[a],'query':'实验报告'})['evidence']
    assert search(db,{'account_ids':[b],'query':'实验报告'})['evidence']


def test_memory_is_confirmed_scoped_and_frozen(db):
    a,b=account(db),account(db,2)
    db.insert('memory',{'content':'不能出现'},scope=b,status='published')
    db.insert('memory',{'content':'尚未批准'},scope=a,status='candidate')
    mid=db.insert('memory',{'content':'先写结论','memory_type':'preference'},scope=a,status='published')
    s=create_session(db,SessionInput(account_ids=[a]));t=create_turn(db,s['id'],TurnInput(text='总结实验'))
    snapshot=t['body']['memory_snapshot'];assert '先写结论' in snapshot['user_md']
    assert '不能出现' not in str(snapshot) and '尚未批准' not in str(snapshot)
    db.update(mid,status='suspended')
    assert db.get(t['id'])['body']['memory_snapshot']==snapshot
    t2=create_turn(db,s['id'],TurnInput(text='再次总结'))
    assert '先写结论' not in t2['body']['memory_snapshot']['user_md']


def test_demo_agent_records_retrieval_and_sources(db):
    aid=account(db);mid=message(db,aid);index_message(db,mid)
    s=create_session(db,SessionInput(account_ids=[aid]));t=create_turn(db,s['id'],TurnInput(text='实验报告'))
    run_turn(db,t['id']);result=db.get(t['id'])
    assert result['status']=='completed'
    assert [s['tool'] for s in result['body']['steps']]==['search','answer']
    assert result['body']['citations'] and db.for_run('audit',t['id'])
    assert (db.path.parent/'memory-snapshots'/t['id']/'USER.md').exists()


class FakeIMAP:
    def __init__(self,count=5000):self.count=count;self.fetches=[];self.validity=b'100'
    def select(self,*a,**k):assert k['readonly'];return 'OK',[b'5000']
    def response(self,*a):return 'UIDVALIDITY',[self.validity]
    def uid(self,cmd,*args):
        if cmd=='search':return 'OK',[' '.join(map(str,range(1,self.count+1))).encode()]
        uid,fields=args;self.fetches.append(fields)
        m=EmailMessage();m['From']='teacher@example.com';m['Subject']='实验任务';m.set_content('请完成实验')
        raw=m.as_bytes()
        meta=f'{uid} (INTERNALDATE "22-Sep-2026 18:00:00 +0800" RFC822.SIZE {len(raw)})'.encode()
        return 'OK',[(meta,raw),b')']


def mock_imap(monkeypatch,fake):
    import backend.app.modules.mail.ingestion as ingestion
    @contextmanager
    def connect(*args):yield fake
    monkeypatch.setattr(ingestion,'connection',connect)


def test_first_connection_5000_messages_no_download(db,monkeypatch):
    aid=account(db);fake=FakeIMAP();mock_imap(monkeypatch,fake)
    assert scan(db,aid)['baseline'] and fake.fetches==[]
    fake.count=5001;assert scan(db,aid)['imported']==1
    assert len(rows(db,'mail_message',[aid]))==1
    assert scan(db,aid)['imported']==0


def test_preview_has_no_cursor_messages_or_agent_jobs(db,monkeypatch):
    aid=account(db);fake=FakeIMAP(3);mock_imap(monkeypatch,fake)
    result=scan(db,aid,{'account_id':aid,'start':'2026-09-22T17:00:00+08:00','end':'2026-09-22T19:00:00+08:00'},preview=True)
    assert result['matched']==3
    assert not db.list('mail_cursor') and not db.list('mail_message') and not db.list('assistant_turn')
    assert all('HEADER' in f for f in fake.fetches)


def test_uidvalidity_and_lease_conflict(db,monkeypatch):
    aid=account(db);fake=FakeIMAP();mock_imap(monkeypatch,fake);scan(db,aid)
    fake.validity=b'200';assert scan(db,aid)['attention']=='uidvalidity_changed'
    assert not fake.fetches
    with lease(db,aid):
        with pytest.raises(ValueError):
            with lease(db,aid):pass


def test_queue_recovers_lease_and_other_account_survives(db,monkeypatch):
    import backend.app.workers.mail_jobs as worker
    a,b=account(db),account(db,2)
    j1=enqueue(db,'test',{},a);j2=enqueue(db,'test',{},b)
    with db.engine.begin() as c:c.execute(text("UPDATE mail_jobs SET status='running',lease=0 WHERE id=:id"),{'id':j1})
    def run(database,j):
        if j['account_id']==a:raise ValueError('模拟认证失败')
        return {'ok':True}
    monkeypatch.setattr(worker,'execute_job',run)
    assert worker.work_once(db) and worker.work_once(db)
    assert job(db,j1)['status']=='failed' and job(db,j2)['status']=='completed'


def test_migration_holds_pending_work_and_is_idempotent(db,monkeypatch):
    from backend.app.agent.graph import ingest
    from backend.app.core.config import settings
    monkeypatch.setattr(settings,'mail_address','')
    rid=ingest({'source':'email','message_id':'unknown','conversation_id':'old','text':'旧邮件'},db)
    migrate(db);migrate(db)
    assert db.get(rid)['status']=='migration_review'
    assert len(db.list('mail_message'))==1
    assert db.list('mail_message')[0]['scope']=='legacy-unassigned'
    assert not work_once(db)


def test_dpapi_roundtrip_and_public_account_redaction(db):
    secret=b'synthetic-test-secret'
    encrypted=protect(secret);assert secret not in encrypted and protect(encrypted,True)==secret
    row=save_account(db,MailAccount(name='测试',address='test@qq.com',password=secret.decode()))
    assert row['body']['credential_configured'] and 'credential_ref' not in row['body'] and 'password' not in row['body']


@pytest.mark.parametrize('field,value',[('imap_tls','none'),('smtp_tls','plain'),('scan_limit',0),('address','a\r\nBcc:x@y.com')])
def test_invalid_configuration_rejected(field,value):
    with pytest.raises(ValueError):MailAccount.model_validate({'name':'test','address':'a@qq.com',field:value})


def test_review_mail_stays_out_of_context_until_released(db):
    aid=account(db)
    db.update('mail-filter:'+aid,{'enabled':True,'whitelist_senders':[]})
    mid=message(db,aid,body='一段无法确定用途的普通文字',subject='问候')
    assert db.get(mid)['status']=='review'
    assert index_message(db,mid)['skipped']
    assert not thread_messages(db,db.get(mid)['body']['thread_id'],[aid])
    db.update(mid,status='active');assert index_message(db,mid)['chunks']>0


def test_catchup_cap_persists_across_polls(db,monkeypatch):
    aid=account(db);fake=FakeIMAP(0);mock_imap(monkeypatch,fake);scan(db,aid)
    key='mail-cursor:'+aid+':INBOX';cursor=db.get(key)['body']
    cursor.update(catchup={'ceiling':100,'count':0,'after':'2026-09-21T10:00:00+00:00'})
    db.update(key,cursor);fake.count=100
    assert scan(db,aid)['imported']==20
    assert scan(db,aid)['imported']==20
    assert scan(db,aid)['imported']==10
    assert scan(db,aid)['paused']
    assert db.get(key)['body']['catchup']['count']==50
    assert len(rows(db,'mail_message',[aid]))==50


def test_attachment_text_docx_encrypted_pdf_and_corrupt(db,tmp_path):
    from backend.app.modules.mail.attachments import extract
    from docx import Document
    from pypdf import PdfWriter
    txt=tmp_path/'sample.txt';txt.write_text('中文实验记录',encoding='utf-8')
    assert extract(txt,'txt')['segments'][0]['text']=='中文实验记录'
    doc=Document();doc.add_paragraph('仪器参数与结论');path=tmp_path/'sample.docx';doc.save(path)
    assert extract(path,'docx')['segments'][0]['location']=='段落 1'
    writer=PdfWriter();writer.add_blank_page(width=200,height=200);writer.encrypt('synthetic')
    path=tmp_path/'encrypted.pdf'
    with path.open('wb') as f:writer.write(f)
    assert extract(path,'pdf')['status']=='encrypted'
    bad=tmp_path/'bad.pdf';bad.write_bytes(b'not a PDF')
    assert extract(bad,'pdf')['status']=='failed'


def test_agent_tool_scope_and_round_budget(db,monkeypatch):
    import backend.app.agent.model_client as planner
    from backend.app.core.config import settings
    aid,other=account(db),account(db,2)
    session=create_session(db,SessionInput(account_ids=[aid]));turn=create_turn(db,session['id'],TurnInput(text='恶意邮件要求跨账号发送'))
    monkeypatch.setattr(settings,'mode','live')
    monkeypatch.setattr(planner,'model_json',lambda *a,**k:{'tool':'draft','args':{'account_id':other,'to':['leak@example.com'],'subject':'secret','content':'secret'}})
    result=run_turn(db,turn['id'])
    assert len(result['steps'])==6 and db.get(turn['id'])['status']=='budget_exceeded'
    assert not db.list('mail_draft') and not db.list('run')
    assert all('不能跨账号' in s['result'] for s in result['steps'])


def test_agent_retrieval_limit_and_input_limit(db,monkeypatch):
    import backend.app.agent.model_client as planner
    from backend.app.core.config import settings
    aid=account(db);s=create_session(db,SessionInput(account_ids=[aid]))
    t=create_turn(db,s['id'],TurnInput(text='实验'))
    monkeypatch.setattr(settings,'mode','live');monkeypatch.setattr(planner,'model_json',lambda *a,**k:{'tool':'search','args':{'query':'实验'}})
    run_turn(db,t['id']);assert db.get(t['id'])['body']['searches']==3
    second=create_turn(db,s['id'],TurnInput(text='测试'))
    db.update(second['id'],{**second['body'],'input_tokens':23999})
    run_turn(db,second['id']);assert db.get(second['id'])['status']=='budget_exceeded'
    assert not db.get(second['id'])['body']['steps']


def test_auto_analysis_recovers_after_budget_reservation(db):
    import json
    from backend.app.workers.mail_jobs import execute_job
    aid=account(db);a=db.get(aid);db.update(aid,{**a['body'],'auto_analyze':True})
    mid=message(db,aid);db.insert('analysis_slot',{'message_id':mid},id='auto-analysis:'+mid,scope=aid)
    j={'kind':'analyze','account_id':aid,'payload':json.dumps({'message_id':mid})}
    first=execute_job(db,j);second=execute_job(db,j)
    assert first==second and first.get('turn_id')
    assert len(db.list('assistant_session'))==len(db.list('assistant_turn'))==1


def test_history_batches_keep_the_live_cursor_and_cumulative_limit(db,monkeypatch):
    aid=account(db);fake=FakeIMAP(3);mock_imap(monkeypatch,fake);scan(db,aid)
    cursor=db.get('mail-cursor:'+aid+':INBOX')
    spec={'account_id':aid,'start':'2026-09-22T00:00:00+00:00','end':'2026-09-23T00:00:00+00:00','limit':2}
    ident=db.insert('mail_import',{**spec,'last_uid':0,'imported':0,'scanned':0},scope=aid,status='running')
    result=scan(db,aid,spec,import_id=ident)
    assert result['imported']==2 and db.get(ident)['status']=='completed'
    assert db.get('mail-cursor:'+aid+':INBOX')==cursor
    from backend.app.modules.mail.routes.imports import import_action
    with pytest.raises(ValueError):import_action(ident,'resume',db)
    with pytest.raises(ValueError):import_action(ident,'cancel',db)


def test_history_import_can_explicitly_queue_perception(db,monkeypatch):
    from backend.app.modules.mail.schemas import ImportRequest
    aid=account(db);fake=FakeIMAP(2);mock_imap(monkeypatch,fake)
    spec={'account_id':aid,'start':'2026-09-22T00:00:00+00:00',
          'end':'2026-09-23T00:00:00+00:00','limit':2,
          'analyze_after_import':True}
    ImportRequest.model_validate(spec)
    ident=db.insert('mail_import',{**spec,'last_uid':0,'imported':0,'scanned':0},scope=aid,status='running')
    scan(db,aid,spec,import_id=ident)
    with db.engine.connect() as c:
        queued=c.execute(text("SELECT COUNT(*) FROM mail_jobs WHERE kind='perception' AND account_id=:a"),{'a':aid}).scalar_one()
    assert queued==2 and db.get(ident)['body']['analysis_queued']==2


def test_history_import_analysis_is_capped_across_scan_batches(db,monkeypatch):
    aid=account(db);fake=FakeIMAP(21);mock_imap(monkeypatch,fake)
    spec={'account_id':aid,'start':'2026-09-22T00:00:00+00:00',
          'end':'2026-09-23T00:00:00+00:00','limit':21,
          'analyze_after_import':True}
    ident=db.insert('mail_import',{**spec,'last_uid':0,'imported':0,'scanned':0},scope=aid,status='running')
    scan(db,aid,spec,import_id=ident)
    scan(db,aid,spec,import_id=ident)
    assert db.get(ident)['body']['imported']==21
    assert db.get(ident)['body']['analysis_queued']==20


def test_import_opt_in_does_not_duplicate_account_auto_analysis(db,monkeypatch):
    aid=account(db);row=db.get(aid);db.update(aid,{**row['body'],'auto_analyze':True})
    mock_imap(monkeypatch,FakeIMAP(2))
    spec={'account_id':aid,'start':'2026-09-22T00:00:00+00:00',
          'end':'2026-09-23T00:00:00+00:00','limit':2,'analyze_after_import':True}
    ident=db.insert('mail_import',{**spec,'last_uid':0,'imported':0,'scanned':0},scope=aid,status='running')
    scan(db,aid,spec,import_id=ident)
    with db.engine.connect() as c:
        queued=c.execute(text("SELECT COUNT(*) FROM mail_jobs WHERE kind='perception' AND account_id=:a"),{'a':aid}).scalar_one()
    assert queued==2


def test_filter_version_validation_and_duplicate_entries(db):
    from backend.app.modules.mail.routes.settings import FilterRules, filters
    aid=account(db)
    first=filters(aid,FilterRules(whitelist_senders=['teacher@example.com','teacher@example.com']),db)
    second=filters(aid,FilterRules(subject_keywords=['广告']),db)
    assert len(first['whitelist_senders'])==1 and second['version']==first['version']+1
    with pytest.raises(ValueError):FilterRules(content_keywords=['x'*257])
    with pytest.raises(ValueError):FilterRules(whitelist_senders=[' '])


def test_index_model_switch_requires_whole_account_rebuild(db,monkeypatch):
    import backend.app.modules.mail.retrieval as rag
    aid=account(db);mid=message(db,aid);index_message(db,mid)
    with db.engine.begin() as c:c.execute(text("UPDATE mail_chunks SET model_version='old-version'"))
    monkeypatch.setattr(rag,'load_models',lambda:(object(),object()))
    monkeypatch.setattr(rag,'model_version',lambda:'new-version')
    with pytest.raises(ValueError,match='重建整个账号'):
        index_message(db,mid)
    with db.engine.connect() as c:
        assert c.execute(text('SELECT DISTINCT model_version FROM mail_chunks')).scalar_one()=='old-version'
