"""Offline inventories. No framework import, download, unpickling or weight loading."""
import hashlib
import json
from pathlib import Path
import re


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def inventory(path, expected_type, hashes=False):
    path = Path(path).expanduser().resolve()
    config = json.loads((path / 'config.json').read_text())
    if config.get('model_type') != expected_type:
        raise ValueError('模型架构不符合指定基座，不能混用27B、9B与VL')
    required = ['config.json', 'tokenizer_config.json', 'tokenizer.json']
    template = next((x for x in ('chat_template.jinja', 'chat_template.json') if (path / x).is_file()), None)
    if template:
        required.append(template)
    elif not json.loads((path / 'tokenizer_config.json').read_text()).get('chat_template'):
        raise ValueError('缺少原生聊天模板')
    if expected_type == 'qwen3_vl':
        required.append('preprocessor_config.json')
    index = path / 'model.safetensors.index.json'
    if index.exists():
        required.append(index.name)
        weights = sorted(set(json.loads(index.read_text())['weight_map'].values()))
    else:
        weights = ['model.safetensors']
    required += weights
    files = {}
    for name in required:
        p = (path / name).resolve()
        if not p.is_relative_to(path) or not p.is_file() or p.stat().st_size <= 0:
            raise ValueError('模型文件不完整或路径不安全：' + name)
        files[name] = {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
        if hashes:
            files[name]['sha256'] = digest(p)
    return {'model_type': config['model_type'], 'architectures': config.get('architectures'),
            'path': str(path), 'files': files, 'hashes_computed': hashes,
            'integrity_scope': 'local_inventory; compare against trusted official manifest separately'}


def language_lora_targets(target_modules, shapes, rank):
    """PEFT accepts target suffix lists or regex strings; inspect saved tensors too."""
    if isinstance(target_modules, str):
        if not target_modules or len(target_modules) > 4096:
            raise ValueError('无效的LoRA目标表达式')
        try:
            expression = re.compile(target_modules)
        except re.error:
            raise ValueError('无效的LoRA目标表达式') from None
        matches = lambda module: expression.fullmatch(module) is not None
    elif (isinstance(target_modules, list) and target_modules and
          all(isinstance(value, str) and value and len(value) <= 1000 for value in target_modules)):
        if any('visual' in value or 'vision' in value for value in target_modules):
            raise ValueError('LoRA目标列表包含视觉模块')
        matches = lambda module: any(module == value or module.endswith('.'+value) for value in target_modules)
    else:
        raise ValueError('无效的LoRA目标模块')
    # A generic pattern must not silently include the independently preserved vision tower.
    if isinstance(target_modules, str):
        for prefix in ('visual.blocks.0.attn', 'model.visual.blocks.0.attn',
                       'model.vision_model.encoder.layers.0.self_attn', 'vision_tower.encoder.layers.0.self_attn'):
            if any(matches(prefix+'.'+projection) for projection in ('qkv','proj','q_proj','k_proj','v_proj','o_proj')):
                raise ValueError('LoRA目标表达式包含视觉模块')
    if type(rank) is not int or rank <= 0:
        raise ValueError('无效的LoRA秩')
    pairs = {}
    for name, shape in shapes.items():
        found = re.fullmatch(r'base_model\.model\.(.+)\.lora_([AB])(?:\.[\w-]+)?\.weight', name)
        if not found:
            raise ValueError('存在非预期的LoRA张量')
        module, side = found.groups()
        parts = module.split('.')
        language = (re.match(r'^(?:model\.)?(?:language_model|text_model)(?:\.model)?\.layers\.\d+\.', module)
                    or re.match(r'^(?:model\.)?layers\.\d+\.', module))
        if not language or any(part in ('visual','vision_model','vision_tower') for part in parts):
            raise ValueError('仅允许已核对的语言层LoRA张量')
        if not matches(module):
            raise ValueError('LoRA张量与目标模块配置不一致')
        if (not isinstance(shape, list) or len(shape) != 2 or
                any(type(size) is not int or size <= 0 for size in shape) or
                shape[0 if side == 'A' else 1] != rank):
            raise ValueError('LoRA维度与配置不一致')
        pair = pairs.setdefault(module, {})
        if side in pair:
            raise ValueError('重复的LoRA矩阵')
        pair[side] = shape
    if not pairs or any(set(pair) != {'A','B'} for pair in pairs.values()):
        raise ValueError('LoRA矩阵必须成对完整')
    return sorted(pairs)


def adapter_inventory(adapter, base, hashes=False):
    import importlib.util
    from .settings import ROOT
    path = ROOT / 'day1/4-validation/3-test-adapter.py'
    spec = importlib.util.spec_from_file_location('existing_adapter_validation', path)
    existing = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(existing)
    existing.inspect_adapter(Path(adapter).resolve(), Path(base).resolve())
    path = Path(adapter).resolve()
    config = json.loads((path / 'adapter_config.json').read_text())
    target_modules = config['target_modules']
    with (path / 'adapter_model.safetensors').open('rb') as source:
        size = int.from_bytes(source.read(8), 'little')
        if not 2 <= size <= 16 * 1024 * 1024:
            raise ValueError('无效的safetensors头部')
        tensors = json.loads(source.read(size))
    shapes = {k: v['shape'] for k, v in tensors.items() if k != '__metadata__'}
    modules = language_lora_targets(target_modules, shapes, config['r'])
    files = {}
    for p in sorted(path.iterdir()):
        if p.is_file():
            files[p.name] = {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns}
            if hashes:
                files[p.name]['sha256'] = digest(p)
    return {'path': str(path), 'r': config['r'], 'target_modules': target_modules, 'tensor_shapes': shapes,
            'verified_language_modules': modules,
            'files': files, 'hashes_computed': hashes}


def verify_receipt(settings):
    receipt = json.loads(settings.model_verification_file.read_text())
    if receipt.get('mode') != settings.text_mode or receipt.get('text_base') != str(settings.text_model_path.resolve()):
        raise ValueError('verification receipt mode/base mismatch')
    expected = settings.text_adapter_path if settings.text_mode == 'finetuned_lora' else settings.text_merged_model_path
    if receipt.get('artifact') != str(expected.resolve()):
        raise ValueError('verification artifact mismatch')
    for section in receipt['inventories']:
        for name, info in section['files'].items():
            path = Path(section['path']) / name
            if not info.get('sha256') or path.stat().st_size != info['bytes'] or path.stat().st_mtime_ns != info['mtime_ns']:
                raise ValueError('verified file changed')
    if not receipt.get('inventories'):
        raise ValueError('empty verification receipt')
    if settings.text_mode == 'finetuned_lora':
        # Offline checks alone cannot establish an active runtime adapter.
        # Only permit service after an operator provides the paired GPU evidence generated by the existing validator.
        evidence_path = Path(receipt['runtime_evidence'])
        if digest(evidence_path) != receipt['runtime_evidence_sha256']:
            raise ValueError('runtime evidence changed')
        evidence = json.loads(evidence_path.read_text())
        if not evidence.get('active_adapters') or not evidence.get('gpu'):
            raise ValueError('runtime adapter activation not proven')
    return receipt
