"""Foreground launcher; one API process, an owned SQLite and process receipt."""
import argparse
import json
import logging
import os
from pathlib import Path
import secrets
import socket
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path, default=ROOT / 'assistant_app/.env')
    args = p.parse_args()
    if args.env_file.is_file():
        from dotenv import load_dotenv
        load_dotenv(args.env_file.resolve(), override=False)
    from assistant_app.backend.settings import Settings
    from assistant_app.backend.app import create_app
    import uvicorn
    s = Settings()
    logger = logging.getLogger('coating_assistant')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            check.bind((s.host, s.port))
        except OSError as exc:
            import errno
            if exc.errno == errno.EADDRINUSE:
                raise SystemExit(f'端口 {s.port} 已被占用；未停止任何已有进程') from None
            raise SystemExit(f'无法监听端口 {s.port}（系统错误码 {exc.errno}），请核对本地执行权限') from None
    s.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    nonce = secrets.token_hex(16)
    os.environ['COATING_ASSISTANT_PROCESS_NONCE'] = nonce
    import psutil
    receipt = s.data_dir / 'process.json'
    # This file only identifies this launcher; the application lock prevents a second executor.
    state = {'pid': os.getpid(), 'create_time': psutil.Process().create_time(), 'nonce': nonce,
             'script': str(Path(__file__).resolve()), 'port': s.port}
    receipt.write_text(json.dumps(state))
    os.chmod(receipt, 0o600)
    print(f'助手网页：http://{s.host}:{s.port}；模型服务独立运行。', flush=True)
    try:
        uvicorn.run(create_app(s), host=s.host, port=s.port, workers=1, access_log=False, log_level='warning')
    finally:
        if receipt.exists() and json.loads(receipt.read_text()).get('nonce') == nonce:
            receipt.unlink()


if __name__ == '__main__':
    main()
