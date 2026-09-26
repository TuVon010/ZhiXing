"""SMTP accepts immutable draft snapshots after the user confirms sending."""
from email.message import EmailMessage
import hashlib
import smtplib
from sqlalchemy import text
from backend.app.persistence.store import encode
from backend.app.modules.mail.repository import require,secret
from backend.app.core.exceptions import ExternalUnknown
from backend.app.integrations.smtp import open_smtp


def smtp_connection(db,account):
    """Bind the protocol adapter to the account's encrypted local secret."""
    return open_smtp(account, secret(db, account))


def check_draft(db,args,accounts=None,conn=None):
    row=require(db,args['draft_id'],'mail_draft',accounts,conn);b=row['body']
    if args['account_id']!=row['scope'] or args['draft_version']!=b['version'] or row['status']!='submitted':
        raise ValueError('草稿状态、账号或版本已改变，请重新确认发送')
    for key in ['account_id','to','cc','subject','content','message_id','mode','thread_id','in_reply_to','references']:
        if args.get(key)!=b.get(key):raise ValueError('发送参数不再对应已确认草稿；请编辑草稿后重新确认')
    if args.get('draft_hash')!=b.get('approved_hash'):raise ValueError('草稿内容校验失败')
    account=require(db,row['scope'],'mail_account',conn=conn)['body']
    if not account.get('test_account') and not account.get('enabled'):
        raise ValueError('发件账号已暂停')
    return row,account


def send(db,args,action_id):
    row,account=check_draft(db,args)
    if account.get('test_account'):raise ValueError('测试邮箱不可真实发信')
    msg=EmailMessage();msg['From']=account['address'];msg['To']=', '.join(args['to']);msg['Subject']=args['subject']
    if args.get('cc'):msg['Cc']=', '.join(args['cc'])
    msg['Message-ID']='<'+action_id+'@zhixing.local>'
    if args.get('in_reply_to'):msg['In-Reply-To']=args['in_reply_to']
    if args.get('references'):msg['References']=' '.join(args['references'])
    msg.set_content(args['content'])
    # Account/login errors occur before any SMTP DATA; still require a fresh review to retry.
    with smtp_connection(db,account) as smtp:
        db.update(row['id'],status='submitted')
        try:
            refused=smtp.send_message(msg)
            if refused:raise ExternalUnknown('部分收件人被拒绝，可能已向其他收件人发送，请核对')
        except (OSError,smtplib.SMTPException,ExternalUnknown) as exc:
            db.update(row['id'],status='unknown')
            raise ExternalUnknown('SMTP 结果不明，禁止自动重发，请核对') from exc
    db.update(row['id'],{**row['body'],'smtp_message_id':str(msg['Message-ID'])},'smtp_accepted')
    from backend.app.modules.mail.work_items import mark_replied
    mark_replied(db,row['body'].get('message_id'),row['id'])
    return {'message_id':str(msg['Message-ID']),'status':'smtp_accepted','delivered':False,'draft_id':row['id']}
