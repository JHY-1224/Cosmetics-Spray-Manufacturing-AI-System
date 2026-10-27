"""Append explicitly synthetic numerical/record-reading teaching examples.

Never reads evaluation questions, images or reference answers. Source training
rows stay unchanged. Held-out parameter groups are excluded from optimization;
they are template-sharing capability checks, not independent field acceptance.
"""
import argparse
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'day1'))
from pipeline import verified_rows

spec = importlib.util.spec_from_file_location('training_views', Path(__file__).with_name('prepare_app_training.py'))
views = importlib.util.module_from_spec(spec)
spec.loader.exec_module(views)
D = Decimal
SOURCE_SHA = '08c200856db129bd21215b06ab5ef027d48200d6dcc8b99e8945fec80b34dd81'


def number(value):
    return format(D(value).normalize(), 'f')


def teaching_case(family, index, heldout=False):
    # Disjoint parameter groups. Every question explicitly identifies teaching data.
    k = index + (11 if heldout else 1)
    if family == 'percentage':
        total, pct, a, b = D(140 * k), D('2.5'), D(3), D(2)
        combined = total * pct / 100
        left, right = combined * a / (a + b), combined * b / (a + b)
        assert left + right == combined and left / right == a / b
        question = f'虚构教学核算：最终样品{number(total)}g，两种添加物合计{number(pct)}%（w/w），甲∶乙的投料质量比3∶2。只算投料质量，不推断纯活性含量，列式并核对合计。'
        answer = (f'合计={number(total)}×{number(pct)}%= {number(combined)}g；'
                  f'甲={number(combined)}×3/5={number(left)}g，乙={number(combined)}×2/5={number(right)}g。'
                  f'复核：{number(left)}+{number(right)}={number(combined)}g。没有纯度数据，不能计算纯活性量；这是教学计算，不是生产批准。')
        expected = dict(combined_g=number(combined), left_g=number(left), right_g=number(right))
    elif family == 'mass_ratio':
        total, a, b = D(7 * k), D(3), D(4)
        left, right = total * a / (a + b), total * b / (a + b)
        assert left + right == total
        question = f'虚构双组分涂料教学题：质量比明确为主料∶辅料=3∶4，总质量{number(total)}kg。分别多少kg？说明合计核对，不能当作现场配漆指令。'
        answer = (f'总份数3+4=7。主料={number(total)}×3/7={number(left)}kg，辅料={number(total)}×4/7={number(right)}kg。'
                  f'合计{number(left)}+{number(right)}={number(total)}kg，符合题设总量。仅作教学核算，实际配漆仍需批准依据。')
        expected = dict(total_kg=number(total), left_kg=number(left), right_kg=number(right))
    elif family == 'volume_density':
        total, left, right = D('5.1') * k, D('3.3') * k, D('1.8') * k
        assert left + right == total and left / D('1.1') / (right / D('0.9')) == D('1.5')
        question = f'虚构教学题：两液体体积比甲∶乙=3∶2，密度分别1.1kg/L、0.9kg/L，不含其他组分，总质量{number(total)}kg。求各自质量并核对体积比。'
        answer = (f'设每体积份为v L，则总质量=(3×1.1+2×0.9)v=5.1v kg，故v={k}L。'
                  f'甲=3×{k}×1.1={number(left)}kg，乙=2×{k}×0.9={number(right)}kg。'
                  f'合计{number(total)}kg；对应体积{3*k}L∶{2*k}L=3∶2。不是把体积比直接当质量比。仅为教学计算。')
        expected = dict(total_kg=number(total), left_kg=number(left), right_kg=number(right))
    elif family == 'unknown_basis':
        total, a, b = 9 * k, 7, 2
        question = f'虚构记录核查：比例栏只写甲∶乙={a}∶{b}，未注明质量/体积，目标总质量{total}kg，密度也未给。可直接分配kg数吗？'
        answer = (f'不能直接分配。{a}∶{b}的质量/体积基准未明确；若为体积比，还缺两组分密度。'
                  f'应保留总量{total}kg和原比例，补齐批准配方的比例基准与适用密度后再核算；当前各组分kg数未知，不能猜值或授权加料。')
        expected = dict(total_kg=str(total), component_masses='unknown', missing=['ratio_basis', 'density_if_volume'])
    elif family == 'units_and_identity':
        millimetres = D(k) / 10
        micrometres = millimetres * 1000
        ident = f'MAT-{600+k}'
        question = (f'虚构记录：设计尺寸{number(millimetres)}mm，实测栏写-{number(micrometres)}μm，物料号{ident}，材料批次及版本空白。'
                    '请保留符号、单位和身份，说明能否当作同一个实测值，能否补出批次或版本。')
        answer = (f'原样保留设计尺寸{number(millimetres)}mm和实测栏-{number(micrometres)}μm。'
                  f'{number(millimetres)}mm={number(micrometres)}μm，但负号与数值性质不同，不能合并或擅自去负号。'
                  f'物料号为{ident}；材料批次、版本均未知，不能由物料号生成。需核对原始实测和更正依据。')
        expected = dict(design_mm=number(millimetres), recorded_um='-'+number(micrometres), material=ident,
                        material_batch=None, version=None)
    elif family == 'sample_sufficiency':
        count, bad, minimum = 35 + k * 7, k, 210 + k * 21
        assert bad <= count < minimum
        question = (f'虚构复测核查：实际检查{count}件、不良{bad}件；预先要求最低{minimum}件，同时更换了清洗剂和涂料。'
                    '能否写成已完成最低样本、已证明唯一根因并结案放行？请区分实际量与要求。')
        answer = (f'不能。实际样本{count}件、不良{bad}件；{minimum}件是最低要求，实际{count}<{minimum}，尚未满足数量要求。'
                  '清洗剂和涂料同时变化，不能据改善确认唯一根因。还需按批准方案补足复测、核对同时变化因素与独立审批；不能虚构已完成、结案或放行。')
        expected = dict(actual_count=count, bad_count=bad, minimum_count=minimum, minimum_met=False)
    else:
        raise ValueError('Unknown teaching family')
    messages = views.app_view([{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}])
    return dict(id=f'teaching-{family}-{k}', split='validation' if heldout else 'train',
                provenance='code-generated fictitious teaching data; no field facts',
                expert_reviewed=False, family=family, parameter_group=k,
                expected=expected, messages=messages)


