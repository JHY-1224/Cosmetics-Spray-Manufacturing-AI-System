"""Build an audited customer result repository with LoRA parts and compiled UI."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while b := f.read(4 * 1024 * 1024):
            h.update(b)
    return h.hexdigest()


def build(source, archives, frontend, output):
    source, archives, frontend, output = map(lambda p: Path(p).resolve(), (source, archives, frontend, output))
    if output.exists() or output == source or source.is_relative_to(output):
        raise ValueError('Output must be a new independent directory')
    subprocess.run([sys.executable, str(ROOT / 'scripts/audit_public_release.py'), str(source)], check=True)
    public = json.loads((source / 'PUBLIC_RELEASE_MANIFEST.json').read_text())
    manifest = json.loads((source / 'models/releases/qwen-light-20261028-bundled.json').read_text())
    # Verify both originals before copying any result files.
    for model in manifest['models'].values():
        p = archives / model['archive']
        if p.stat().st_size != model['archive_bytes'] or sha(p) != model['archive_sha256']:
            raise ValueError('Original adapter archive does not match frozen release')
    if not (frontend / 'index.html').is_file():
        raise ValueError('Build assistant_app/frontend before packaging')
    output.mkdir(parents=True, exist_ok=False)
    for name in [x['path'] for x in public['files']] + ['PUBLIC_RELEASE_MANIFEST.json']:
        p, target = source / name, output / name
        if p.is_symlink() or not p.is_file():
            raise ValueError('Invalid source entry')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    pretrained = output / 'pretrained'
    pretrained.mkdir()
    for model in manifest['models'].values():
        with (archives / model['archive']).open('rb') as f:
            for item in model['downloads']:
                b = f.read(item['bytes'])
                if len(b) != item['bytes'] or hashlib.sha256(b).hexdigest() != item['sha256']:
                    raise ValueError('Adapter part differs from frozen bundle manifest')
                (pretrained / item['path']).write_bytes(b)
            if f.read(1):
                raise ValueError('Unexpected archive bytes')
    for p in sorted(frontend.rglob('*')):
        if p.is_file():
            if p.is_symlink() or p.suffix not in {'.html', '.js', '.css', '.svg', '.png', '.ico', '.woff2'}:
                raise ValueError('Unexpected compiled frontend asset')
            target = output / 'assistant_app/frontend/dist' / p.relative_to(frontend)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
    (output / 'README.md').write_text((output / 'docs/GITEE_RESULT_README.md').read_text())
    # GitHub excludes deployable binaries; Gitee deliberately tracks this result.
    ignore = output / '.gitignore'
    ignore.write_text(ignore.read_text().replace('\npretrained/\n', '\n') +
                      '\n# Compiled customer UI is part of the Gitee result.\n!assistant_app/frontend/dist/\n!assistant_app/frontend/dist/**\n')
    app_ignore = output / 'assistant_app/.gitignore'
    app_ignore.write_text(app_ignore.read_text().replace('frontend/dist/\n', ''))
    public['files'] = [{'path': str(p.relative_to(output)), 'bytes': p.stat().st_size, 'sha256': sha(p)}
                       for p in sorted(output.rglob('*')) if p.is_file() and p.name != 'PUBLIC_RELEASE_MANIFEST.json']
    public.update(delivery_kind='trained_customer_result', public_source_files=len(public['files']),
                  source_bytes=sum(x['bytes'] for x in public['files']),
                  models='two original LoRA archives bundled as40MB parts; official bases external',
                  excluded=[x for x in public['excluded'] if x != 'weights and adapters'])
    (output / 'PUBLIC_RELEASE_MANIFEST.json').write_text(json.dumps(public, ensure_ascii=False, indent=2))
    subprocess.run([sys.executable, str(ROOT / 'scripts/audit_public_release.py'), str(output), '--result'], check=True)
    return {'files': len(public['files']) + 1, 'bytes': public['source_bytes'], 'lora_parts': 8}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--archives', type=Path, required=True)
    p.add_argument('--frontend', type=Path, default=ROOT / 'assistant_app/frontend/dist')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.source, args.archives, args.frontend, args.output)))


if __name__ == '__main__':
    main()
