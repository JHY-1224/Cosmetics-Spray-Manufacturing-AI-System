"""Guard source preservation and production boundaries of candidate annotations."""
import importlib.util
from pathlib import Path

import pytest

from assistant_app.backend.schemas import VisionEvidence

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('aligned_candidate', ROOT / 'day1/multimodal/prepare_aligned.py')
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


def test_changed_source_fails_before_output_is_created(tmp_path):
    source = tmp_path / 'metadata.jsonl'
    source.write_text('{"changed":true}\n')
    output = tmp_path / 'new-candidate'
    with pytest.raises(ValueError, match='Original source metadata changed'):
        candidate.build(source, tmp_path / 'absent-review.tsv', output)
    assert not output.exists()


def test_existing_output_is_never_overwritten(tmp_path):
    output = tmp_path / 'existing'
    output.mkdir()
    kept = output / 'keep.txt'
    kept.write_text('original')
    with pytest.raises(ValueError, match='Refusing to overwrite'):
        candidate.build(tmp_path / 'absent-source', tmp_path / 'absent-review', output)
    assert kept.read_text() == 'original'


def test_all_manual_reading_targets_obey_strict_contract():
    for ident, (_, fields, warnings) in candidate.reading_targets().items():
        answer = dict(image_id=ident.split('-read-')[0], image_kind='来源文档页',
                      visible_observations=['可见表格和正文'], extracted_fields=fields,
                      uncertain_items=[], missing_context=[], warnings=warnings)
        result = VisionEvidence.model_validate(answer)
        assert result.extracted_fields
        assert all(f.value_text is None and f.unit_text is None for f in result.extracted_fields
                   if f.status != 'readable')


def test_visual_targets_do_not_invent_calculations_or_units():
    recipes = candidate.reading_targets()
    _, fields, _ = recipes['S30-P040-read-1']
    assert len(fields) == 5  # Only the five printed components; no computed total.
    _, fields, _ = recipes['S16-P035-read-2']
    assert {f['unit_text'] for f in fields if f['label'].endswith('单个喷涂面积')} == {'m²'}
    _, fields, _ = recipes['S16-P035-read-1']
    basis = next(f for f in fields if f['label'] == '比例计量基准')
    assert basis['status'] == 'unknown'
    assert basis['value_text'] is None and basis['unit_text'] is None
