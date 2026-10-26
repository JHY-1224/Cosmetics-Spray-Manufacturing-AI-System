"""Manual smoke check; synthetic prompt, never reads held-out datasets."""
import argparse
import base64
import json
from pathlib import Path
import urllib.request


def build_body(model, prompt, image=None):
    content = prompt
    if image is not None:
        mime = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
        if image.suffix.lower() not in mime or not 0 < image.stat().st_size <= 10 * 1024 * 1024:
            raise ValueError('Image must be PNG/JPEG/WebP, nonempty and at most 10 MiB')
        data = base64.b64encode(image.read_bytes()).decode('ascii')
        content = [{'type': 'text', 'text': prompt},
                   {'type': 'image_url', 'image_url': {'url': f'data:{mime[image.suffix.lower()]};base64,{data}'}}]
    return {'model': model, 'temperature': 0, 'max_tokens': 256,
            'messages': [{'role': 'user', 'content': content}]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8100/v1/chat/completions')
    parser.add_argument('--model', default='cosmetics-qwen35-9b')
    parser.add_argument('--image', type=Path, help='Explicit local test image sent to the selected endpoint')
    parser.add_argument('--prompt', default='只有材料名称，没有配方、工艺条件和检测记录，能否直接确认包材与内容物相容？请说明需要补充什么依据。')
    args = parser.parse_args()
    body = build_body(args.model, args.prompt, args.image)
    req = urllib.request.Request(args.url, json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=120) as response:
        print(json.dumps(json.load(response), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
