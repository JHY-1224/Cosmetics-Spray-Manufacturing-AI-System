"""Official SDK long connection -> signed local durable inbox. One consumer per app."""
import argparse
import fcntl
import hashlib
import hmac
from pathlib import Path
import secrets
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path, required=True)
    args = p.parse_args()
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    from assistant_app.backend.settings import Settings
    s = Settings()
    if not s.feishu_enabled or s.feishu_transport != 'ws':
        raise SystemExit('请先完成飞书ws模式、范围与桥接密钥配置')
    s.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (s.data_dir / 'feishu-ws.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    import httpx
    import lark_oapi as lark
    def on_message(event):
        raw = lark.JSON.marshal(event).encode('utf-8')
        timestamp, nonce = str(int(time.time())), secrets.token_hex(16)
        signature = hmac.new(s.feishu_bridge_secret.get_secret_value().encode(),
                             timestamp.encode() + nonce.encode() + raw, hashlib.sha256).hexdigest()
        # ACK only after the assistant inbox committed; failure is raised to the SDK, never ignored.
        with httpx.Client(timeout=2.5, trust_env=False, follow_redirects=False) as client:
            response = client.post(f'http://127.0.0.1:{s.port}/api/assistant/channels/feishu/events', content=raw,
                headers={'Content-Type': 'application/json', 'X-Lark-Request-Timestamp': timestamp,
                         'X-Lark-Request-Nonce': nonce, 'X-Lark-Signature': signature})
            if response.status_code != 200:
                raise RuntimeError('Local durable inbox rejected event')
    handler = lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(on_message).build()
    # ERROR level avoids logging payloads and WebSocket connection URLs.
    client = lark.ws.Client(s.feishu_app_id, s.feishu_app_secret.get_secret_value(),
                           event_handler=handler, log_level=lark.LogLevel.ERROR)
    print('飞书官方SDK长连接消费者启动；Ctrl+C仅停止此消费者。', flush=True)
    client.start()


if __name__ == '__main__':
    main()
