"""Legacy records remain readable but cannot bypass multi-mail draft checks."""
import pytest
from sqlalchemy import text
from backend.config import settings
from backend.tools import execute


def test_legacy_send_requires_account_draft_before_ledger(db,monkeypatch):
    monkeypatch.setattr(settings,'mode','live')
    action={'id':'legacy-send','tool':'send_email','args':{'recipient':'a@example.com','subject':'合成数据','content':'不可直接发送'}}
    with pytest.raises(ValueError,match='旧版直接发送已停用'):
        execute(db,action,'old-run','web:inbox')
    with db.engine.connect() as c:
        assert c.execute(text('SELECT COUNT(*) FROM ledger')).scalar_one()==0
    assert execute(db,action,'old-run','web:inbox',dry_run=True)['simulated']
