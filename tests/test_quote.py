import sys, pathlib, types, json, os
os.environ.setdefault("SECRET_KEY", "testing")
sys.path.append(str(pathlib.Path(__file__).resolve().parent.parent))

# Stub external modules not available in test environment
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

from app import quote
import pytest


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


def setup_quote(monkeypatch, tmp_path):
    quote.DB.clear()
    monkeypatch.setattr(quote, 'forward_to_openai', fake_forward)
    monkeypatch.setattr(quote, 'EXPECTED_API_KEY', None)
    quote.DEMO_USAGE.clear()
    monkeypatch.setattr(quote, 'OPENAI_ENABLED', True)
    prompt_file = tmp_path / 'prompt.py'
    prompt_file.write_text('custom_msg = "hi"')
    monkeypatch.setattr(quote, 'PROMPT_FILE', str(prompt_file))


def test_generate_quote(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    result = quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')
    assert result.quote_id == 'q_00001'
    assert result.total == 121.0
    assert result.quote_id in quote.DB


def test_patch_quote(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')
    body = quote.PatchBody(items=[quote.PatchItem(index=0, qty=2)])
    patched = quote.patch_quote('q_00001', body, x_api_key=None)
    assert patched.items[0].qty == 2
    assert patched.total == 242.0


def test_pdf_generation(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')
    res = quote.pdf('q_00001', x_api_key=None)
    assert res.media_type == 'application/pdf'
    assert res.body == b'pdf'



def test_openai_disabled(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(quote, 'OPENAI_ENABLED', False)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    with pytest.raises(quote.HTTPException) as exc:
        quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')
    assert exc.value.status_code == 503


def test_demo_rate_limit(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(quote, 'DEMO_RATE_LIMIT_SECONDS', 10)
    monkeypatch.setattr(quote, 'DEMO_DAILY_QUOTA', 5)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='demo@fixhub.es')
    with pytest.raises(quote.HTTPException) as exc:
        quote.generate(req, x_api_key=None, device_id='dev1', current_user='demo@fixhub.es')
    assert exc.value.status_code == 429


def test_demo_daily_quota(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    monkeypatch.setattr(quote, 'DEMO_RATE_LIMIT_SECONDS', 0)
    monkeypatch.setattr(quote, 'DEMO_DAILY_QUOTA', 1)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='demo@fixhub.es')
    with pytest.raises(quote.HTTPException) as exc:
        quote.generate(req, x_api_key=None, device_id='dev1', current_user='demo@fixhub.es')
    assert exc.value.status_code == 429


def test_pii_redaction(monkeypatch, tmp_path):
    quote.DB.clear()
    quote.DEMO_USAGE.clear()
    monkeypatch.setattr(quote, 'EXPECTED_API_KEY', None)
    monkeypatch.setattr(quote, 'OPENAI_ENABLED', True)
    prompt_file = tmp_path / 'prompt.py'
    prompt_file.write_text('custom_msg = "hi"')
    monkeypatch.setattr(quote, 'PROMPT_FILE', str(prompt_file))

    class DummyClient:
        class Chat:
            class Completions:
                def create(self, **kwargs):
                    data = {
                        'choices': [types.SimpleNamespace(message=types.SimpleNamespace(content=json.dumps({})))],
                    }
                    return types.SimpleNamespace(**data)
            completions = Completions()
        chat = Chat()
    monkeypatch.setattr(quote, 'client', DummyClient())

    req = quote.QuoteRequest(client=quote.Client(name='John', email='john@example.com'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')
    logged = json.loads(pathlib.Path('last_openai_message.json').read_text())
    dump = json.dumps(logged)
    assert 'john@example.com' not in dump
    assert '[REDACTED]' in dump

def test_pdf_malicious_name(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    with pytest.raises(quote.HTTPException) as exc:
        quote.pdf('../evil', x_api_key=None)
    assert exc.value.status_code == 400


def test_pdf_timeout(monkeypatch, tmp_path):
    setup_quote(monkeypatch, tmp_path)
    req = quote.QuoteRequest(client=quote.Client(name='John'), description='desc')
    quote.generate(req, x_api_key=None, device_id='dev1', current_user='user@example.com')

    def fake_timeout(func, timeout=5, max_memory=268435456):
        raise TimeoutError

    monkeypatch.setattr(quote, 'run_isolated', fake_timeout)
    with pytest.raises(quote.HTTPException) as exc:
        quote.pdf('q_00001', x_api_key=None)
    assert exc.value.status_code == 504

