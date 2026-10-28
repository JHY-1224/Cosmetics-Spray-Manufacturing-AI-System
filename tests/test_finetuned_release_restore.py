"""Publication restores must reject corruption and archive path attacks."""
import copy
import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('restore_release', Path(__file__).parents[1] / 'scripts/restore_finetuned_release.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture(tmp_path, attack=None):
    manifest = {'release_id': 'test', 'models': {}}
    for kind in ('text', 'vision'):
        archive = tmp_path / (kind + '.tar.gz')
        content = b'synthetic adapter'
        info = tarfile.TarInfo(attack if attack and kind == 'vision' else kind + '/adapter.json')
        info.size = len(content)
        with tarfile.open(archive, 'w:gz') as tf:
            tf.addfile(info, io.BytesIO(content))
        blob = archive.read_bytes()
        manifest['models'][kind] = {
            'downloads': [{'path': archive.name, 'bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest()}],
            'archive_bytes': len(blob), 'archive_sha256': hashlib.sha256(blob).hexdigest(),
            'files': [{'path': 'adapter.json', 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}]}
    return manifest


def test_restore_original_bytes_and_no_overwrite(tmp_path):
    manifest = fixture(tmp_path)
    output = tmp_path / 'restored'
    assert module.restore(manifest, tmp_path, output)['status'] == 'restored_verified_not_started'
    assert (output / 'vision/adapter.json').read_bytes() == b'synthetic adapter'
    with pytest.raises(ValueError, match='new directory'):
        module.restore(manifest, tmp_path, output)


def test_corrupt_part_does_not_create_output(tmp_path):
    manifest = fixture(tmp_path)
    (tmp_path / 'text.tar.gz').write_bytes(b'broken')
    with pytest.raises(ValueError, match='integrity'):
        module.restore(manifest, tmp_path, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('attack', ['../escape.json', '/absolute.json', 'vision/../../escape.json'])
def test_archive_member_escape_rejected(tmp_path, attack):
    manifest = fixture(tmp_path, attack)
    with pytest.raises(ValueError, match='archive member'):
        module.restore(manifest, tmp_path, tmp_path / 'out')
    assert not (tmp_path / 'out').exists()


def test_download_path_escape_rejected(tmp_path):
    manifest = fixture(tmp_path)
    manifest = copy.deepcopy(manifest)
    manifest['models']['text']['downloads'][0]['path'] = '../text.tar.gz'
    with pytest.raises(ValueError, match='filename'):
        module.restore(manifest, tmp_path, tmp_path / 'out')
