"""Build a NEW development SFT candidate from reviewed training images only.

Original captions/images and historical review/rights flags remain untouched.
This does not read evaluation questions, run training or approve deployment.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from assistant_app.backend.prompts import VISION, policy_metadata
from assistant_app.backend.schemas import VisionEvidence
from dataset import load_records

SOURCE_SHA = '66eeb0c7de39c3963857dbff50f2d231680823c3949a5e9ba879c6066f19fd81'


def field(label, value, unit, location, raw=None, status='readable'):
    return dict(label=label, raw_text=raw if raw is not None else value,
                value_text=value, unit_text=unit, location=location, status=status)


def reading_targets():
    """Manual transcription checked against S30-P040 and S16-P035 pixels."""
    s30 = '表2-8'
    s16 = '表2-8'
    return {
        'S30-P040-read-1': (
            '读取表2-8的UV面漆一行，逐项列出五类主要成分及比例；保留原值，不计算合计。',
            [field('UV面漆/' + label, value, '%', s30 + ' UV面漆行/主要成分', label + '（' + value + '%）')
             for label, value in [('聚氨酯丙烯酸酯', '50'), ('环氧丙烯酸酯', '24'),
                                  ('单体', '20'), ('光引发剂', '5'), ('BYK助剂', '1')]], []),
        'S30-P040-read-2': (
            '读取表2-8水性底漆一行的五类主要成分范围和密度，保留名称、范围与单位。',
            [field('水性底漆/' + label, value, '%', s30 + ' 水性底漆行/主要成分', label + value + '%')
             for label, value in [('水性聚氨酯丙烯酸树脂', '35～45'), ('水性丙烯酸树脂', '3～6'),
                                  ('2-羟基-2-甲基-1苯基-1-丙酮', '2～5'), ('二丙二醇甲醚', '3～6'),
                                  ('水', '45～50')]] +
            [field('水性底漆/密度', '1.050', 'g/cm³', s30 + ' 水性底漆行/密度', '密度1.050g/cm³')],
            ['这是组成范围的转录；不选择范围端点配制涂料。']),
        'S30-P040-read-3': (
            '转录页首备注中关于外购成品油漆及是否另加助剂的说明。',
            [field('页首备注', '项目外购成品油漆进行喷涂，无需再添加助剂等进行调漆。', None,
                   '表2-8上方/备注')], []),
        'S30-P040-read-4': (
            '分别读取UV底漆和UV面漆两行的VOCs检测结果与折算VOC占比，不作新计算。',
            [field('UV底漆/VOCs检测结果', '156', 'g/L', s30 + ' UV底漆行/末段', 'VOCs检测结果为156g/L'),
             field('UV底漆/折算VOC占比', '14.3', '%', s30 + ' UV底漆行/末段', '折算VOC占比约为14.3%'),
             field('UV面漆/VOCs检测结果', '93', 'g/L', s30 + ' UV面漆行/末段', 'VOCs检测结果为93g/L'),
             field('UV面漆/折算VOC占比', '10.3', '%', s30 + ' UV面漆行/末段', '折算VOC占比约为10.3%')],
            ['数值是来源页面对报告的转述，不是当前客户批次的新检测。']),
        'S16-P035-read-1': (
            '转录正文的三组分调配比例；如未明确质量或体积基准，保留未知。',
            [field('组分顺序', '水性玻璃烘烤涂料：色浆：水', None, '表2-8下方正文'),
             field('调配比例', '10:1:2', None, '表2-8下方正文', '水=10:1:2'),
             field('比例计量基准', None, None, '表2-8下方正文', None, 'unknown')],
            ['正文未明确质量比或体积比。']),
        'S16-P035-read-2': (
            '读取表2-8中2#和3#无尘涂装自动线的产品产能、单个喷涂面积、总喷涂面积。',
            [field(line + '/' + label, value, unit, s16 + ' ' + line + '行/' + label, value)
             for line, cap, area in [('2#无尘涂装自动线', '4158', '99792'),
                                     ('3#无尘涂装自动线', '2772', '66528')]
             for label, value, unit in [('产品产能', cap, '万个/年'),
                                        ('单个喷涂面积', '0.0024', 'm²'), ('总喷涂面积', area, 'm²')]],
            ['单位来自表头；设计核算表不是现场实测记录。']),
        'S16-P035-read-3': (
            '查看本图表2-9是否跨页；只转录可见内容，不补猜色浆和水的密度。',
            [field('色浆/密度', None, None, '页底表2-9/本页未显示该行', None, 'unknown'),
             field('水/密度', None, None, '页底表2-9/本页未显示该行', None, 'unknown')],
            ['表2-9跨页，本图只有表头及起始行；需同表续页。']),
        'S16-P035-read-4': (
            '分别读取表2-8中5#内喷线和6#内喷线的产能、单个喷涂面积、总喷涂面积。',
            [field(line + '/' + label, value, unit, s16 + ' ' + line + '行/' + label, value)
             for line in ('5#内喷线', '6#内喷线')
             for label, value, unit in [('产品产能', '3465', '万个/年'),
                                        ('单个喷涂面积', '0.0018', 'm²'), ('总喷涂面积', '62370', 'm²')]],
            ['两行保持独立；不合并为一次生产实测。']),
    }


def build(source, review, output):
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to overwrite an existing candidate')
    if hashlib.sha256(source.read_bytes()).hexdigest() != SOURCE_SHA:
        raise ValueError('Original source metadata changed; review before rebuilding')
    rows = load_records(source, allow_unreviewed=True)
    if len(rows) != 82 or any(r['split'] != 'train' for r in rows):
        raise ValueError('Expected original 82 training-only records')
    with review.open(encoding='utf-8', newline='') as f:
        notes = list(csv.DictReader(f, delimiter='\t'))
    visual = {r['image_id']: r for r in notes}
    if len(notes) != len(visual) or len(visual) != 74:
        raise ValueError('Require exactly 74 individually reviewed images')
    recipes = reading_targets()
    candidates = []
    for row in rows:
        image_id = Path(row['images'][0]['path']).stem
        note = visual[image_id]
        if row['id'] in recipes:
            question, fields, warnings = recipes[row['id']]
        else:
            if row['id'] != image_id + '-context':
                raise ValueError('Unreviewed task ID: ' + row['id'])
            question = ('请客观描述这张' + note['image_kind'] + '中1至3项可见内容，'
                        '并说明画面不能直接确认的现场事实；本题只需画面概述，不作全文转录。')
            fields, warnings = [], []
        answer = dict(image_id=image_id, image_kind=note['image_kind'],
                      visible_observations=note['visible_observations'].split('；'),
                      extracted_fields=fields, uncertain_items=[note['uncertain_items']],
                      missing_context=['未提供当前客户批次、采集时间及现场核验记录。'], warnings=warnings)
        VisionEvidence.model_validate(answer)
        candidates.append({k: v for k, v in row.items() if k != '_paths'} | {
            'id': row['id'] + '-aligned-v1', 'original_id': row['id'], 'system': VISION,
            'question': json.dumps(dict(image_id=image_id, order=1, question=question), ensure_ascii=False),
            'answer': json.dumps(answer, ensure_ascii=False, separators=(',', ':')),
            'label_basis': 'assistant_visual_review_20261027; production_schema_aligned_development_candidate',
            'asset_user_approved_for_training': True, 'annotation_expert_approved': False,
            'original_answer_sha256': hashlib.sha256(row['answer'].encode()).hexdigest(),
        })
    # All annotations are validated before allocating the new output directory.
    output.mkdir(parents=True, exist_ok=False)
    for row in rows:
        for item, path in zip(row['images'], row['_paths'], strict=True):
            target = output / item['path']
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
    metadata = output / 'metadata.jsonl'
    with metadata.open('x', encoding='utf-8') as f:
        for row in candidates:
            f.write(json.dumps(row, ensure_ascii=False) + '\n')
    load_records(metadata, allow_unreviewed=True)
    card = dict(status='development_candidate_not_trained', rows=len(candidates), images=len(visual),
                original_data_sha256=SOURCE_SHA,
                reviewed_visuals_sha256=hashlib.sha256(review.read_bytes()).hexdigest(),
                cases_sha256=hashlib.sha256(metadata.read_bytes()).hexdigest(), prompt=policy_metadata(),
                structured_reading_tasks=len(recipes), visual_description_tasks=len(candidates) - len(recipes),
                evaluation_questions_or_images_used=False, original_files_modified=False,
                historical_expert_and_rights_flags_preserved=True,
                limitation='Only 8 targeted reading tasks; not a comprehensive OCR or independent field acceptance set.')
    with (output / 'dataset-card.json').open('x', encoding='utf-8') as f:
        json.dump(card, f, ensure_ascii=False, indent=2)
    return card


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--review', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    print(json.dumps(build(args.source, args.review, args.output), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
