from fastapi.testclient import TestClient
from app.main import app, validation


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
