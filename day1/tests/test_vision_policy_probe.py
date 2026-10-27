import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('policy_probe', ROOT / 'day1/multimodal/probe_vision_policy.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@pytest.mark.parametrize('ids', [['locked-1'], ['dev-1', 'dev-1'], ['missing']])
def test_locked_duplicate_and_unknown_ids_are_rejected(ids):
    evaluation = SimpleNamespace(load_suite=lambda *a: ([
        {'id': 'dev-1', 'split': 'development'}, {'id': 'locked-1', 'split': 'locked'}], {}))
    args = SimpleNamespace(evaluation_root=Path('/kit'), training_data=Path('/train'), case_ids=ids)
    with pytest.raises(ValueError, match='unique development'):
        probe.selected_development(evaluation, args)


def test_small_probe_keeps_full_development_denominator():
    rows = [{'id': 'dev-' + str(i), 'split': 'development'} for i in range(48)]
    rows.append({'id': 'locked', 'split': 'locked'})
    evaluation = SimpleNamespace(load_suite=lambda *a: (rows, {'questions': 49}))
    args = SimpleNamespace(evaluation_root=Path('/kit'), training_data=Path('/train'), case_ids=['dev-0'])
    selected, _, expected = probe.selected_development(evaluation, args)
    assert len(selected) == 1 and expected == 48
