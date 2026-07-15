from fastapi.testclient import TestClient
from app.main import AppError, app, validation, adspower_request
import httpx
import pytest


def test_validation_accepts_case_insensitive_ak_mk():
    headers = ['account_id', 'AK', 'MK', 'bank_card_tail', 'register_time', 'register_ip', 'charge_ip', 'remark']
    row = {h: 'value' for h in headers}
    assert validation(headers, [row])['valid'] is True


def test_validation_reports_missing_headers():
    result = validation(['account_id'], [{'account_id': 'a@example.com'}])
    assert result['valid'] is False
    assert 'ak' in result['missing_headers']


def test_homepage_renders():
    client = TestClient(app)
    response = client.get('/')
    assert response.status_code == 200
    assert '账号信息回填' in response.text


@pytest.mark.anyio
async def test_adspower_request_retries_rate_limit():
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        body = {'code': -1, 'msg': 'Too many request per second, please check'} if calls == 1 else {'code': 0, 'data': {'list': []}}
        return httpx.Response(200, json=body)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        result = await adspower_request(client, 'http://ads.local/api/v1/user/list', {}, 'test')
    assert calls == 2
    assert result['code'] == 0
