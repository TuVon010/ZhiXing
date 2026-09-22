import pytest
from backend.db import Store
from backend.config import settings

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'mode','demo')
    monkeypatch.setattr(settings,'jev_mode','off')
    database=Store(tmp_path/'zhixing.db')
    yield database
    database.engine.dispose()
