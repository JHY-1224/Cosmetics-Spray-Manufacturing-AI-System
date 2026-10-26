"""Run real browser flows on a new synthetic database, keeping existing services and reports intact."""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-dir', type=Path, required=True, help='必须为新目录')
    parser.add_argument('--frontend-dir', type=Path, required=True, help='已构建的当前隔离前端')
    parser.add_argument('--browser-path', type=Path, required=True)
    parser.add_argument('--suite', choices=('reaudit', 'workflow', 'learning', 'delivery', 'model'), action='append', help='可重复指定；默认运行原四组，模型组显式指定')
    parser.add_argument('--model-config', type=Path, help='只允许model组使用；0600私有配置，最多一次合成浏览器模型调用')
    args = parser.parse_args()
    if args.model_config and args.suite != ['model']:
        parser.error('真实模型配置仅可与单独的--suite model一起使用')
    report, frontend = args.report_dir.resolve(), args.frontend_dir.resolve()
    if report.exists() or not (frontend / 'build-info.json').is_file() or not args.browser_path.is_file():
        parser.error('须提供新报告目录、含构建身份的前端及本机浏览器')
    node = shutil.which('node')
    if not node:
        parser.error('PATH缺少Node.js；请使用本机已有运行时')
    report.mkdir(parents=True, mode=0o700)
    task = Path(tempfile.mkdtemp(prefix='quality-industrial-browser-'))
    data = task / 'data'
    data.mkdir(mode=0o700)
    from quality.config import Settings
    # Reset before importing the app, whose default factory also creates local storage.
    for key in list(os.environ):
        if key.upper().startswith('QUALITY_'):
            del os.environ[key]
    for key, field in Settings.model_fields.items():
        value = field.get_default(call_default_factory=True)
        if key == 'data_dir':
            value = str(data)
        elif key == 'database_url':
            value = 'sqlite:///' + str(data / 'browser.db')
        elif key == 'mode':
            value = 'demo'
        os.environ['QUALITY_' + key.upper()] = json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)
    from quality.cli import migrate, initialize, seed_demo
    from quality.app import create_app
    from quality.security import current_time
    from fastapi.testclient import TestClient
    from tests.conftest import post
    from tests.test_industrial_supply_cost import order_fixture
    from tests.test_industrial_revalidation import supply_plan
    settings = Settings(_env_file=None)
    model_reference = None
    if args.suite and 'model' in args.suite:
        if args.model_config:
            if args.model_config.stat().st_mode & 0o777 != 0o600:
                parser.error('模型配置文件权限必须为0600')
            connection = json.loads(args.model_config.read_text())
            allowed = {'model_url', 'model_name', 'model_api_key', 'model_approved', 'model_external', 'model_timeout', 'model_max_output_tokens', 'approved_hosts'}
            if set(connection) - allowed:
                parser.error('模型配置只能含连接字段，不扩大数据许可')
        else:
            from tests.test_model_workflows import reference_model
            model_reference = reference_model()
            url, _ = model_reference.__enter__()
            connection = {'model_url': url, 'model_name': 'reference-model', 'model_approved': True}
        for key, value in connection.items():
            os.environ['QUALITY_'+key.upper()] = json.dumps(value) if isinstance(value, (list, dict, bool)) else str(value)
        settings = Settings(_env_file=None)
    migrate(settings)
    password = secrets.token_urlsafe(32)
    initialize(settings, 'admin', password)
    seed_demo(settings)
    credential = data / 'admin.json'
    credential.write_text(json.dumps({'username': 'admin', 'password': password}))
    credential.chmod(0o600)
    app = create_app(settings, frontend_dir=frontend)
    passwords = {'admin': password, **json.loads((data / 'demo-credentials.json').read_text())}
    clients = {}
    for role, value in passwords.items():
        client = TestClient(app)
        post(client, '/auth/login', {'username': role, 'password': value})
        clients[role] = client
    lines = clients['admin'].get('/api/masters?kind=line').json()
    clients, line, file, materials, stocks, _, order, _ = order_fixture((settings, app, clients, lines))
    plan = supply_plan(clients['admin'], order, stocks[:1])
    doc = {
        'line_id': line['id'], 'source_system': 'synthetic-browser', 'order_id': order['id'],
        'currency': 'CNY', 'tax_basis': 'tax_exclusive', 'tax_rate': '0.13',
        'occurred_at': current_time().isoformat(), 'evidence_ids': [file],
        'lines': [{'external_line': '1', 'order_line_id': order['lines'][0]['id'],
                   'material_id': materials[1]['id'], 'lot': stocks[1]['lot'], 'quantity': '10', 'unit': 'kg', 'unit_price': '5'}],
    }
    receipt = post(clients['admin'], '/industrial/business-documents', {**doc, 'kind': 'receipt', 'external_id': 'UI-RECEIPT'})
    invoice = post(clients['admin'], '/industrial/business-documents', {**doc, 'kind': 'invoice', 'external_id': 'UI-INVOICE'})
    usage = post(clients['admin'], '/industrial/actual-usage', {
        'line_id': line['id'], 'material_id': materials[1]['id'], 'inventory_id': stocks[1]['id'],
        'order_line_id': order['lines'][0]['id'], 'source_id': 'UI-REWORK-USAGE', 'quantity': '2', 'unit': 'kg',
        'purpose': 'rework', 'occurred_at': current_time().isoformat(), 'evidence_ids': [file],
    })
    fixture = report / 'fixture.json'
    fixture.write_text(json.dumps({'line': line['name'], 'material': materials[0]['name'], 'stock': stocks[0],
                                  'order': order['external_id'], 'order_id': order['id'], 'plan_id': plan['id'],
                                  'file_name': 'method-proof.txt', 'receipt_line': receipt['lines'][0]['id'],
                                  'invoice_line': invoice['lines'][0]['id'], 'usage_id': usage['id'],
                                  'data_nature': 'synthetic API setup only; browser actions are recorded separately'}, ensure_ascii=False, indent=2))
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
    os.environ['QUALITY_TRUSTED_ORIGINS'] = json.dumps([f'http://127.0.0.1:{port}'])
    server_script = task / 'server.py'
    server_script.write_text('''import sys,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
from quality.config import Settings
from quality.app import create_app
from quality.worker import run
import uvicorn
settings=Settings(_env_file=None)
stop=threading.Event()
worker=threading.Thread(target=run,args=(settings,stop),daemon=True);worker.start()
try:uvicorn.run(create_app(settings,frontend_dir=Path(sys.argv[2])),host='127.0.0.1',port=int(sys.argv[3]),log_level='warning')
finally:stop.set();worker.join(timeout=15)
''')
    results = []
    with (report / 'server.log').open('x') as log:
        server = subprocess.Popen([sys.executable, str(server_script), str(ROOT), str(frontend), str(port)], cwd=ROOT, env=os.environ, stdout=log, stderr=subprocess.STDOUT)
        try:
            import httpx
            with httpx.Client(trust_env=False) as http:
                for _ in range(150):
                    if server.poll() is not None:
                        raise RuntimeError('隔离浏览器服务启动失败；见server.log')
                    try:
                        if http.get(f'http://127.0.0.1:{port}/api/ready', timeout=1).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(.1)
                else:
                    raise RuntimeError('隔离浏览器服务未就绪')
            base = {**os.environ, 'QUALITY_E2E_URL': f'http://127.0.0.1:{port}',
                    'QUALITY_E2E_ADMIN': str(credential), 'QUALITY_E2E_CREDENTIALS': str(data / 'demo-credentials.json'),
                    'QUALITY_BROWSER_PATH': str(args.browser_path), 'INDUSTRIAL_BROWSER_FIXTURE': str(fixture)}
            for suite in args.suite or ('reaudit', 'workflow', 'learning', 'delivery'):
                folder = report / suite
                folder.mkdir()
                env = {**base, 'QUALITY_BROWSER_SUITE': suite, 'QUALITY_BROWSER_REPORT': str(folder / 'result.json'),
                       'QUALITY_BROWSER_OUTPUT': str(folder / 'artifacts'), 'QUALITY_E2E_SCREENSHOTS': str(folder)}
                with (folder / 'run.log').open('x') as output:
                    result = subprocess.run([node, 'node_modules/@playwright/test/cli.js', 'test'], cwd=ROOT / 'frontend', env=env, stdout=output, stderr=subprocess.STDOUT)
                results.append({'suite': suite, 'exit_code': result.returncode})
                print(json.dumps(results[-1]), flush=True)
        finally:
            server.terminate()
            try:
                server.wait(timeout=30)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()
            if model_reference:
                model_reference.__exit__(None, None, None)
    (report / 'run.json').write_text(json.dumps({'results': results, 'temporary_data': str(data),
        'original_services_changed': False, 'original_business_data_changed': False,
        'frontend': str(frontend), 'model_provider': 'live' if args.model_config else 'local_reference' if model_reference else 'disabled',
        'model': settings.model_name, 'field_acceptance': 'not_performed'}, indent=2))
    return int(any(r['exit_code'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
