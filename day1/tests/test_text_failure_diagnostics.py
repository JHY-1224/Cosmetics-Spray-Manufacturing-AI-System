import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('text_failure_diagnostics',
    ROOT / 'day1/4-validation/diagnose_text_failures.py')
diagnostic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostic)


def test_targets_are_removed_and_original_messages_are_preserved():
    row = {'messages': [{'role': 'system', 'content': 'policy'},
                        {'role': 'user', 'content': 'question'},
                        {'role': 'assistant', 'content': 'reference only'}]}
    before = copy.deepcopy(row)
    result = diagnostic.inputs_only(row, 'candidate policy')
    assert row == before
    assert result == [{'role': 'system', 'content': 'candidate policy'},
                      {'role': 'user', 'content': 'question'}]
    with pytest.raises(ValueError):
        diagnostic.inputs_only({'messages': row['messages'][:-1]})


def test_table_reformat_preserves_original_columns_and_cells():
    rows = diagnostic.json_rows(ROOT / 'data/text-training/balanced-20261027-v3/train.messages.jsonl')
    row = next(r for r in rows if r['id'] == 'S-bfed82d3b7db875642-canonical')
    question = row['messages'][1]['content']
    excerpt = diagnostic.table_excerpt(question)
    assert '| PLA/PHA-84P | 0.59 |' in excerpt
    assert '| PLA/PHA-84 | 0.57 |' in excerpt
    assert '| PLA Jar Bottom | 0.52 |' in excerpt
    assert '| PLA/PHA Jar Bottom | 0.06/0.46 |' in excerpt
    assert '| PLA/PHA-182 | 0.04/59.54 |' in excerpt
    assert row['messages'][-1]['content'] not in excerpt
    with pytest.raises(ValueError, match='columns'):
        diagnostic.table_excerpt(question.replace('PLA/PHA-84P\n', 'PLA/PHA-84\n'))


def test_diagnostic_scope_uses_existing_replay_and_no_reference_in_generation():
    plan = json.loads((ROOT / 'day1/4-validation/plans/text-balanced-replay-v3.json').read_text())
    refs, controls, manifest = diagnostic.prepare(ROOT / 'data/text-training/balanced-20261027-v3', plan)
    assert len(refs) == 20 and len(controls) == 10
    assert manifest['validation_used_in_optimization'] is False
    assert all(r['split'] in ('train', 'validation') for r in refs)
    assert all(r['messages'][-1]['role'] == 'user' for r in controls)
    assert all(m['role'] != 'assistant' for r in controls for m in r['messages'])
