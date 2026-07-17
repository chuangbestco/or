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
    assert '下载已回填文件' in response.text
    assert 'v1.2.0' in response.text
    assert '2026-07-17' in response.text


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


def test_concurrent_job_reports_stage_errors_and_continues_other_accounts(tmp_path, monkeypatch):
    async def case():
        monkeypatch.setattr(main, 'UPLOADS', tmp_path / 'uploads')
        monkeypatch.setattr(main, 'OUTPUTS', tmp_path / 'outputs')
        main.UPLOADS.mkdir()
        main.OUTPUTS.mkdir()
        token = 'concurrent-upload'
        headers = ['account_id', 'AK', 'MK', 'bank_card_tail', 'register_time', 'register_ip', 'charge_ip', 'remark']
        rows = '\n'.join([
            'first@example.com,ak-first,mk-first,1234,,,,',
            'second@example.com,ak-second,mk-second,5678,,,,',
            'third@example.com,ak-third,mk-third,9012,,,,',
        ])
        (main.UPLOADS / f'{token}.bin').write_text(','.join(headers) + '\n' + rows + '\n', encoding='utf-8')
        (main.UPLOADS / f'{token}.json').write_text('{"filename":"accounts.csv"}', encoding='utf-8')

        async def profiles(_settings):
            return [{'username': 'first@example.com', 'user_proxy_config': {'proxy_host': '1.1.1.1'}}, {'username': 'third@example.com', 'user_proxy_config': {'proxy_host': '3.3.3.3'}}]
        active = 0
        peak_active = 0
        async def keys(mk, _client=None):
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0.03)
            active -= 1
            if mk == 'mk-second': raise main.AppError('MK 无效')
            return [{'label': mk.replace('mk', 'ak'), 'created_at': '2026-07-17T00:00:00Z'}]
        async def credits(_key, _client=None): return 10.0
        monkeypatch.setattr(main, 'adspower_profiles', profiles)
        monkeypatch.setattr(main, 'key_metadata', keys)
        monkeypatch.setattr(main, 'balance', credits)
        job = {'status': 'queued', 'phase': '', 'total': 0, 'completed': 0, 'message': '', 'report': None}
        await main.run_job(job, token, {'base_url': 'http://ads.local', 'api_key': 'test'})

        assert peak_active > 1
        assert job['completed'] == 3
        assert job['status'] == 'error'
        errors = {error['account_id']: error['errors'] for error in job['report']['errors']}
        assert errors['second@example.com'] == [
            {'stage': '注册时间', 'error': 'MK 无效'},
            {'stage': '注册 IP', 'error': '未找到对应 AdsPower IP。'},
        ]
        assert job['report']['time_filled'] == 2
        assert job['report']['ip_filled'] == 2
        assert job['report']['card_normalized'] == 3
        output = list(csv.DictReader((main.OUTPUTS / 'accounts-已回填注册时间IP卡尾.csv').open(encoding='utf-8-sig')))
        assert output[0]['register_time'] == '26/07/17 08:00:00'
        assert output[1]['register_time'] == ''
        assert output[2]['register_ip'] == '3.3.3.3'
    asyncio.run(case())
