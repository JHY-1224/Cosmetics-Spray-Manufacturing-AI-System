"""Foreground supervisor for the paired vLLM components; stops only its own children.

The existing assistant/FastAPI process remains the single public service. Run this
in the existing vLLM environment; run_assistant.py can use its separate web env.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, ProxyHandler


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def wait_ready(child, endpoint, model, timeout, key=''):
    """A live process is not readiness: require the exact served identity."""
    url = urlsplit(endpoint)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Paired local models require loopback HTTP readiness endpoints')
    request = Request(endpoint.rstrip('/') + '/models',
                      headers={'Authorization': 'Bearer ' + key} if key else {})
    opener = build_opener(ProxyHandler({}), NoRedirect())
    deadline = time.monotonic() + timeout
    while True:
        code = child.poll()
        if code is not None:
            raise RuntimeError(f'Model process exited before readiness: {code}')
        try:
            with opener.open(request, timeout=2) as response:
                raw = response.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError('Oversized model readiness response')
                data = json.loads(raw)
            if any(isinstance(item, dict) and item.get('id') == model for item in data.get('data', [])):
                return
        except (URLError, OSError, ValueError, TypeError, AttributeError):
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError('Model did not serve its configured identity before startup timeout')
        time.sleep(.5)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path, required=True)
    p.add_argument('--startup-timeout', type=float, default=600)
    args = p.parse_args()
    if not args.env_file.is_file():
        raise SystemExit('Missing paired service environment')
    if not 10 <= args.startup_timeout <= 1800:
        raise SystemExit('startup-timeout must be between 10 and 1800 seconds')
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    if not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise SystemExit('Set CUDA_VISIBLE_DEVICES explicitly')
    fractions = [float(os.environ.get('ASSISTANT_'+role+'_GPU_MEMORY_UTILIZATION','0')) for role in ('TEXT','VISION')]
    if not all(0<f<=.9 for f in fractions) or sum(fractions)>.9:
        raise SystemExit('Explicit GPU memory fractions must be positive and sum <= 0.9')
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from assistant_app.backend.settings import Settings
    settings = Settings()
    endpoints = [(settings.text_model_base_url, settings.text_served_model, settings.text_api_key),
                 (settings.vision_model_base_url, settings.vision_served_model, settings.vision_api_key)]
    if settings.text_mode == 'approved_cloud' or settings.vision_mode == 'approved_cloud':
        raise SystemExit('Paired GPU launch requires two local model configurations')
    for role, (endpoint, _, _) in zip(('TEXT', 'VISION'), endpoints, strict=True):
        url = urlsplit(endpoint)
        port = int(os.environ.get('ASSISTANT_'+role+'_PORT', '5101' if role == 'TEXT' else '5102'))
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost')
                or url.port != port or url.path.rstrip('/') != '/v1'):
            raise SystemExit('Readiness endpoint must match the configured local model port')
    if urlsplit(endpoints[0][0]).port == urlsplit(endpoints[1][0]).port:
        raise SystemExit('Text and vision ports must differ')
    for endpoint, _, _ in endpoints:
        with socket.socket() as check:
            check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                check.bind(('127.0.0.1', urlsplit(endpoint).port))
            except OSError:
                raise SystemExit('A configured model port is occupied; no process was stopped') from None
    children=[]
    def stop(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        # Profile and allocate one engine at a time on the shared GPU.
        for role, (endpoint, model, key) in zip(('text','vision'), endpoints, strict=True):
            child = subprocess.Popen([sys.executable,str(Path(__file__).with_name('launch_model.py')),
                role,'--env-file',str(args.env_file.resolve())],start_new_session=True)
            children.append(child)
            wait_ready(child, endpoint, model, args.startup_timeout, key.get_secret_value())
            print(role + ' model ready: ' + model, flush=True)
        for child in children:
            code = child.poll()
            if code is not None:
                raise SystemExit(code or 1)
        print('Both model identities are ready; inference and quality checks remain separate.', flush=True)
        while True:
            for child in children:
                code=child.poll()
                if code is not None: raise SystemExit(code or 1)
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
        for child in children:
            try: child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL)
                child.wait()


if __name__ == '__main__': main()
