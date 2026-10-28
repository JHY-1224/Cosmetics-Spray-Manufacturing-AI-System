"""Verify and restore published LoRA files into a new directory; never run models."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import tempfile


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        while block := f.read(4 * 1024 * 1024):
            h.update(block)
    return h.hexdigest()


def basename(value):
    if not isinstance(value, str) or not value or PurePosixPath(value).name != value or '\\' in value or value in {'.', '..'}:
        raise ValueError('Invalid artifact filename')
    return value


def verify(path, item):
    if path.is_symlink() or not path.is_file() or path.stat().st_size != item['bytes'] or digest(path) != item['sha256']:
        raise ValueError('Artifact integrity failure: ' + path.name)


def restore(manifest, archive_dir, output):
    archive_dir = Path(archive_dir).resolve()
    output = Path(output).absolute()
    if output.exists() or output.is_symlink():
        raise ValueError('Output must be a new directory; existing files are never overwritten')
    if set(manifest['models']) != {'text', 'vision'}:
        raise ValueError('Expected text and vision components')
    # Check all downloads before creating the destination.
    for model in manifest['models'].values():
        for item in model['downloads']:
            verify(archive_dir / basename(item['path']), item)
    with tempfile.TemporaryDirectory(prefix='qwen-release-') as temporary:
        archives = {}
        for kind, model in manifest['models'].items():
            archive = Path(temporary) / (kind + '.tar.gz')
            with archive.open('xb') as target:
                for item in model['downloads']:
                    with (archive_dir / item['path']).open('rb') as source:
                        shutil.copyfileobj(source, target)
            verify(archive, {'bytes': model['archive_bytes'], 'sha256': model['archive_sha256']})
            allowed = {kind + '/' + basename(item['path']): item for item in model['files']}
            if len(allowed) != len(model['files']):
                raise ValueError('Duplicate file in manifest')
            with tarfile.open(archive, 'r:gz') as tf:
                members = tf.getmembers()
                if len(members) != len(allowed) or {m.name for m in members} != set(allowed):
                    raise ValueError('Unexpected or duplicate archive member')
                if any(not m.isfile() or m.size != allowed[m.name]['bytes'] for m in members):
                    raise ValueError('Non-regular or oversized archive member')
            archives[kind] = archive
        output.mkdir(parents=True, exist_ok=False)
        for kind, archive in archives.items():
            model = manifest['models'][kind]
            by_name = {kind + '/' + item['path']: item for item in model['files']}
            (output / kind).mkdir()
            with tarfile.open(archive, 'r:gz') as tf:
                for member in tf.getmembers():
                    target = output / member.name
                    with tf.extractfile(member) as source, target.open('xb') as dest:
                        shutil.copyfileobj(source, dest)
                    verify(target, by_name[member.name])
        result = {'status': 'restored_verified_not_started', 'release_id': manifest['release_id'],
                  'files': sum(len(m['files']) for m in manifest['models'].values())}
        (output / 'RESTORED.json').write_text(json.dumps(result, indent=2))
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--archive-dir', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(restore(json.loads(args.manifest.read_text()), args.archive_dir, args.output)))


if __name__ == '__main__':
    main()
