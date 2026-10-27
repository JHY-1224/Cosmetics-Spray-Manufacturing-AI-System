"""Boundary checks for the independent collator audit, without GPU imports."""
import importlib.util
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / 'multimodal/audit_supervision.py'
spec = importlib.util.spec_from_file_location('audit_supervision', MODULE)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def check(ids, labels, attention=None, tail=None):
    return audit.check_labels(ids, labels, attention or [1] * len(ids),
                              tail or [2, 3, 4, 5], [2], [3, 4], [5], 5)


def test_answer_eos_and_padding():
    result = check([1, 2, 3, 4, 5, 0], [-100, -100, 3, 4, 5, -100], [1, 1, 1, 1, 1, 0])
    assert result['passed'] and result['eos_supervised']
    assert check([0, 1, 2, 3, 4, 5], [-100, -100, -100, 3, 4, 5], [0, 1, 1, 1, 1, 1])['passed']


@pytest.mark.parametrize('labels', [
    [-100, -100, 3, 4, -100],  # Missing EOS.
    [1, -100, 3, 4, 5],       # Leaking user/image prefix.
    [-100, 2, 3, 4, 5],       # Leaking assistant header.
    [-100, -100, -100, 4, 5], # Missing part of the answer.
    [-100, -100, 9, 4, 5],    # Labels differ from actual input IDs.
])
def test_reject_incorrect_supervision(labels):
    assert not check([1, 2, 3, 4, 5], labels)['passed']


def test_unprovable_alignment_raises():
    with pytest.raises(ValueError, match='does not exactly align'):
        check([1, 2, 3, 9, 5], [-100, -100, 3, 9, 5])
    with pytest.raises(ValueError, match='non-contiguous'):
        check([1, 2, 3, 4, 5], [-100, -100, 3, 4, 5], [1, 0, 1, 1, 1])


def test_padding_cannot_be_supervised():
    result = check([1, 2, 3, 4, 5, 0], [-100, -100, 3, 4, 5, 0], [1, 1, 1, 1, 1, 0])
    assert not result['passed'] and result['supervised_padding_positions'] == [5]
