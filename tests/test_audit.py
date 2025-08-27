import sys, pathlib, types, json
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

# Stub external modules
class DummyOpenAI:
    def __init__(self, *args, **kwargs):
        pass

openai_stub = types.SimpleNamespace(OpenAI=DummyOpenAI)
sys.modules.setdefault('openai', openai_stub)

class DummyTemplate:
    def __init__(self, text):
        self.text = text
    def render(self, **kwargs):
        return self.text

class DummyEnvironment:
    def __init__(self, loader=None):
        pass
    def from_string(self, text):
        return DummyTemplate(text)

class DummyBaseLoader:
    pass

sys.modules.setdefault('jinja2', types.SimpleNamespace(Environment=DummyEnvironment, BaseLoader=DummyBaseLoader))

class DummyHTML:
    def __init__(self, string):
        self.string = string
    def write_pdf(self):
        return b'pdf'

sys.modules.setdefault('weasyprint', types.SimpleNamespace(HTML=DummyHTML))

import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta
from importlib import reload

from app import database, quote
import app.main as main_module


def fake_forward(custom_message, payload, documents=None, response_format=None):
    data = {
        'items': [{
            'concept': 'Service',
            'qty': 1,
            'unit': 'u',
            'unit_price': 100,
            'subtotal': 100
        }],
        'tax_rate': 21,
        'currency': 'EUR',
        'terms': 'pay soon',
        'note': 'thanks'
    }
    return types.SimpleNamespace(
        choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps(data)))]
    )


def setup_app(monkeypatch, tmp_path):
    monkeypatch.setattr(database, 'DB_PATH', tmp_path / 'audit.db')
    database.create_tables()

    reload(quote)
    quote.DB.clear()
    monkeypatch.setattr(quote, 'forward_to_openai', fake_forward)
    monkeypatch.setattr(quote, 'EXPECTED_API_KEY', None)
    prompt_file = tmp_path / 'prompt.py'
    prompt_file.write_text('custom_msg = "hi"')
    monkeypatch.setattr(quote, 'PROMPT_FILE', str(prompt_file))

    reload(main_module)
    return main_module.app


def auth_headers(client: TestClient):
    res = client.post('/api/auth/login', json={'email': 'demo@fixhub.es', 'password': 'demo123!'})
    token = res.json()['token']
    return {
        'Authorization': f'Bearer {token}',
        'X-Device-Id': 'dev1',
        'User-Agent': 'pytest'
    }


def test_audit_logging(monkeypatch, tmp_path):
    app = setup_app(monkeypatch, tmp_path)
    client = TestClient(app)
    headers = auth_headers(client)

    client.get('/secure-data', headers=headers)
    qreq = {'client': {'name': 'John', 'email': 'john@example.com'}, 'description': 'desc'}
    res = client.post('/api/quotes/generate', json=qreq, headers=headers)
    assert res.status_code == 200
    quote_id = res.json()['quote_id']
    res = client.patch(f'/api/quotes/{quote_id}', json={'tax_rate': 10}, headers=headers)
    assert res.status_code == 200

    start = (datetime.utcnow() - timedelta(minutes=1)).isoformat()
    end = (datetime.utcnow() + timedelta(minutes=1)).isoformat()
    res = client.get('/audit/logs', headers=headers, params={'user': 'demo@fixhub.es', 'start': start, 'end': end})
    logs = res.json()
    actions = {l['action'] for l in logs}
    assert {'access', 'create', 'update'} <= actions
    create_log = next(l for l in logs if l['action'] == 'create')
    assert '[REDACTED]' in create_log['after']
    assert 'john@example.com' not in create_log['after']

    res = client.get('/audit/logs/export', headers=headers)
    assert res.status_code == 200
    assert res.headers['content-type'] == 'text/csv'
    assert 'actor' in res.text.splitlines()[0]
