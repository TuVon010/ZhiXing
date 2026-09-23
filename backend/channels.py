"""SMTP helper for legacy local actions; multi-account sends use mail_send."""
import smtplib
import ssl
from email.message import EmailMessage
from .config import settings


class ExternalUnknown(Exception):
    """An external write may have happened; reconcile before retrying."""


def send_mail(args, action_id):
    if not settings.mail_address or not settings.mail_password:
        raise ValueError('请先在邮箱工作台添加发件账号')
    message=EmailMessage()
    message['From']=settings.mail_address
    message['To']=args['recipient']
    message['Subject']=args['subject']
    message['Message-ID']='<'+action_id+'@zhixing.local>'
    message.set_content(args['content'])
    with smtplib.SMTP_SSL('smtp.qq.com',465,context=ssl.create_default_context(),timeout=30) as smtp:
        smtp.login(settings.mail_address,settings.mail_password)
        try:smtp.send_message(message)
        except (OSError,smtplib.SMTPException) as exc:
            raise ExternalUnknown('SMTP 提交结果不明，请核对发件箱，禁止自动重发') from exc
    return {'message_id':str(message['Message-ID']),'delivered':False}
