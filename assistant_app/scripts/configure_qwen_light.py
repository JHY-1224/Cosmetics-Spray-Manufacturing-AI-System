"""Register two independently trained components as ONE assistant choice.

Only writes new operator configuration. Does not move weights, generate verification
evidence, load a GPU, install dependencies or change the current Aliyun connection.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--text-adapter', type=Path, required=True)
    p.add_argument('--vision-adapter', type=Path, required=True)
    p.add_argument('--model-root', type=Path, default=Path('/root/autodl-tmp/models'))
    p.add_argument('--service-dir', type=Path, default=Path('/root/autodl-tmp/models/services/qwen-light'))
    p.add_argument('--profiles-dir', type=Path, default=ROOT / 'assistant_app/configs/model_profiles')
    args = p.parse_args()
    service = args.service_dir.expanduser().resolve()
    profile_path = args.profiles_dir.resolve() / 'qwen-light.json'
    env_path = service / 'service.env'
    if env_path.exists() or profile_path.exists():
        raise SystemExit('Configuration exists; review/version the existing file rather than overwrite')
    root = args.model_root.resolve()
    text, vision = args.text_adapter.resolve(), args.vision_adapter.resolve()
    for adapter, kind in ((text,'qwen3_5'),(vision,'qwen3_vl')):
        if not (adapter/'adapter_config.json').is_file() or not (adapter/'adapter_model.safetensors').is_file():
            raise SystemExit('A trained adapter is required: ' + str(adapter))
        recorded = json.loads((adapter/'adapter_config.json').read_text())['base_model_name_or_path']
        expected = root / ('Qwen3.5-9B' if kind=='qwen3_5' else 'Qwen3-VL-8B-Instruct')
        if Path(recorded).resolve() != expected:
            raise SystemExit('Adapter/base path mismatch: ' + str(adapter))
    profile = {'id':'qwen-light','name':'Qwen轻量级服务','category':'local_finetuned',
        'text_model_base_url':'http://127.0.0.1:5101/v1','text_served_model':'qwen-light-text',
        'text_model_path':str(root/'Qwen3.5-9B'),'text_adapter_path':str(text),
        'model_verification_file':str(service/'text-verification.json'),
        'vision_model_base_url':'http://127.0.0.1:5102/v1','vision_served_model':'qwen-light-vision',
        'vision_model_path':str(root/'Qwen3-VL-8B-Instruct'),'vision_adapter_path':str(vision),
        'vision_mode':'finetuned_lora','vision_verification_file':str(service/'vision-verification.json')}
    env = {key.upper():value for key,value in profile.items()
           if key not in ('id','name','category')}
    env.update(TEXT_MODE='finetuned_lora',ALLOW_BASE_PREVIEW='false',
               MODEL_DIRECTORY=str(root),MODEL_TIMEOUT='180',REQUEST_TIMEOUT='420')
    # dotenv values are quoted; this file is not a shell script and must not be sourced.
    service.mkdir(parents=True,exist_ok=True)
    profile_path.parent.mkdir(parents=True,exist_ok=True)
    with env_path.open('x') as f:
        for key,value in env.items():
            f.write('ASSISTANT_'+key+'='+json.dumps(value,ensure_ascii=False)+'\n')
    env_path.chmod(0o600)
    with profile_path.open('x') as f: json.dump(profile,f,ensure_ascii=False,indent=2)
    print(json.dumps({'profile':str(profile_path),'service_env':str(env_path),
                      'state':'registered; both verification receipts and live endpoints still required'},ensure_ascii=False))


if __name__ == '__main__': main()
