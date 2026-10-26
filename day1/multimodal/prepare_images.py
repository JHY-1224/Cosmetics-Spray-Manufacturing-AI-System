"""Create a new portable training package from the 74 curated originals.

Originals and their historical approval flags are never modified. All captions are
development labels, not expert-verified defect/causality labels. Public export is
a subset of this private training package, not a replacement for it.
"""
import argparse
from collections import Counter
import hashlib
import html
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]
EXTRA = {
 'S16-P035': [
  ('请读取正文中水性玻璃漆的三组分及调配比例，说明是否给出了计量基准。',
   '正文写明：水性玻璃烘烤涂料：色浆：水 = 10:1:2。该段未明确质量比或体积比，不能擅自改写为质量百分比。这是该来源项目的说明，不是客户批准的生产配方。'),
  ('表2-8中2#和3#无尘涂装自动线分别是多少产能、单个喷涂面积和总喷涂面积？请保留单位。',
   '2#无尘涂装自动线：4158万个/年，0.0024 m²/个，总喷涂面积99792 m²；3#无尘涂装自动线：2772万个/年，0.0024 m²/个，总喷涂面积66528 m²。两行对象均为化妆品玻璃瓶外喷。这是设计核算表，不是现场实测记录。'),
  ('表2-9在这一页是否完整？能否把缺少的色浆和水的物性补全？',
   '不完整。这一页只显示表头及水性玻璃烘烤涂料一行，续页内容不在本图中。不能凭比例猜测色浆和水的密度、固体份或VOCs数据，应查看同表续页。'),
  ('表2-8中的5#内喷线和6#内喷线分别对应什么数值？',
   '两行都是玻璃瓶内喷：5#内喷线为3465万个/年、0.0018 m²/个、62370 m²；6#内喷线也是3465万个/年、0.0018 m²/个、62370 m²。须保留行号，不能把两行混成一次生产实测。')],
 'S30-P040': [
  ('读取表2-8中UV面漆这一行列出的五类主要成分及比例，是否合计100%？',
   'UV面漆一行列出：聚氨酯丙烯酸酯50%、环氧丙烯酸酯24%、单体20%、光引发剂5%、BYK助剂1%。五项相加为100%。这只是对原图组成说明的读取与算术核对，不能由此推断原料批次、配制顺序或批准生产配方。'),
  ('读取水性底漆这一行的主要成分范围和密度。',
   '水性底漆：水性聚氨酯丙烯酸树脂35～45%；水性丙烯酸树脂3～6%；2-羟基-2-甲基-1苯基-1-丙酮2～5%；二丙二醇甲醚3～6%；水45～50%。密度为1.050 g/cm³。范围不能任选上限凑成配方，具体组成仍应核对供应商资料。'),
  ('根据页首备注，这个项目需要在外购成品油漆中另加助剂调漆吗？',
   '页首备注说明，该项目外购成品油漆进行喷涂，无需再添加助剂等进行调漆。不能把表中的组成说明转成追加助剂的操作指令，也不能将这个项目的说明推广到所有涂料。'),
  ('UV底漆和UV面漆两行的VOCs检测结果分别是什么？',
   '图中UV底漆一行写VOCs检测结果156 g/L，折算VOC占比约14.3%；UV面漆一行写93 g/L，折算VOC占比约10.3%。这是来源文件对检测报告的转述，不是本项目某批材料的新检测结果。')]
}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=ROOT / '化妆品工艺图像采集包_20261026')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--public-only', action='store_true')
    args = p.parse_args()
    source, output = args.source.resolve(), args.output.resolve()
    if output.exists():
        raise ValueError('Output already exists; refusing to overwrite a reviewed package')
    assets = [json.loads(l) for l in (source / '清单/入选图片清单.jsonl').read_text().splitlines() if l.strip()]
    if args.public_only:
        assets = [a for a in assets if a['rights_status'] == 'CC_BY_4.0_declared']
    # Check every source before writing the new package.
    for a in assets:
        path = (source / a['local_path']).resolve()
        if not path.is_relative_to(source) or hashlib.sha256(path.read_bytes()).hexdigest() != a['sha256']:
            raise ValueError('Original missing or changed: ' + a['asset_id'])
    (output / 'images').mkdir(parents=True)
    rows = []
    for a in assets:
        name = a['asset_id'] + Path(a['local_path']).suffix.lower()
        shutil.copy2(source / a['local_path'], output / 'images' / name)
        base = {'source_group': 'cosmetic-glass-thread-team' if a['source_id'] in ('S11','S12') else a['source_group'],
                'split': 'train', 'expert_reviewed': False, 'source_url': a['source_url'],
                'rights_status': a['rights_status'], 'stages': a['stages'], 'asset_kind': a['asset_kind'],
                'file_name': 'images/' + name,
                'images': [{'path': 'images/' + name, 'sha256': a['sha256']}],
                'label_basis': 'existing_visual_curation_and_source_context; not exhaustive OCR',
                'field_record_verified': False}
        rows.append({**base, 'id': a['asset_id'] + '-context',
            'question': '请识别这份工艺资料的对象或主题，并说明它不能证明哪些现场事实。',
            'answer': a['caption_cn'] + '。资料边界：' + a['limitations']})
        if not args.public_only:
            for i, (question, answer) in enumerate(EXTRA.get(a['asset_id'], []), 1):
                rows.append({**base, 'id': a['asset_id'] + '-read-' + str(i),
                             'label_basis': 'assistant_checked_page_pixels_and_original_text_layer',
                             'question': question, 'answer': answer})
    (output / 'metadata.jsonl').write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
    report = {'images': len(assets), 'samples': len(rows), 'context_samples': len(assets),
              'specific_reading_samples': len(rows)-len(assets), 'source_groups': len({r['source_group'] for r in rows}),
              'train_rows': len(rows), 'validation_rows': 0, 'test_rows': 0,
              'expert_reviewed': False, 'public_subset': args.public_only,
              'rights': dict(Counter(a['rights_status'] for a in assets)),
              'limitations': ['small development SFT set; no independent efficacy claim',
                             '74 contextual labels do not equal exhaustive table transcription',
                             'no real customer batch, defect cause or before/after validation labels'],
              'metadata_sha256': hashlib.sha256((output / 'metadata.jsonl').read_bytes()).hexdigest()}
    (output / 'dataset-card.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
    attribution = []
    import re
    for ident in ('S11','S12'):
        if not any(a['source_id']==ident for a in assets): continue
        raw = (source / '来源原件' / (ident + '.html')).read_text()
        def meta(key):
            return [html.unescape(v) for v in re.findall(r'<meta name="'+key+r'" content="([^"]+)"',raw)]
        attribution.append({'source_id':ident,'title':meta('citation_title'),'authors':meta('citation_author'),
                            'doi':meta('citation_doi'),'license':'CC BY 4.0',
                            'license_url':'https://creativecommons.org/licenses/by/4.0/',
                            'changes':'Original figure pixels retained; Chinese contextual Q&A added; not expert reviewed.'})
    (output / 'ATTRIBUTION.json').write_text(json.dumps(attribution, ensure_ascii=False, indent=2))
    cards = ''.join('<article><h3>'+html.escape(r['id'])+'</h3><img loading="lazy" src="'+r['images'][0]['path']+'"><p>'+html.escape(r['question'])+'</p><pre>'+html.escape(r['answer'])+'</pre><a href="'+html.escape(r['source_url'],quote=True)+'">来源</a></article>' for r in rows)
    (output / 'review.html').write_text('<!doctype html><meta charset="utf-8"><title>8B图文训练样本审核</title><style>body{max-width:1100px;margin:32px auto;font:16px sans-serif}article{border-top:1px solid #ddd;padding:24px}img{max-width:100%;max-height:850px}pre{white-space:pre-wrap;line-height:1.6}</style><h1>8B图文训练样本</h1><p>开发标签，未经过领域专家审核；来源相同的图片和衍生问答同组，不作为独立评测。共 '+str(len(assets))+' 张图、'+str(len(rows))+' 条问答。</p>'+cards)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
