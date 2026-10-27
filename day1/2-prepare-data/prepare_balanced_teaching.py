"""Versioned, reproducible teaching data; never reads evaluation questions.

Development failures inform the task families, not copied examples or targets.
Synthetic arithmetic is checked with Decimal; it is not a real approved recipe.
The new parameter holdout shares families and is not field acceptance evidence.
"""
import argparse
import copy
from decimal import Decimal as D
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import random
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'day1'))
from pipeline import verified_rows

spec = importlib.util.spec_from_file_location('balanced_views', Path(__file__).with_name('prepare_app_training.py'))
views = importlib.util.module_from_spec(spec)
spec.loader.exec_module(views)
SOURCE_SHA = '08c200856db129bd21215b06ab5ef027d48200d6dcc8b99e8945fec80b34dd81'
POLICY_SHA = 'f888c4c87fc5ecf4116f45faf07c70d6638a4c417c9dc5b2401faad35dffb291'
FAMILIES = ('percentage', 'density', 'mass_ratio', 'partial_quantities', 'record_identity', 'sample_sufficiency')


def n(value):
    return format(D(value).normalize(), 'f')


def parameters(family):
    if family == 'mass_ratio':
        values = [(a,b,k,D(1)) for a,b,k in itertools.product(range(2,9),range(2,8),range(2,19))]
    elif family in ('percentage', 'partial_quantities'):
        values = list(itertools.product(range(2, 9), range(2, 8), range(2, 19), (D('.5'), D('1.25'), D('2.5'), D('4'))))
    elif family == 'density':
        values = list(itertools.product(range(2, 7), range(2, 6), (D('.75'), D('.8'), D('1.05'), D('1.2')), (D('.85'), D('.95'), D('1.1')), range(2, 23)))
    else:
        values = list(itertools.product(range(21, 91), range(3, 11), range(3)))
    random.Random('balanced-teaching-3407-' + family).shuffle(values)
    return values


