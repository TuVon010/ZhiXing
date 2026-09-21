from unittest.mock import patch, MagicMock
from email.message import EmailMessage
import pytest
from backend.channels import poll_mail, Feishu, ExternalUnknown, send_mail
from backend.config import settings
from backend.runtime import ingest

def mock_imap(uids):
    imap=MagicMock()
    imap.__enter__.return_value=imap
    imap.response.return_value=('UIDVALIDITY',[b'777'])
    def call(command,*args):
        if command=='search':
            return 'OK',[b' '.join(str(u).encode() for u in uids)]
        msg=EmailMessage();msg['From']='teacher@example.com';msg['Subject']='实验';msg['Message-ID']='<'+args[0]+'@example.com>';msg.set_content('今晚整理实验结果')
        return 'OK',[(b'body',msg.as_bytes())]
    imap.uid.side_effect=call
    return imap

def test_mail_cursor_initial_skip_increment_and_import(db,monkeypatch):
    monkeypatch.setattr(settings,'mail_address','user@qq.com');monkeypatch.setattr(settings,'mail_password','test')
    receive=lambda m:ingest(m,db)
    with patch('backend.channels.imaplib.IMAP4_SSL',return_value=mock_imap([1,2])):
        poll_mail(db,receive)
    assert not db.list('message')
    with patch('backend.channels.imaplib.IMAP4_SSL',return_value=mock_imap([1,2,3])):
        poll_mail(db,receive);poll_mail(db,receive)
        assert len(db.list('message'))==1
        poll_mail(db,receive,historical=True)
    assert len(db.list('message'))==3

def test_mail_uidvalidity_change_does_not_replay(db,monkeypatch):
    monkeypatch.setattr(settings,'mail_address','user@qq.com');monkeypatch.setattr(settings,'mail_password','test')
    db.insert('channel',{'validity':'111','uid':10},id='mail-cursor')
    with patch('backend.channels.imaplib.IMAP4_SSL',return_value=mock_imap([1])):
        poll_mail(db,lambda m:ingest(m,db))
    assert not db.list('message')
    assert db.get('mail-cursor')['status']=='attention'

def test_smtp_uncertain_submit(monkeypatch):
    monkeypatch.setattr(settings,'mail_address','user@qq.com');monkeypatch.setattr(settings,'mail_password','test')
    smtp=MagicMock();smtp.__enter__.return_value=smtp;smtp.send_message.side_effect=OSError('disconnected')
    with patch('backend.channels.smtplib.SMTP_SSL',return_value=smtp),pytest.raises(ExternalUnknown):
        send_mail({'recipient':'a@example.com','subject':'x','content':'y'},'action')

def test_sync_external_conflict(db):
    ident=db.insert('todo',{'title':'task','external_id':'ext','external_snapshot':{'summary':'before'}},scope='web:inbox')
    fs=Feishu()
    with patch.object(fs,'request',return_value={'task':{'summary':'changed'}}) as request,pytest.raises(ValueError):
        fs.sync(db,'todo',ident,'action')
    assert request.call_count==1
    assert db.get(ident)['body']['sync_status']=='conflict'

def test_sync_create_and_snapshot(db):
    ident=db.insert('todo',{'title':'task'},scope='web:inbox')
    fs=Feishu()
    with patch.object(fs,'request',side_effect=[{'task':{'guid':'ext'}},{'task':{'guid':'ext','summary':'task'}}]):
        result=fs.sync(db,'todo',ident,'action')
    assert result['external_id']=='ext'
    assert db.get(ident)['body']['sync_status']=='synced'

def test_feishu_callback_rejects_other_user(db,monkeypatch):
    import lark_oapi as lark
    from types import SimpleNamespace
    from backend.channels import start_feishu
    from backend.runtime import work_once,approve
    monkeypatch.setattr(settings,'feishu_owner','ou_owner')
    ingest({'message_id':'m','text':'明天下午三点组会','timestamp':'2026-09-21T10:00:00+08:00'},db)
    work_once(db)
    a=db.list('approval')[0]
    captured={}
    class FakeClient:
        def __init__(self,*args,**kwargs):
            captured['handler']=kwargs['event_handler']
        def start(self):
            pass
    with patch.object(lark.ws,'Client',FakeClient):
        start_feishu(db,lambda m:ingest(m,db),lambda i,p:approve(i,p,db))
    callback=captured['handler']._callback_processor_map['p2.card.action.trigger']
    value={'approval_id':a['id'],'decision':'approve','version':1}
    event=SimpleNamespace(event=SimpleNamespace(operator=SimpleNamespace(open_id='ou_stranger'),action=SimpleNamespace(value=value)))
    result=callback.do(event)
    assert result.toast.type=='error'
    assert db.get(a['id'])['status']=='pending'
    event.event.operator.open_id='ou_owner'
    assert callback.do(event).toast.type=='success'
    assert db.get(a['id'])['status']=='approved'
