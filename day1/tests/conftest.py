"""Portable fixtures from the published, hash-pinned evaluation archive."""
import hashlib
from pathlib import Path
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='session')
def frozen_kit(tmp_path_factory):
    archive = ROOT / 'evaluation/evaluation-kit-20261026.tar.gz'
    expected = '8b1ec491b099ae91383918a3c46ebbcd3124823e6a3d59362a0dcff0ddb6a399'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == expected
    output = tmp_path_factory.mktemp('frozen-evaluation')
    with tarfile.open(archive, 'r:gz') as package:
        package.extractall(output, filter='data')
    return output / 'evaluation-kit-20261026'
