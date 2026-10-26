import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from assistant_app.backend.settings import Settings
import psutil


def owned_process(settings):
    receipt = settings.data_dir / 'process.json'
    if not receipt.exists():
        return None
    state = json.loads(receipt.read_text())
    try:
        process = psutil.Process(state['pid'])
        command_paths = [str((Path(process.cwd()) / arg).resolve()) for arg in process.cmdline() if not arg.startswith('-')]
        if (abs(process.create_time() - state['create_time']) > .01
                or state['script'] not in command_paths
                or Path(state['script']).resolve() != Path(__file__).with_name('run_assistant.py').resolve()):
            return None
        return process
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None


def settings_from_args():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path, default=Path(__file__).resolve().parents[1] / '.env')
    args = p.parse_args()
    if args.env_file.is_file():
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    return Settings()


if __name__ == '__main__':
    settings = settings_from_args()
    process = owned_process(settings)
    print(json.dumps({'running': process is not None, 'pid': process.pid if process else None,
                      'port': settings.port}, ensure_ascii=False))
