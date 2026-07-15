import asyncio
import csv
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import main


def test_validation_accepts_case_insensitive_ak_mk():
    headers = ['account_id', 'AK', 'MK', 'bank_card_tail', 'register_time', 'register_ip', 'charge_ip', 'remark']
    assert main.validation(headers, [{h: 'value' for h in headers}])['valid'] is True


def test_validation_reports_missing_headers():
    result = main.validation(['account_id'], [{'account_id': 'a@example.com'}])
    assert result['valid'] is False
    assert 'ak' in result['missing_headers']


def test_homepage_renders():
    response = TestClient(main.app).get('/')
    assert response.status_code == 200
    assert '执行回填' in response.text
    assert '下载csv' in response.text


def test_settings_are_saved_with_owner_only_permissions(tmp_path, monkeypatch):
    config = tmp_path / 'settings.json'
    monkeypatch.setattr(main, 'CONFIG_FILE', config)
    main.save_settings(main.AdsPowerSettings(base_url='http://127.0.0.1:50325', api_key='secret'))
    assert main.read_settings()['base_url'] == 'http://127.0.0.1:50325'
    assert (config.stat().st_mode & 0o777) == 0o600


def test_adspower_request_retries_rate_limit():
    async def case():
        calls = 0
        async def handler(request):
            nonlocal calls
            calls += 1
            body = {'code': -1, 'msg': 'Too many request per second, please check'} if calls == 1 else {'code': 0, 'data': {'list': []}}
            return httpx.Response(200, json=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await main.adspower_request(client, 'http://ads.local/api/v1/user/list', {}, 'test')
        assert calls == 2
        assert result['code'] == 0
    asyncio.run(case())


def test_job_with_backfill_error_still_generates_downloadable_csv(tmp_path, monkeypatch):
    async def case():
        monkeypatch.setattr(main, 'UPLOADS', tmp_path / 'uploads')
        monkeypatch.setattr(main, 'OUTPUTS', tmp_path / 'outputs')
        main.UPLOADS.mkdir()
        main.OUTPUTS.mkdir()
        token = 'upload-token'
        headers = ['account_id', 'AK', 'MK', 'bank_card_tail', 'register_time', 'register_ip', 'charge_ip', 'remark']
        (main.UPLOADS / f'{token}.bin').write_text(','.join(headers) + '\nuser@example.com,ak,mk,1234,,,,\n', encoding='utf-8')
        (main.UPLOADS / f'{token}.json').write_text('{"filename":"accounts.csv"}', encoding='utf-8')

        async def profiles(_settings): return []
        monkeypatch.setattr(main, 'adspower_profiles', profiles)
        job = {'status': 'queued', 'phase': '', 'total': 0, 'completed': 0, 'message': '', 'report': None}
        await main.run_job(job, token, {'base_url': 'http://ads.local', 'api_key': 'test'})

        assert job['status'] == 'error'
        assert job['report']['errors'][0]['account_id'] == 'user@example.com'
        assert job['download_url'].endswith('accounts-已回填注册时间IP卡尾.csv')
        output = main.OUTPUTS / 'accounts-已回填注册时间IP卡尾.csv'
        assert output.exists()
        assert list(csv.DictReader(output.open(encoding='utf-8-sig'))) [0]['account_id'] == 'user@example.com'
    asyncio.run(case())
