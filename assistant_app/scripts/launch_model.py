"""Explicit operator entry in an existing GPU environment, never installs/downloads."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from assistant_app.backend.settings import Settings
from assistant_app.backend.model_inventory import inventory, adapter_inventory


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('kind', choices=['text', 'vision'])
    parser.add_argument('--env-file', type=Path)
    args = parser.parse_args()
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    s, kind = Settings(), args.kind
    from assistant_app.backend.model_clients import vision_as_text
    component = s if kind == 'text' else vision_as_text(s)
    if (s.text_mode == 'approved_cloud' if kind == 'text' else s.vision_mode == 'approved_cloud'):
        raise SystemExit('当前选择云端模型，无需启动本地GPU服务；切回本地时请提供单独的本地配置文件')
    if not shutil.which('nvidia-smi'):
        raise SystemExit('此入口需要云端NVIDIA环境；本机Web无需启动GPU模型')
    if not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise SystemExit('请显式设置CUDA_VISIBLE_DEVICES；不会选择或清理未知GPU进程')
    prefix = 'ASSISTANT_' + kind.upper()
    fraction = float(os.environ.get(prefix + '_GPU_MEMORY_UTILIZATION', '0'))
    if not 0 < fraction <= .9:
        raise SystemExit('请显式设置每个模型的_GPU_MEMORY_UTILIZATION；同卡预算之和须留有余量')
    subprocess.run(['nvidia-smi'], check=True)
    versions = {}
    for name in ('vllm', 'torch', 'transformers'):
        versions[name] = importlib.metadata.version(name)
    print(json.dumps({'versions': versions, 'kind': kind, 'gpu_budget': fraction}, ensure_ascii=False), flush=True)
    path = s.text_model_path if kind == 'text' else s.vision_model_path
    port = int(os.environ.get(prefix + '_PORT', '5101' if kind == 'text' else '5102'))
    served = s.text_served_model if kind == 'text' else s.vision_served_model
    extra = []
    inventory(component.text_model_path, 'qwen3_5' if kind == 'text' else 'qwen3_vl')
    if kind == 'text':
        extra += ['--language-model-only']
    else:
        extra += ['--limit-mm-per-prompt', '{"image":1,"video":0}']
    if component.text_mode == 'finetuned_merged':
        if not component.text_merged_model_path:
            raise SystemExit(kind + '_merged_missing')
        path = component.text_merged_model_path
    elif component.text_mode == 'finetuned_lora':
        if not component.text_adapter_path:
            raise SystemExit(kind + '_adapter_missing；不会静默回退基座')
        info = adapter_inventory(component.text_adapter_path, component.text_model_path)
        if os.environ.get('ASSISTANT_VLLM_LORA_VALIDATED') != '1':
            raise SystemExit('须先在当前vLLM/模型/量化组合核对语言LoRA支持；未授权假定兼容')
        base_alias = served + '-base'
        extra += ['--enable-lora', '--max-lora-rank', str(info['r']), '--lora-modules',
                  json.dumps({'name': served, 'path': str(component.text_adapter_path), 'base_model_name': base_alias})]
        served = base_alias
    quantization = os.environ.get(prefix + '_QUANTIZATION', '')
    if quantization:
        if quantization not in ('bitsandbytes', 'awq', 'gptq'):
            raise SystemExit('Unsupported explicit quantization')
        extra += ['--quantization', quantization]
    inventory(path, 'qwen3_5' if kind == 'text' else 'qwen3_vl')
    with socket.socket() as check:
        check.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            check.bind(('127.0.0.1', port))
        except OSError:
            raise SystemExit(f'端口{port}已占用，未停止任何进程') from None
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    argv = [sys.executable, '-m', 'vllm.entrypoints.cli.main', 'serve', str(path), '--host', '127.0.0.1',
            '--port', str(port), '--served-model-name', served,
            '--gpu-memory-utilization', str(fraction), '--max-model-len', os.environ.get(prefix + '_MAX_MODEL_LEN', '8192'),
            '--max-num-seqs', '1', '--default-chat-template-kwargs', '{"enable_thinking":false}', *extra]
    print('只启动此模型进程；Ctrl+C正常停止，不操作其他进程。', flush=True)
    os.execv(sys.executable, argv)


if __name__ == '__main__':
    main()
