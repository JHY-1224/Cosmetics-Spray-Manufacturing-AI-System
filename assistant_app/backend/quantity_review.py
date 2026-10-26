"""Deterministic review notices for explicit user-declared quantity changes.

This compares statements, not verified measurements or approved process limits.
It never changes a recipe, authorizes execution, or infers a missing baseline.
"""
from decimal import Decimal
import re

NUMBER = r'[+-]?\d{1,16}(?:\.\d{1,12})?'
UNIT = r'(?:千克|公斤|毫克|毫升|克|升|kg|mg|ml|g|l)(?![a-zA-Z])'
PAIR = re.compile(
    r'(?:原来|原先|之前|上次|基准|原用量|上限)'
    r'(?P<before>[^\d\n。；;]{0,16}?)'
    rf'(?P<a>{NUMBER})\s*(?P<au>{UNIT})'
    r'(?P<between>[^\d\n。；;]{0,36}?)'
    rf'(?P<b>{NUMBER})\s*(?P<bu>{UNIT})', re.I)
CURRENT = re.compile(r'现在|本次|这次|改为|改成|增加到|增加至|调整到|调整为|计划|准备|拟')
DOSING = re.compile(r'加料|添加|加入|加水|加涂料|加固化剂|加稀释剂|加色浆|用量|配比|调漆|混配')
FACTORS = {'g':('mass',Decimal(1)), '克':('mass',Decimal(1)),
           'kg':('mass',Decimal(1000)), '千克':('mass',Decimal(1000)), '公斤':('mass',Decimal(1000)),
           'mg':('mass',Decimal('.001')), '毫克':('mass',Decimal('.001')),
           'ml':('volume',Decimal(1)), '毫升':('volume',Decimal(1)),
           'l':('volume',Decimal(1000)), '升':('volume',Decimal(1000))}


def material_label(text):
    text = CURRENT.sub('', text)
    text = re.sub(r'原来|原先|之前|上次|基准|原用量|上限|添加|加入|加料|用量|使用|调整|增加|改|加|为|是|的', '', text)
    return re.sub(r'[\s，,：:、=→＞>]+', '', text)


def quantity_notices(text, warning_ratio=Decimal('2')):
    notices, compared = [], False
    for match in PAIR.finditer(text):
        if not CURRENT.search(match['between']):
            continue
        a, b = Decimal(match['a']), Decimal(match['b'])
        au, bu = FACTORS[match['au'].lower()], FACTORS[match['bu'].lower()]
        left, right = material_label(match['before']), material_label(match['between'])
        # Different material names or mass/volume cannot form a defensible comparison.
        if au[0] != bu[0] or (left and right and left != right):
            notices.append({'code':'quantity_context_mismatch', 'source':'user_statement',
                'message':'数量的物料或质量/体积基准可能不同，不能直接比较。请核对原料、单位、批量和批准配方。'})
            continue
        compared = True
        baseline, current = a*au[1], b*bu[1]
        if baseline <= 0 or current < 0:
            notices.append({'code':'quantity_invalid_baseline', 'source':'user_statement',
                'message':'输入的基准用量非正数或本次用量为负数，无法作有效比较。请先核对原始记录和单位。'})
            continue
        factor = current / baseline
        if factor >= warning_ratio or current == 0 or (current > 0 and baseline/current >= warning_ratio):
            factor_text = format(factor, '.4f').rstrip('0').rstrip('.')
            if not factor_text:
                factor_text = '0'
            notices.append({'code':'quantity_change_review', 'source':'user_statement',
                'baseline':match['a']+match['au'], 'current':match['b']+match['bu'], 'ratio':str(factor),
                'message':f"输入用量由{match['a']}{match['au']}变为{match['b']}{match['bu']}，是原值的{factor_text}倍，变化较大。"
                          '请确认是否录入错误，并核对物料、单位、批量和批准配方后再由有权限人员处理。'
                          '这是数据变化提醒，不代表已认定超工艺限值，也不构成加料或执行批准。'})
    if DOSING.search(text) and not compared and not notices:
        notices.append({'code':'quantity_basis_required', 'source':'user_statement',
            'message':'加料/配比是否合适，需要对应物料、质量或体积基准、批量和有效批准配方/SOP。'
                      '当前没有明确可比的基准，不能确认是否过量或直接给出现场加量。'})
    # Repeated quoted snippets must not create an unbounded warning panel.
    return list({(n['code'],n['message']):n for n in notices}.values())[:5]
