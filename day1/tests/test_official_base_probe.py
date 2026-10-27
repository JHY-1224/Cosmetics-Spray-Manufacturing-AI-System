"""Exercise the exact frozen-kit import boundary without GPU/model loading."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'day1/multimodal/verify_official_base.py'
spec = importlib.util.spec_from_file_location('official_base_probe', SCRIPT)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def test_explicit_frozen_module_and_development_selection(frozen_kit):
    evaluation = probe.load_evaluation(frozen_kit)
    assert Path(evaluation.__file__) == frozen_kit / 'day1/multimodal/evaluate.py'
    rows, _ = evaluation.load_suite(frozen_kit / 'data/vision-eval-v2',
                                    ROOT / 'data/vision-public/metadata.jsonl')
    selected = probe.select_development(rows)
    assert len(selected) == 8
    assert all(r['split'] == 'development' for r in selected)
    assert len({r['category'] for r in selected}) == 8


def test_altered_package_rejected_before_import(tmp_path, frozen_kit):
    copied = tmp_path / 'kit'
    shutil.copytree(frozen_kit, copied)
    script = copied / 'day1/multimodal/evaluate.py'
    script.write_text('raise AssertionError("must never execute modified code")')
    with pytest.raises(ValueError, match='integrity failed'):
        probe.load_evaluation(copied)


def test_missing_explicit_kit_does_not_fall_back_to_import_search(tmp_path):
    with pytest.raises(FileNotFoundError):
        probe.load_evaluation(tmp_path)


def test_default_cli_checks_without_gpu_imports_or_creating_output(tmp_path, frozen_kit):
    output = tmp_path / 'no-inference'
    model = tmp_path / 'model-config-fixture'
    model.mkdir()
    (model / 'config.json').write_text('{"model_type":"qwen3_vl","text_config":{"hidden_size":4096}}')
    result = subprocess.run([
        sys.executable, str(SCRIPT), '--model-dir',
        str(model),
        '--training-data', str(ROOT / 'data/vision-public/metadata.jsonl'),
        '--evaluation-root', str(frozen_kit), '--output', str(output)],
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'files_and_imports_checked_no_inference' in result.stdout
    assert not output.exists()