def build(source, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to overwrite data')
    source_rows, original = verified_rows(source)
    if original['train_sha256'] != SOURCE_SHA or len(source_rows) != 72:
        raise ValueError('Approved prior training-view inventory changed')
    families = ['percentage', 'mass_ratio', 'volume_density', 'unknown_basis',
                'units_and_identity', 'sample_sufficiency']
    supplement = [teaching_case(f, i) for f in families for i in range(8)]
    validation = [teaching_case(f, i, heldout=True) for f in families for i in range(2)]
    train = source_rows + supplement
    assert not {r['id'] for r in train} & {r['id'] for r in validation}
    assert not {r['parameter_group'] for r in supplement} & {r['parameter_group'] for r in validation}
    assert len({r['messages'][-2]['content'] for r in supplement + validation}) == 60
    output.mkdir(parents=True, exist_ok=False)
    for name, rows in [('train', train), ('validation', validation), ('test', [])]:
        (output / (name + '.messages.jsonl')).write_text(''.join(json.dumps(r, ensure_ascii=False)+'\n' for r in rows))
    manifest = {**original, 'train_rows': len(train), 'validation_rows': len(validation), 'test_rows': 0,
        'train_sha256': hashlib.sha256((output/'train.messages.jsonl').read_bytes()).hexdigest(),
        'validation_sha256': hashlib.sha256((output/'validation.messages.jsonl').read_bytes()).hexdigest(),
        'sample_ids': [r.get('id') for r in train], 'synthetic_teaching_rows': len(supplement),
        'derivation': '72 unchanged prior source-training views plus 48 code-verified synthetic teaching rows',
        'original_targets_changed': False, 'synthetic_expert_reviewed': False,
        'validation_scope': '12 parameter-disjoint, template-sharing capability probes; not independent field acceptance',
        'validation_used_in_optimization': False, 'evaluation_material_used': False,
        'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'status': 'prepared_not_trained_not_quality_accepted'}
    (output/'dataset-manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    shutil.copy2(source/'ATTRIBUTION.txt', output/'ATTRIBUTION.txt')
    verified_rows(output)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = build(args.source, args.output)
    print(json.dumps({k: result[k] for k in ('train_rows', 'validation_rows', 'synthetic_teaching_rows',
        'train_sha256', 'validation_sha256', 'evaluation_material_used')}, ensure_ascii=False, indent=2))
