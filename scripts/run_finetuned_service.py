"""Customer result entry: verify bundled LoRA, prepare private paths, start paired service.

Never trains, downloads bases, installs packages, overwrites originals or stops
unrelated processes. Web is started only after both actual LoRA identities and
generation checks succeed. GPU startup is explicit; --prepare-only stays offline.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.restore_finetuned_release import restore, digest, verify, basename

MANIFEST = ROOT / 'models/releases/qwen-light-20261028-bundled.json'


def write_new(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)


def prepare_artifacts(manifest, archives, runtime, model_root):
    """Preserve published bytes; rebase only deployment copies of adapter_config."""
    runtime, model_root = Path(runtime).resolve(), Path(model_root).resolve()
    restored = runtime / 'original'
    marker = runtime / 'artifacts.json'
    identity = {'manifest_sha256': hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
                'model_root': str(model_root)}
    if marker.exists():
        prior = json.loads(marker.read_text())
        if prior['identity'] != identity:
            raise ValueError('运行目录属于另一份配置，请选择新的 --runtime-dir，原目录不覆盖')
        for item in prior['files']:
            verify(runtime / item['path'], item)
        return {k: Path(v) for k, v in prior['adapters'].items()}
    if restored.exists() or (runtime / 'artifacts').exists():
        raise ValueError('上次准备未完成，请保留现场并指定新的 --runtime-dir')
    restore(manifest, archives, restored)
    adapters = {}
    for kind, model in manifest['models'].items():
        destination = runtime / 'artifacts' / kind / basename(model['run_id']) / 'adapter'
        shutil.copytree(restored / kind, destination)
        base = model_root / model['base_model_id'].split('/')[1]
        config_path = destination / 'adapter_config.json'
        config = json.loads(config_path.read_text())
        if config['base_model_name_or_path'] != model['lineage']['model_dir']:
            raise ValueError('原始Adapter基座谱系不匹配')
        config['base_model_name_or_path'] = str(base)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2))
        lineage = dict(model['lineage'], model_dir=str(base),
                       original_model_dir=model['lineage']['model_dir'], deployment_copy=True)
        write_new(destination.parent / 'run-manifest.json', lineage)
        adapters[kind] = destination
    files = []
    for folder in (restored, runtime / 'artifacts'):
        for path in sorted(folder.rglob('*')):
            if path.is_file():
                files.append({'path': str(path.relative_to(runtime)), 'bytes': path.stat().st_size,
                              'sha256': digest(path)})
    write_new(marker, {'identity': identity, 'adapters': {k: str(v) for k, v in adapters.items()}, 'files': files})
    return adapters


def verify_bases(model_root, runtime, python):
    signature = {}
    for name in ('Qwen3.5-9B', 'Qwen3-VL-8B-Instruct'):
        manifest_path = ROOT / 'models' / (name + '.manifest.json')
        spec = json.loads(manifest_path.read_text())
        signature[name] = {'manifest_sha256': digest(manifest_path), 'path': str(model_root / name), 'files': {}}
        for item in spec['files']:
            p = model_root / name / item['Path']
            if not p.resolve().is_relative_to((model_root / name).resolve()) or not p.is_file():
                raise ValueError('基座缺文件：' + str(p))
            signature[name]['files'][item['Path']] = [p.stat().st_size, p.stat().st_mtime_ns]
    cache = runtime / 'base-verification.json'
    if cache.exists() and json.loads(cache.read_text()) == signature:
        return
    subprocess.run([str(python), str(ROOT / 'day1/0-download-model/1-download.py'),
                    '--verify-only', '--model-root', str(model_root)], check=True)
    cache.write_text(json.dumps(signature, indent=2))


def validate_model_card(cards, alias, adapter):
    found = [x for x in cards.get('data', []) if x.get('id') == alias]
    if len(found) != 1 or found[0].get('parent') != alias + '-base' or found[0].get('root') != str(adapter):
        raise ValueError('服务端未证明指定LoRA身份与路径：' + alias)
    return found[0]


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request_json(url, payload=None, timeout=10):
    request = Request(url, data=json.dumps(payload).encode() if payload else None,
                      headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=timeout) as response:
        blob = response.read(256 * 1024 + 1)
        if len(blob) > 256 * 1024:
            raise ValueError('启动检查响应过大')
        return json.loads(blob)


def probe_lora(endpoint, alias, adapter, kind):
    card = validate_model_card(request_json(endpoint + '/models'), alias, adapter)
    prompt = '这是启动连通检查，请简短回答已收到。不判断品质或放行。'
    content = prompt
    if kind == 'vision':
        # A synthetic1x1 PNG; tests image transport/activation, not visual accuracy.
        png = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='
        content = [{'type': 'text', 'text': prompt},
                   {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + png}}]
    response = request_json(endpoint + '/chat/completions',
                            {'model': alias, 'messages': [{'role': 'user', 'content': content}],
                             'temperature': 0, 'max_tokens': 128,
                             'chat_template_kwargs': {'enable_thinking': False}}, timeout=180)
    choice = response['choices'][0]
    message = choice['message']
    if (response.get('model') != alias or choice.get('finish_reason') != 'stop'
            or not isinstance(message.get('content'), str) or not message['content'].strip()
            or message.get('tool_calls') or message.get('function_call')):
        raise ValueError('LoRA启动生成检查未完整通过：' + alias)
    return {'model_card': card, 'finish_reason': 'stop', 'response_model': response['model'],
            'scope': 'actual startup generation, not content/field acceptance'}


def available_ports(ports):
    if len(set(ports)) != len(ports):
        raise ValueError('三个端口必须互不相同')
    for port in ports:
        if not 1024 <= port <= 65535:
            raise ValueError('端口范围须1024—65535')
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError:
                raise ValueError(f'端口{port}已被占用，不会结束已有服务') from None


def env_for(args, runtime, adapters, run):
    values = {'HOST': '127.0.0.1', 'PORT': args.web_port, 'PUBLIC_ORIGIN': f'http://127.0.0.1:{args.web_port}',
              'DATA_DIR': str(runtime / 'jobs'), 'MODEL_DIRECTORY': str(args.model_root),
              'MODEL_PROFILES_DIR': str(runtime / 'profiles'), 'ALLOW_BASE_PREVIEW': 'false',
              'MODEL_TIMEOUT': '180', 'REQUEST_TIMEOUT': '420', 'VLLM_LORA_VALIDATED': '1'}
    for kind, port, memory in [('text', args.text_port, args.text_memory), ('vision', args.vision_port, args.vision_memory)]:
        prefix = kind.upper() + '_'
        values.update({prefix + 'MODE': 'finetuned_lora', prefix + 'MODEL_BASE_URL': f'http://127.0.0.1:{port}/v1',
                       prefix + 'SERVED_MODEL': 'qwen-light-' + kind, prefix + 'MODEL_PATH': str(args.model_root / ('Qwen3.5-9B' if kind == 'text' else 'Qwen3-VL-8B-Instruct')),
                       prefix + 'ADAPTER_PATH': str(adapters[kind]), prefix + 'PORT': port,
                       prefix + 'GPU_MEMORY_UTILIZATION': memory, prefix + 'MAX_MODEL_LEN': args.context})
    values['VISION_QUANTIZATION'] = 'bitsandbytes'
    values['MODEL_VERIFICATION_FILE'] = str(run / 'text-verification.json')
    values['VISION_VERIFICATION_FILE'] = str(run / 'vision-verification.json')
    # This flag authorizes a startup attempt of the published LoRA combination.
    # No Web request is accepted until real LoRA cards/generation/receipts pass.
    return {f'ASSISTANT_{k}': str(v) for k, v in values.items()}


def serve(args, manifest, runtime, adapters):
    if sys.platform != 'linux':
        raise ValueError('模型服务入口需要Linux NVIDIA服务器；离线准备可用 --prepare-only')
    if not (ROOT / 'assistant_app/frontend/dist/index.html').is_file():
        raise ValueError('缺少网页成品，请使用Gitee训练结果版，或先构建assistant_app/frontend')
    available_ports([args.web_port, args.text_port, args.vision_port])
    clean = {k: v for k, v in os.environ.items() if not k.startswith('ASSISTANT_')}
    clean['CUDA_VISIBLE_DEVICES'] = args.gpu
    code = "import importlib.metadata,json,torch; assert torch.cuda.is_available(); print(json.dumps({'gpu':torch.cuda.get_device_name(0),'versions':{n:importlib.metadata.version(n) for n in ['vllm','torch','transformers','bitsandbytes']}}))"
    gpu = json.loads(subprocess.check_output([str(args.inference_python), '-c', code], env=clean, text=True))
    for name, version in manifest['environments']['serving']['packages'].items():
        if name in gpu['versions'] and gpu['versions'][name] != version:
            raise ValueError(f'推理环境{name}={gpu["versions"][name]}，发布版要求{version}；先按README准备环境')
    run = runtime / 'runs' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    run.mkdir(parents=True, exist_ok=False)
    values = env_for(args, runtime, adapters, run)
    env_file = run / 'service.env'
    with env_file.open('x') as f:
        for key, value in values.items():
            f.write(key + '=' + json.dumps(value) + '\n')
    env_file.chmod(0o600)
    env = dict(clean, **values)
    children, logs = [], []
    def stopped(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, stopped)
    try:
        log = (run / 'models.log').open('xb'); logs.append(log)
        models = subprocess.Popen([str(args.inference_python), str(ROOT / 'assistant_app/scripts/launch_qwen_light.py'),
                                   '--env-file', str(env_file)], cwd=ROOT, env=env,
                                  stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(models)
        for kind, port in [('text', args.text_port), ('vision', args.vision_port)]:
            alias = 'qwen-light-' + kind
            endpoint = f'http://127.0.0.1:{port}/v1'
            deadline = time.monotonic() + 1200
            while True:
                if models.poll() is not None:
                    raise RuntimeError('模型启动失败；查看' + str(run / 'models.log'))
                try:
                    validate_model_card(request_json(endpoint + '/models'), alias, adapters[kind])
                    break
                except (URLError, OSError):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('等待模型就绪超时') from None
                    time.sleep(.5)
            evidence = dict(probe_lora(endpoint, alias, adapters[kind], kind), gpu=gpu['gpu'],
                            active_adapters=[alias], versions=gpu['versions'])
            evidence_file = run / (kind + '-runtime.json')
            write_new(evidence_file, evidence)
            subprocess.run([str(args.web_python), str(ROOT / 'assistant_app/scripts/check_models.py'),
                            '--env-file', str(env_file), '--hash', '--kind', kind,
                            '--runtime-evidence', str(evidence_file), '--output', str(run / (kind + '-verification.json'))],
                           cwd=ROOT, env=env, check=True)
        log = (run / 'web.log').open('xb'); logs.append(log)
        web = subprocess.Popen([str(args.web_python), str(ROOT / 'assistant_app/scripts/run_assistant.py'),
                                '--env-file', str(env_file)], cwd=ROOT, env=env,
                               stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        children.append(web)
        deadline = time.monotonic() + 60
        while True:
            if any(p.poll() is not None for p in children):
                raise RuntimeError('服务退出，请查看' + str(run))
            try:
                ready = request_json(f'http://127.0.0.1:{args.web_port}/health/ready')
                if not ready['text']['available'] or not ready['vision']['available']:
                    raise ValueError('微调产物未通过Web就绪核验')
                break
            except (URLError, OSError):
                if time.monotonic() >= deadline:
                    raise TimeoutError('网页启动超时') from None
                time.sleep(.5)
        print(f'服务已启动：http://127.0.0.1:{args.web_port}；Ctrl+C正常停止本次服务。日志：{run}', flush=True)
        while all(p.poll() is None for p in children):
            time.sleep(.5)
        raise RuntimeError('一个子服务已退出，正在停止本次其他子服务')
    except KeyboardInterrupt:
        pass
    finally:
        for child in reversed(children):
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
        for child in reversed(children):
            try:
                child.wait(timeout=60)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        for log in logs:
            log.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-root', type=Path, required=True)
    p.add_argument('--runtime-dir', type=Path, default=ROOT / '.runtime-finetuned')
    p.add_argument('--archive-dir', type=Path, default=ROOT / 'pretrained')
    p.add_argument('--inference-python', type=Path, default=ROOT / 'day2/5-service/.venv/bin/python')
    p.add_argument('--web-python', type=Path, default=ROOT / 'assistant_app/.venv/bin/python')
    p.add_argument('--gpu', default='0')
    p.add_argument('--text-memory', type=float, default=.55)
    p.add_argument('--vision-memory', type=float, default=.35)
    p.add_argument('--context', type=int, default=8192)
    p.add_argument('--web-port', type=int, default=5001)
    p.add_argument('--text-port', type=int, default=5101)
    p.add_argument('--vision-port', type=int, default=5102)
    p.add_argument('--prepare-only', action='store_true')
    args = p.parse_args()
    if (not 0 < args.text_memory <= .9 or not 0 < args.vision_memory <= .9
            or args.text_memory + args.vision_memory > .900001 or not 256 <= args.context <= 8192):
        p.error('显存比例须正数且合计≤0.9；上下文须256—8192')
    args.model_root = args.model_root.expanduser().resolve()
    args.inference_python = args.inference_python.expanduser().absolute()
    args.web_python = args.web_python.expanduser().absolute()
    if not args.web_python.is_file():
        p.error('缺少Web环境Python，请按README准备，或传 --web-python 指向现有环境')
    if not args.prepare_only and not args.inference_python.is_file():
        p.error('缺少推理环境Python，请按README准备，或传 --inference-python 指向现有环境')
    manifest = json.loads(MANIFEST.read_text())
    runtime = args.runtime_dir.expanduser().resolve()
    if not args.archive_dir.is_dir():
        p.error('缺少随项目提供的pretrained参数目录，请下载Gitee训练结果版')
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / 'launcher.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            p.error('此运行目录已有启动程序，不重复执行')
        verify_bases(args.model_root, runtime, args.web_python)
        adapters = prepare_artifacts(manifest, args.archive_dir, runtime, args.model_root)
        if args.prepare_only:
            print(json.dumps({'status': 'prepared_not_started', 'adapters': {k: str(v) for k, v in adapters.items()}}))
            return
        serve(args, manifest, runtime, adapters)


if __name__ == '__main__':
    main()
