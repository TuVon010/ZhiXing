from fastapi.testclient import TestClient
import backend.main as main

def test_api_auth_and_pages(db,monkeypatch):
    from backend.app.api.routes import automation, system
    monkeypatch.setattr(automation,'store',db)
    monkeypatch.setattr(system,'store',db)
    with TestClient(main.app) as client:
        assert client.get('/api/todos').status_code==401
        assert client.get('/api/session').status_code==200
        assert client.get('/api/todos').status_code==200
        assert client.post('/api/memory',json={'content':'test'}).status_code==403
        headers={'X-ZhiXing-Local':'1'}
        assert client.post('/api/memory',headers={**headers,'Origin':'https://attacker.example'},json={'content':'test'}).status_code==403
        response=client.post('/api/memory',headers=headers,json={'content':'偏好中文'})
        assert response.status_code==200
        assert db.get(response.json()['id'])['status']=='candidate'
        assert client.get('/api/health',headers={'Host':'attacker.example'}).status_code==403

def test_no_secrets_in_settings(db,monkeypatch):
    from backend.app.api.routes import system
    from backend.app.core.config import settings
    monkeypatch.setattr(system,'store',db)
    monkeypatch.setattr(settings,'model_api_key','private-secret')
    with TestClient(main.app) as client:
        client.get('/api/session')
        assert 'private-secret' not in client.get('/api/settings').text
