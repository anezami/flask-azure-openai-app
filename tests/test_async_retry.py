import os
import json
import time
import threading
import types
import pytest
from app import app as flask_app

# We'll monkeypatch call_chat_completion to simulate retries and success.

class SimulatedError(Exception):
    def __init__(self, status_code, message="simulated"):
        super().__init__(message)
        self.status_code = status_code

responses_plan = []  # filled per test with either 'error:<code>' or 'ok'


def fake_call_chat_completion(system_prompt, user_content, deployment_name, temperature, max_output_tokens):
    if not responses_plan:
        return "OK(DEFAULT)"
    step = responses_plan.pop(0)
    if step.startswith('error:'):
        code = int(step.split(':')[1])
        raise SimulatedError(code, f"forced {code}")
    return f"RESULT-{step}"

@pytest.fixture(autouse=True)
def patch_client(monkeypatch):
    from importlib import import_module
    mod = import_module('azure_openai_client')
    monkeypatch.setattr(mod, 'call_chat_completion', fake_call_chat_completion)
    yield

@pytest.fixture
def client():
    flask_app.config['TESTING'] = True
    with flask_app.test_client() as c:
        yield c


def test_async_job_retry_and_success(client, monkeypatch):
    # Plan: first chunk errors 429 twice then succeeds
    global responses_plan
    responses_plan = ['error:429', 'error:429', 'ok']
    monkeypatch.setenv('RETRY_MAX_ATTEMPTS', '4')
    monkeypatch.setenv('MAX_PARALLEL_REQUESTS', '1')
    monkeypatch.setenv('MAX_INPUT_TOKENS', '10')  # force single chunk

    rv = client.post('/process', data={'text': 'Hello world', 'mode':'grammar'})
    assert rv.status_code == 200
    # Extract job id from response HTML
    html = rv.data.decode('utf-8')
    import re
    m = re.search(r'data-job-id="([A-Za-z0-9_-]+)"', html)
    assert m, 'job id not found in html'
    job_id = m.group(1)

    # Poll status endpoint until finished
    for _ in range(30):
        st = client.get(f'/job/{job_id}/status')
        data = st.get_json()
        if data['status'] in ('succeeded','failed'):
            break
        time.sleep(0.2)
    else:
        pytest.fail('Job did not finish in time')

    assert data['status'] == 'succeeded'
    assert data['chunks_completed'] == 1
    # attempts should reflect retries (>=3 total attempts)
    # metrics captured in status are not exported directly; further inspection could read metrics.log if needed


def test_async_job_circuit_breaker(client, monkeypatch):
    global responses_plan
    # Force consecutive failures beyond threshold
    responses_plan = ['error:500', 'error:500', 'error:500']
    monkeypatch.setenv('RETRY_MAX_ATTEMPTS', '1')  # no internal retry per chunk
    monkeypatch.setenv('CIRCUIT_BREAKER_FAILURE_THRESHOLD', '2')
    monkeypatch.setenv('MAX_PARALLEL_REQUESTS', '2')
    monkeypatch.setenv('MAX_INPUT_TOKENS', '4')  # create multiple chunks

    rv = client.post('/process', data={'text': 'abcdefghi', 'mode':'grammar'})
    assert rv.status_code == 200
    html = rv.data.decode('utf-8')
    import re
    m = re.search(r'data-job-id="([A-Za-z0-9_-]+)"', html)
    assert m, 'job id not found in html'
    job_id = m.group(1)

    for _ in range(40):
        st = client.get(f'/job/{job_id}/status')
        data = st.get_json()
        if data['status'] in ('succeeded','failed'):
            break
        time.sleep(0.25)
    else:
        pytest.fail('Job did not finish in time')

    assert data['status'] == 'failed'
    assert data['chunks_failed'] >= 1