def case(family, params, index, split):
    proof = {}
    if family == 'percentage':
        a, b, k, pct = params
        combined = D(a + b) * k
        total = combined * 100 / pct
        left, right = D(a) * k, D(b) * k
        assert total * pct / 100 == left + right == combined
        questions = [
            f'最终混合物{n(total)}g，甲和乙合计占{n(pct)}%（w/w），两者投料质量比{a}:{b}。分别计算质量，列公式及合计校验；未给纯度。',
            f'记录：样品总质量={n(total)} g；甲+乙总投料质量分数={n(pct)}%；甲:乙质量比={a}:{b}。请先列式再给称量核算，不作纯活性折算。',
            f'请复核教学配料：甲乙质量比{a}∶{b}，两者合计为最终{n(total)}g的{n(pct)}%。需要甲乙各自质量与和，禁止猜纯度。']
        question = questions[index % 3]
        answer = (f'先算合计：{n(total)}×{n(pct)}/100={n(combined)}g。总份数={a}+{b}={a+b}。'
                  f'甲={n(combined)}×{a}/{a+b}={n(left)}g；乙={n(combined)}×{b}/{a+b}={n(right)}g。'
                  f'校验：{n(left)}+{n(right)}={n(combined)}g，甲乙质量比={a}:{b}。'
                  '以上为投料量；纯度未给，纯活性量未知。仅作教学核算，不批准实际称量或加料。')
        proof = dict(total_g=n(total), percent=n(pct), ratio=[a,b], combined_g=n(combined), left_g=n(left), right_g=n(right))
    elif family == 'density':
        a, b, ra, rb, k = params
        coefficient = a * ra + b * rb
        left, right = a * D(k) * ra, b * D(k) * rb
        total = left + right
        assert coefficient * k == total and left / ra == a*k and right / rb == b*k
        question = (f'两液体甲∶乙为体积比{a}∶{b}，密度分别{n(ra)}kg/L和{n(rb)}kg/L，'
                    f'仅这两组分，总质量{n(total)}kg。求各自质量，并用体积比和总质量复核。')
        if index % 2:
            question = (f'复核表：甲/乙体积份={a}/{b}；甲密度={n(ra)} kg/L；乙密度={n(rb)} kg/L；'
                        f'合计质量={n(total)} kg（没有其他组分）。请列方程求kg，不把体积比当质量比。')
        answer = (f'设每体积份为v L。质量合计=({a}×{n(ra)}+{b}×{n(rb)})v='
                  f'({n(a*ra)}+{n(b*rb)})v={n(coefficient)}v={n(total)}kg，故v={n(total)}/{n(coefficient)}={k}L。'
                  f'甲质量={a}×{k}×{n(ra)}={n(left)}kg；乙质量={b}×{k}×{n(rb)}={n(right)}kg。'
                  f'质量校验：{n(left)}+{n(right)}={n(total)}kg；体积校验：{n(left)}/{n(ra)}={a*k}L，'
                  f'{n(right)}/{n(rb)}={b*k}L，比例={a}:{b}。仅为教学计算，不是已批准配方。')
        proof = dict(ratio=[a,b], density_kg_l=[n(ra),n(rb)], scale_l=k, total_kg=n(total), left_kg=n(left), right_kg=n(right))
    elif family == 'mass_ratio':
        a, b, k, _ = params
        left, right, total = D(a)*k, D(b)*k, D(a+b)*k
        assert left + right == total
        question = f'主剂∶辅剂明确为质量比{a}∶{b}，总质量{n(total)}kg。先列式，求两者kg数并检查合计。'
        answer = (f'总份数={a}+{b}={a+b}。主剂={n(total)}×{a}/{a+b}={n(left)}kg；'
                  f'辅剂={n(total)}×{b}/{a+b}={n(right)}kg。合计={n(left)}+{n(right)}={n(total)}kg。'
                  '质量比已经明确，本数学题可计算；计算不等于已批准现场配漆。')
        proof = dict(ratio=[a,b], total_kg=n(total), left_kg=n(left), right_kg=n(right))
    elif family == 'partial_quantities':
        a, b, k, pct = params
        total = D(a+b)*k
        if index % 2:
            question = f'比例栏只写甲∶乙={a}:{b}，质量/体积基准空白，目标总质量{n(total)}kg，密度未给。能直接拆出各自kg吗？请保留已知总量。'
            answer = (f'已知总质量{n(total)}kg，原比例{a}:{b}；未知的是比例基准和两者各自kg数。'
                      '不能直接拆分。若是质量比可按质量份分配；若是体积比需对应密度。'
                      '需先核对比例基准，不能把已知总量说成缺失，也不能猜密度或授权现场加料。')
            proof = dict(total_kg=n(total), ratio=[a,b], ratio_basis=None, component_masses=None)
        else:
            combined = total*pct/100
            question = f'最终样品{n(total)}g，甲和乙合并标注{n(pct)}%（w/w），组内比例空白。合计及各自质量能算多少？'
            answer = (f'合计可算：{n(total)}×{n(pct)}/100={n(combined)}g。'
                      '甲各自质量未知，乙各自质量未知，因为没有组内分配比例；不能给每种各分配整个百分数。'
                      f'部分信息缺失不影响合计{n(combined)}g的计算。仅为教学核算，不是加料批准。')
            proof = dict(total_g=n(total), percent=n(pct), combined_g=n(combined), left_g=None, right_g=None)
    elif family == 'record_identity':
        k, factor, mode = params
        mm, microns = D(k)/100, D(k)*10
        ident = f'TEACH-MAT-{k}-{factor}'
        signed = ('-' if mode != 1 else '') + n(microns)
        execution = ('空白', '明确写“尚未执行”', '明确写“人工登记已执行，待核验”')[mode]
        question = (f'记录：设计尺寸{n(mm)}mm，实测栏{signed}μm（负号含义未定义），物料号{ident}，'
                    f'材料批次和版本空白，执行栏{execution}。请保留读数、标识，说明哪些已知和未知，能否认定质量合格或已放行。')
        observed = ('执行栏为空，只能说执行情况未知；不能写成“未执行”或“已执行”。',
                    '执行栏明确记录“尚未执行”，可转录为来源声明未执行。',
                    '执行栏仅有人工作出的已执行登记且待核验，可转录来源声明，不能说平台已独立核验。')[mode]
        answer = (f'保留设计{n(mm)}mm、实测栏{signed}μm、物料号{ident}。'
                  f'单位尺度换算{n(mm)}mm={n(microns)}μm；设计值与实测记录性质不同，'
                  '有负号时必须保留，未定义含义不能擅自去掉或修正。'
                  f'批次与版本未知，不能从物料号生成。{observed}'
                  '没有适用验收标准和独立放行记录，不能仅凭这些字段认定质量合格、已结案或已放行。')
        proof = dict(design_mm=n(mm), recorded_um=signed, material_id=ident, batch=None, version=None, execution_mode=mode)
    elif family == 'sample_sufficiency':
        k, factor, mode = params
        minimum = k*factor
        actual = minimum + (-factor, 0, factor)[mode]
        bad = k % 5 + 1
        met = actual >= minimum
        question = (f'实际复测{actual}件，其中不良{bad}件；预先规定最低样本{minimum}件。'
                    '同期更换清洗剂和涂料，其他验收条件、独立审批记录未提供。请区分实际与要求，能否确认唯一根因、结案放行？')
        answer = (f'实际样本={actual}件，不良={bad}件；最低要求={minimum}件。数量比较：'
                  f'{actual}{">=" if met else "<"}{minimum}，数量门槛{"达到" if met else "未达到"}。'
                  '数量门槛与全部复测合格是两回事；其他验收条件未给，整体效果尚不能确认。'
                  '同期两个变量改变，不能据此证明唯一根因；独立审批与正式放行情况未知，不能宣称结案或放行。')
        proof = dict(actual_count=actual, bad_count=bad, minimum_count=minimum, minimum_met=met)
    else:
        raise ValueError('Unknown teaching family')
    question = '虚构教学资料（不是现场事实或生产配方）：' + question
    messages = views.app_view([dict(role='user',content=question),dict(role='assistant',content=answer)])
    group = hashlib.sha256(json.dumps([family,[str(v) for v in params]]).encode()).hexdigest()
    return dict(id=f'balanced-{family}-{split}-{index:03d}',split=split,family=family,parameter_group=group,
                provenance='code-generated fictitious teaching; no field facts',expert_reviewed=False,expected=proof,messages=messages)


