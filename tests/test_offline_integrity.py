import json
import pytest
from scripts.bundle_checksums import inventory, verify


def test_offline_bundle_detects_tampered_wheel_and_path_escape(tmp_path):
    (tmp_path / "wheels").mkdir()
    wheel = tmp_path / "wheels" / "reference.whl"
    wheel.write_bytes(b"fixed reference bytes")
    manifest = tmp_path / "bundle-checksums.json"
    manifest.write_text(json.dumps(inventory(tmp_path)))
    assert verify(tmp_path)["verified_files"] == 1
    wheel.write_bytes(b"tampered reference bytes")
    with pytest.raises(ValueError):
        verify(tmp_path)
    manifest.write_text(json.dumps({"../outside": "0" * 64}))
    with pytest.raises(ValueError):
        verify(tmp_path)
