import pytest
from backend.db import Store
from backend.config import settings

@pytest.fixture(autouse=True)
def isolated_defaults(monkeypatch):
    monkeypatch.setattr(settings,'mode','demo')
    monkeypatch.setenv('ZHIXING_DISABLE_LOCAL_MODELS','1')

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'mode','demo')
    database=Store(tmp_path/'zhixing.db')
    yield database
    from backend.mail_rag import _close_vector_store
    _close_vector_store(database)
    database.engine.dispose()
