"""Explicit live-provider check: two synthetic processes through independent human workflow roles."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-file', type=Path, required=True, help='受限本机模型JSON文件；不打印密钥')
    parser.add_argument('--report-dir', type=Path, required=True, help='新建结果目录，不覆盖历史')
    args = parser.parse_args()
    config_file, report = args.config_file.resolve(), args.report_dir.resolve()
    if report.exists() or not config_file.is_file() or stat.S_IMODE(config_file.stat().st_mode) != 0o600:
        parser.error('须提供权限0600的模型配置文件和全新报告目录')
    private = json.loads(config_file.read_text())
    allowed = {'model_url', 'model_name', 'model_api_key', 'model_approved', 'model_external', 'model_timeout', 'model_max_output_tokens', 'approved_hosts'}
    if set(private) - allowed or not private.get('model_url') or not private.get('model_approved'):
        parser.error('模型配置包含非连接字段，或未提供已批准的端点')
    report.mkdir(mode=0o700, parents=True)
    task = Path(tempfile.mkdtemp(prefix='quality-model-workflows-'))
    for key in list(os.environ):
        if key.upper().startswith('QUALITY_'):
            del os.environ[key]
    from quality.config import Settings
    values = {key: field.get_default(call_default_factory=True) for key, field in Settings.model_fields.items()}
    values.update(mode='integration', data_dir=str(task), database_url='sqlite:///'+str(task/'synthetic.db'))
    for key, value in values.items():
        os.environ['QUALITY_'+key.upper()] = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
    from quality.app import create_app
    from quality.cli import initialize, migrate, seed_demo
    from fastapi.testclient import TestClient
    from tests.conftest import post
    from tests.test_model_workflows import reviewed_model_workflow
    settings = Settings(_env_file=None)
    migrate(settings)
    password = secrets.token_urlsafe(32)
    initialize(settings, 'admin', password)
    seed_demo(settings)
    app = create_app(settings)
    credentials = {'admin': password, **json.loads((task/'demo-credentials.json').read_text())}
    clients = {}
    for role, password in credentials.items():
        client = TestClient(app)
        post(client, '/auth/login', {'username': role, 'password': password})
        clients[role] = client
    lines = clients['engineer'].get('/api/masters?kind=line').json()
    live_settings = Settings(_env_file=None, **{**values, **private})
    assert live_settings.model_allowed_data == ['process_labels', 'evidence_references']
    results = []
    for process in ('plastic_spray', 'glass_vacuum'):
        try:
            result = reviewed_model_workflow((settings, app, clients, lines), live_settings, process)
            result['status'] = 'passed'
        except Exception as exc:
            result = {'process': process, 'status': 'failed', 'error_type': type(exc).__name__}
        results.append(result)
        print(json.dumps({'process': process, 'status': result['status']}, ensure_ascii=False), flush=True)
    record = {'checked_at': datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(), 'results': results,
              'endpoint': live_settings.model_url, 'model': live_settings.model_name,
              'provider_mocked': False, 'original_services_changed': False, 'original_business_data_used': False,
              'temporary_data': str(task), 'max_provider_calls': 2, 'field_acceptance': 'not_performed'}
    serialized = json.dumps(record, ensure_ascii=False, indent=2)
    if private.get('model_api_key'):
        serialized = serialized.replace(private['model_api_key'], '[REDACTED]')
    (report/'result.json').write_text(serialized)
    return int(any(result['status'] != 'passed' for result in results))


if __name__ == '__main__':
    raise SystemExit(main())
