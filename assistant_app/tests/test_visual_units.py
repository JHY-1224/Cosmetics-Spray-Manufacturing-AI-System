import copy

import pytest
from pydantic import ValidationError

from assistant_app.backend.schemas import VisionEvidence
from assistant_app.backend.visual_units import canonical_units


def evidence(unit, status='readable', value='-11.2'):
    return dict(image_id='fixture', image_kind='table', visible_observations=['original record'],
        extracted_fields=[dict(label='temperature', raw_text='original -11.2 '+str(unit), value_text=value,
                               unit_text=unit, location='row 1', status=status)],
        uncertain_items=[], missing_context=[], warnings=[])


@pytest.mark.parametrize('unit', ['° C', '°\u00a0C', '°\u202fC', '℃'])
def test_typography_preserves_raw_text_numbers_and_source_object(unit):
    source = VisionEvidence.model_validate(evidence(unit)).model_dump()
    before = copy.deepcopy(source)
    result, changes = canonical_units(source)
    assert source == before
    assert result['extracted_fields'][0]['unit_text'] == '°C'
    assert result['extracted_fields'][0]['raw_text'] == source['extracted_fields'][0]['raw_text']
    assert result['extracted_fields'][0]['value_text'] == '-11.2'
    assert changes[0]['original'] == unit
    assert canonical_units(result) == (result, [])


@pytest.mark.parametrize('unit', ['°C', '°c', 'K', 'mPa s', 'mPa·s', 'mL', 'μm', 'mm', None])
def test_other_units_are_neither_guessed_nor_converted(unit):
    source = VisionEvidence.model_validate(evidence(unit)).model_dump()
    assert canonical_units(source) == (source, [])


def test_normalization_cannot_bypass_length_or_uncertainty_contract():
    for data in [evidence('°'+' '*80+'C'), evidence('° C', status='unknown', value=None)]:
        with pytest.raises(ValidationError):
            VisionEvidence.model_validate(data)
