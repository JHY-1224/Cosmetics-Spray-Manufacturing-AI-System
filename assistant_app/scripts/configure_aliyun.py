"""Explicitly import only an approved connection into this app's private env."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--vision-model', required=True, help='Explicitly approved and tested cloud vision model')
    parser.add_argument('--output', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    args = parser.parse_args()
    data = json.loads(args.source.read_text())
    url = urlsplit(data['model_url'])
    if (not data.get('model_approved') or not data.get('model_external') or url.scheme != 'https'
            or url.hostname != 'dashscope.aliyuncs.com' or url.path != '/compatible-mode/v1'):
        raise SystemExit('仅导入已批准的阿里云百炼连接；不修改原配置')
    values = {'ASSISTANT_TEXT_MODE': 'approved_cloud', 'ASSISTANT_VISION_MODE': 'approved_cloud',
              'ASSISTANT_TEXT_MODEL_BASE_URL': data['model_url'],
              'ASSISTANT_VISION_MODEL_BASE_URL': data['model_url'],
              'ASSISTANT_TEXT_SERVED_MODEL': data['model_name'],
              'ASSISTANT_VISION_SERVED_MODEL': args.vision_model,
              'ASSISTANT_TEXT_API_KEY': data['model_api_key'],
              'ASSISTANT_VISION_API_KEY': data['model_api_key'],
              'ASSISTANT_MODEL_HOSTS': '127.0.0.1,localhost,::1,dashscope.aliyuncs.com'}
    if any('\n' in str(value) or '\r' in str(value) or not value for value in values.values()):
        raise SystemExit('配置值无效')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, 'w') as target:
        for key, value in values.items():
            target.write(key + '=' + str(value) + '\n')
    print('已创建助手私有连接配置（0600）；未修改原平台配置，未输出凭据。')
