"""Read-only app/model environment inspection, never imports CUDA."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import socket
import errno
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from assistant_app.backend.settings import Settings
from assistant_app.backend.model_inventory import inventory


def main():
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[1] / '.env', override=False)
    s = Settings()
    packages = {}
    for name in ('fastapi', 'uvicorn', 'httpx', 'Pillow', 'torch', 'transformers', 'vllm', 'unsloth'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = 'not_installed_in_this_environment'
    result = {'python': sys.version, 'packages': packages, 'ports': {}, 'models': {}}
    for port in (s.port, 5101, 5102, 8000, 8001, 8002, 8100, 8101):
        with socket.socket() as probe:
            code = probe.connect_ex(('127.0.0.1', port))
            result['ports'][port] = ('listening' if code == 0 else 'not_listening' if code == errno.ECONNREFUSED
                                    else 'unknown_network_restricted')
    for kind, path, model_type in [('text', s.text_model_path, 'qwen3_5'), ('vision', s.vision_model_path, 'qwen3_vl')]:
        try:
            result['models'][kind] = inventory(path, model_type)
        except (OSError, ValueError, KeyError) as exc:
            result['models'][kind] = {'state': 'incomplete_or_missing', 'reason': type(exc).__name__}
    if shutil.which('nvidia-smi'):
        result['gpu'] = subprocess.run(['nvidia-smi'], capture_output=True, text=True, timeout=10).stdout
    else:
        result['gpu'] = 'not_available; no GPU validation performed'
    result['feishu'] = {'enabled': s.feishu_enabled, 'transport': s.feishu_transport}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
