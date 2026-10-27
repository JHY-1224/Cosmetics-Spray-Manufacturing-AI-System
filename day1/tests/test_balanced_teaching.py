import importlib.util
from decimal import Decimal as D
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('balanced_teaching', ROOT/'day1/2-prepare-data/prepare_balanced_teaching.py')
teaching = importlib.util.module_from_spec(spec)
spec.loader.exec_module(teaching)


@pytest.mark.parametrize('family', teaching.FAMILIES)
def test_generated_arithmetic_and_identity_targets(family):
    for i, values in enumerate(teaching.parameters(family)[:100]):
        row = teaching.case(family, values, i, 'train')
        p = row['expected']
        assert row['expert_reviewed'] is False
        assert row['messages'][-2]['role']=='user' and row['messages'][-1]['role']=='assistant'
        if family in ('percentage','mass_ratio','density'):
            suffix = 'g' if family=='percentage' else 'kg'
            left,right = D(p['left_'+suffix]),D(p['right_'+suffix])
            assert left+right==D(p['combined_g'] if family=='percentage' else p['total_kg'])
            a,b = p['ratio']
            if family=='density':
                ra,rb = map(D,p['density_kg_l'])
                assert left/ra*b==right/rb*a
            else:assert left*b==right*a
        elif family=='partial_quantities':
            if 'combined_g' in p:assert D(p['total_g'])*D(p['percent'])/100==D(p['combined_g'])
            else:assert p['ratio_basis'] is None and p['component_masses'] is None
        elif family=='record_identity':
            assert p['material_id'] in row['messages'][-1]['content']
            assert p['batch'] is None and p['version'] is None
            if p['execution_mode']==0:assert '执行情况未知' in row['messages'][-1]['content']
        elif family=='sample_sufficiency':
            assert p['minimum_met']==(p['actual_count']>=p['minimum_count'])
            assert 0<=p['bad_count']<=p['actual_count']


def test_refuses_existing_destination_before_reading_source(tmp_path):
    with pytest.raises(ValueError,match='overwrite'):
        teaching.build(tmp_path/'missing',tmp_path/'missing-policy',tmp_path)
