from unittest.mock import MagicMock, patch

import pytest

from backend.channels import ExternalUnknown, send_mail
from backend.config import settings


def test_smtp_uncertain_submit_is_not_retried(monkeypatch):
    monkeypatch.setattr(settings, 'mail_address', 'user@qq.com')
    monkeypatch.setattr(settings, 'mail_password', 'test')
    smtp = MagicMock()
    smtp.__enter__.return_value = smtp
    smtp.send_message.side_effect = OSError('connection lost after submit')
    with patch('backend.channels.smtplib.SMTP_SSL', return_value=smtp), pytest.raises(ExternalUnknown):
        send_mail({'recipient': 'a@example.com', 'subject': 'x', 'content': 'y'}, 'action')
    assert smtp.send_message.call_count == 1
