"""Read local download progress without network access or model loading."""
import argparse
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--model', nargs='+', choices=('Qwen3.5-9B', 'Qwen3-VL-8B-Instruct', 'Qwen3.8-27B'),
                    default=('Qwen3.5-9B', 'Qwen3-VL-8B-Instruct'))
args = parser.parse_args()
reports = []
for name in dict.fromkeys(args.model):
    manifest = root / 'models' / (name + '.manifest.json')
    if not manifest.exists():
        reports.append({'model': name, 'status': 'manifest_missing'})
        continue
    files = json.loads(manifest.read_text())['files']
    dest = root / 'models/base' / name
    total = sum(x['Size'] for x in files)
    downloaded = 0
    completed = 0
    for item in files:
        path = dest / item['Path']
        # ModelScope SDK keeps resumable files under ._____temp in local_dir.
        partials = (path.with_name(path.name + '.incomplete'), dest / '._____temp' / item['Path'])
        if path.is_file() and path.stat().st_size == item['Size']:
            completed += 1
            downloaded += item['Size']
        else:
            downloaded += min(max((p.stat().st_size for p in partials if p.is_file()), default=0), item['Size'])
    reports.append({'model': name, 'total_GB': round(total / 1e9, 3),
                    'downloaded_GB': round(downloaded / 1e9, 3),
                    'percent': round(downloaded / total * 100, 2) if total else 0,
                    'complete_files': completed, 'total_files': len(files),
                    'verification_marker_exists': (dest / 'download-verified.json').exists(),
                    'verification_note': 'marker only; --verify-only rechecks current file hashes'})
print(json.dumps(reports, ensure_ascii=False, indent=2))