def build(source, policy_file, output):
    if output.exists() or output.is_symlink():raise ValueError('Refusing to overwrite data')
    source_rows, original = verified_rows(source)
    if len(source_rows)!=72 or original['train_sha256']!=SOURCE_SHA:raise ValueError('Prior approved views changed')
    policy = policy_file.read_text()
    if hashlib.sha256(policy.encode()).hexdigest()!=POLICY_SHA:raise ValueError('Candidate policy changed')
    train = copy.deepcopy(source_rows)
    for row in train:
        row['messages'][0] = dict(role='system',content=policy)
    for old,new in zip(source_rows,train,strict=True):
        assert [m['content'] for m in old['messages'] if m['role']=='assistant']==[m['content'] for m in new['messages'] if m['role']=='assistant']
    supplements, validation = [], []
    for family in FAMILIES:
        params = parameters(family)
        seen = set()
        i = 0
        for p in params:
            row = case(family,p,i,'train' if i<64 else 'validation')
            if row['messages'][1]['content'] in seen:continue
            seen.add(row['messages'][1]['content'])
            if i<64:
                row['messages'][0] = dict(role='system',content=policy)
                supplements.append(row)
            else:validation.append(row)
            i += 1
            if i==80:break
        assert i==80
    assert not {r['parameter_group'] for r in supplements}&{r['parameter_group'] for r in validation}
    assert len({r['messages'][1]['content'] for r in supplements+validation})==480
    train += supplements
    output.mkdir(parents=True,exist_ok=False)
    for name,rows in [('train',train),('validation',validation),('test',[])]:
        with (output/(name+'.messages.jsonl')).open('x') as f:
            for row in rows:f.write(json.dumps(row,ensure_ascii=False)+'\n')
    manifest={**original,'train_rows':len(train),'validation_rows':len(validation),'test_rows':0,
        'sample_ids':[r['id'] for r in train],
        'train_sha256':hashlib.sha256((output/'train.messages.jsonl').read_bytes()).hexdigest(),
        'validation_sha256':hashlib.sha256((output/'validation.messages.jsonl').read_bytes()).hexdigest(),
        'synthetic_teaching_rows':len(supplements),'original_targets_changed':False,
        'derivation':'72 prior source views with candidate system policy plus384 varied Decimal-checked teaching rows',
        'source_views_sha256':SOURCE_SHA,'training_policy_sha256':POLICY_SHA,
        'validation_policy':'original app policy placeholder; evaluator explicitly replaces with candidate policy',
        'validation_scope':'96 parameter-disjoint family-sharing synthetic development probes; not field acceptance',
        'validation_used_in_optimization':False,'evaluation_rows_in_training':False,
        'development_findings_informed_design':True,'sealed_data_read':False,
        'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'status':'prepared_not_trained_not_quality_accepted'}
    (output/'dataset-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    shutil.copy2(source/'ATTRIBUTION.txt',output/'ATTRIBUTION.txt')
    verified_rows(output)
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True)
    p.add_argument('--policy-file',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    result=build(args.source,args.policy_file,args.output)
    print(json.dumps({k:result[k] for k in ('train_rows','validation_rows','train_sha256','validation_sha256')},indent=2))
