"""Explicit public snapshot. Originals, runtime data and weights are never moved."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = 'qwen-vl-full-20261026.tar.gz'
BUNDLE_SHA256 = '7baeb891d8da0d852db5822d1bac242d1ee7c3904c17143cd3024554621fadcf'
DIRECTORIES = ('quality','frontend','assistant_app','migrations','deploy','schemas','scripts','tests','day1','day2','docs',
               'data/vision-public','data/text-training','data/evaluation-inputs','evaluation','models/releases')
ROOT_FILES = ('.env.example','.dockerignore','.gitignore','Dockerfile','requirements.txt','pyproject.toml','alembic.ini','LICENSE','AGENTS.md')
SKIP = {'node_modules','__pycache__','dist','test-results','playwright-report','.pytest_cache','.ruff_cache','outputs','dataset','.data','.git','configs-private','local-only'}
SUFFIXES = {'.py','.sh','.toml','.lock','.json','.jsonl','.yaml','.yml','.ts','.tsx','.mjs','.mts','.js','.css','.html','.md','.txt','.svg','.csv','.sql','.example','.command','.conf','.node'}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--licensed-data-only', action='store_true',
                   help='Omit the legacy full image archive whose extra54 images lack verified redistribution rights')
    args=p.parse_args()
    output=args.output.resolve()
    if output==ROOT or ROOT.is_relative_to(output): raise ValueError('Output must be a new child or independent directory')
    output.mkdir(parents=True,exist_ok=False)
    def copy(path,relative=None):
        target=output/(relative or path.relative_to(ROOT))
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,target)
    for directory in DIRECTORIES:
        for folder,dirs,names in os.walk(ROOT/directory,followlinks=False):
            dirs[:]=[n for n in dirs if n not in SKIP and not n.startswith('.venv') and not (Path(folder)/n).is_symlink()]
            for name in sorted(names):
                path=Path(folder)/name
                if path.is_symlink() or name.endswith('.tsbuildinfo'): continue
                if name.startswith('.env') and name!='.env.example': continue
                if path.suffix=='.env' and '.example.' not in name: continue
                if 'model_profiles' in path.parts and path.suffix=='.json': continue
                image=directory=='data/vision-public' and path.suffix.lower() in ('.png','.jpg','.jpeg','.webp')
                special=name in ('Caddyfile','.npmrc','.gitignore','Dockerfile') or name.endswith('.example.env')
                evaluation_archive = directory=='evaluation' and name in (
                    'evaluation-kit-20261026.tar.gz','evaluation-kit-20261026.tar.gz.sha256')
                if not (path.suffix in SUFFIXES or image or special or evaluation_archive): continue
                if path.stat().st_size>25*1024*1024: raise ValueError('Unexpected large source: '+str(path.relative_to(ROOT)))
                copy(path)
    for name in ROOT_FILES: copy(ROOT/name)
    # Explicitly requested training bundle; do not include arbitrary local archives.
    if not args.licensed_data_only:
        if digest(ROOT/BUNDLE) != BUNDLE_SHA256:
            raise ValueError('Training bundle differs from the approved snapshot')
        copy(ROOT/BUNDLE)
        copy(ROOT/(BUNDLE+'.sha256'))
    copy(ROOT/'README.md',Path('docs/PROJECT_MANUAL.md'))
    copy(ROOT/'docs/PUBLIC_README.md',Path('README.md'))
    copy(ROOT/'docs/DATA_LICENSE.md',Path('DATA_LICENSE.md'))
    copy(ROOT/'docs/MULTIMODAL_TRAINING.md',Path('TRAINING_ARCHITECTURE.md'))
    for name in ('Qwen3.5-9B','Qwen3-VL-8B-Instruct'):
        copy(ROOT/'models'/(name+'.manifest.json'))
    (output/'models/README.md').write_text('# 模型位置\n\n基座不随Git发布，官方链接与冻结清单见../docs/FINETUNED_RELEASE_20261028.md。自训LoRA参数通过Gitee发行版附件分发，哈希与分片清单在releases/；不是只提供云端路径。原云端基座位于/root/autodl-tmp/models/Qwen3.5-9B和Qwen3-VL-8B-Instruct；原微调产物在/root/autodl-tmp/models/services/qwen-light。恢复步骤见微调后交付文档；Mac本地models/base仅供示范测试。\n')
    # Source links and local-only history are retained, but remove workstation user paths in prose.
    for path in output.rglob('*.md'):
        value=path.read_text()
        value=value.replace(str(ROOT),'${LOCAL_WORKSPACE}').replace('/Users/jianghanyu/','${LOCAL_HOME}/')
        path.write_text(value)
    manual=output/'docs/PROJECT_MANUAL.md'
    manual.write_text('> 原工业平台完整业务说明与历史验证记录。当前云端训练/服务以AGENTS.md第37节及CLOUD_QUICKSTART.md为准；历史报告、客户配置与本机原件未公开。\n\n'+manual.read_text())
    index=['# 源码定义与入口索引','', '由本次公开副本AST生成。详细业务约束见AGENTS.md；索引不替代函数实现。','']
    for path in sorted(output.rglob('*.py')):
        relative=path.relative_to(output)
        if 'tests' in relative.parts: continue
        tree=ast.parse(path.read_text())
        entries=[]
        for node in ast.walk(tree):
            if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                entries.append((node.lineno,node.name))
        if entries:
            index+=['## '+str(relative),'']+[f'- L{line}: `{name}`' for line,name in sorted(entries)]+['']
    (output/'docs/SOURCE_INDEX.md').write_text('\n'.join(index))
    files=[{'path':str(p.relative_to(output)),'bytes':p.stat().st_size,'sha256':digest(p)} for p in sorted(output.rglob('*')) if p.is_file()]
    report={'project':'化妆品喷涂制造工艺AI智能系统','files':files,'public_source_files':len(files),
            'source_bytes':sum(f['bytes'] for f in files),'models':'official manifests only; cloud weights external',
            'data':{'text_rows':36,'cc_by_subset_images':20,'cc_by_subset_rows':20,
                    'bundled_full_images':0 if args.licensed_data_only else 74,
                    'bundled_full_rows':0 if args.licensed_data_only else 82,
                    'rights_unverified_images_excluded':54 if args.licensed_data_only else 0,
                    'bundle':None if args.licensed_data_only else BUNDLE},
            'excluded':['weights and adapters','credentials and private runtime','customer configurations','meeting documents','unpacked local_training and other unselected data','environments and caches','historical reports'],
            'gpu_validation':'not performed on this Mac; target-cloud status in docs/RELEASE_STATUS.md',
            'candidate_text_training':{'rows':456,'parameter_holdout_rows':96,
                                       'quality_scope':'See docs/FINETUNED_RELEASE_20261028.md;96-case correlated development regression is not field accuracy'},
            'evaluation_archive_sha256':'8b1ec491b099ae91383918a3c46ebbcd3124823e6a3d59362a0dcff0ddb6a399'}
    (output/'PUBLIC_RELEASE_MANIFEST.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps({'output':str(output),'files':len(files),'bytes':report['source_bytes']},ensure_ascii=False))


if __name__=='__main__': main()
