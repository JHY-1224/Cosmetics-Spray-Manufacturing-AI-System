"""Download official weights with the ModelScope SDK; never upload local data."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODELS = ('Qwen3.5-9B', 'Qwen3-VL-8B-Instruct')
MODELS = (*DEFAULT_MODELS, 'Qwen3.8-27B')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', nargs='+', choices=MODELS, default=DEFAULT_MODELS)
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--model-root', type=Path, default=ROOT / 'models/base',
                        help='Existing cloud models directory; verification never downloads')
    args = parser.parse_args()
    os.environ['MODELSCOPE_CACHE'] = str(ROOT / 'models/.cache')
    os.environ['MODELSCOPE_ENABLE_DEFAULT_HASH_VALIDATION'] = 'true'
    for name in dict.fromkeys(args.model):
        download_model(name, args.verify_only, args.model_root)


def validate_manifest(manifest, name):
    if manifest.get('model_id') != 'Qwen/' + name or not manifest.get('files'):
        raise ValueError('Unexpected or empty official manifest')
    seen = set()
    for item in manifest['files']:
        path = Path(item['Path'])
        if path.is_absolute() or '..' in path.parts or str(path) in seen:
            raise ValueError('Unsafe or duplicate manifest path')
        seen.add(str(path))
        if not isinstance(item['Size'], int) or item['Size'] < 0:
            raise ValueError('Invalid file size')
        if len(item['Sha256']) != 64 or any(c not in '0123456789abcdef' for c in item['Sha256']):
            raise ValueError('Invalid official SHA256')
        if not item.get('Revision'):
            raise ValueError('Missing immutable revision')


def download_model(name, verify_only, model_root=None):
    dest = (model_root or ROOT / 'models/base').expanduser().resolve() / name
    manifest_path = ROOT / 'models' / (name + '.manifest.json')
    if not manifest_path.exists():
        if verify_only:
            raise SystemExit('Missing download manifest')
        import requests
        response = requests.get(
            f'https://modelscope.cn/api/v1/models/Qwen/{name}/repo/files',
            params={'Recursive': 'true', 'Revision': 'master'}, timeout=60)
        response.raise_for_status()
        payload = response.json()
        if payload['Code'] != 200:
            raise RuntimeError('ModelScope did not return a file list')
        files = [f for f in payload['Data']['Files'] if f['Type'] == 'blob']
        manifest = {'model_id': 'Qwen/' + name, 'files': files}
        validate_manifest(manifest, name)
        with manifest_path.open('x') as stream:
            json.dump(manifest, stream, indent=2)
    manifest = json.loads(manifest_path.read_text())
    validate_manifest(manifest, name)
    print('Model:', name, 'GB:', round(sum(f['Size'] for f in manifest['files']) / 1e9, 3), flush=True)
    if not verify_only:
        from modelscope import model_file_download, snapshot_download
        # Small configs/tokenizer first. Per-file immutable revisions avoid mixed snapshots.
        for item in sorted((f for f in manifest['files'] if f['Size'] <= 100_000_000), key=lambda f: f['Path']):
            print('Downloading:', item['Path'], flush=True)
            model_file_download(model_id=manifest['model_id'], file_path=item['Path'],
                                revision=item['Revision'], local_dir=str(dest))
        revisions = {f['Revision'] for f in manifest['files'] if f['Size'] > 100_000_000}
        for revision in sorted(revisions):
            paths = [f['Path'] for f in manifest['files']
                     if f['Size'] > 100_000_000 and f['Revision'] == revision]
            print('Downloading weight shards with 4 SDK workers:', len(paths), flush=True)
            snapshot_download(model_id=manifest['model_id'], revision=revision,
                              allow_patterns=paths, local_dir=str(dest), max_workers=4)
    result = []
    for item in manifest['files']:
        file = dest / item['Path']
        if not file.resolve().is_relative_to(dest.resolve()):
            raise RuntimeError('Model file escapes destination')
        if not file.is_file() or file.stat().st_size != item['Size']:
            raise RuntimeError(f'Missing/incomplete file: {file}')
        digest = hashlib.sha256()
        with file.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        if digest.hexdigest() != item['Sha256']:
            raise RuntimeError(f'SHA256 mismatch: {file}')
        result.append({'path': item['Path'], 'bytes': item['Size'], 'sha256': digest.hexdigest()})
    report = {'status': 'verified', 'model_id': manifest['model_id'], 'files': result,
              'total_bytes': sum(x['bytes'] for x in result)}
    (dest / 'download-verified.json').write_text(json.dumps(report, indent=2))
    print('All official files verified:', report['total_bytes'], flush=True)


if __name__ == '__main__':
    lock_path = ROOT / 'models/.download.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another download/verification is already running')
        main()
