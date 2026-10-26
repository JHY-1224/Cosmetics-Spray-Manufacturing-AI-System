"""Foreground supervisor for the paired vLLM components; stops only its own children.

The existing assistant/FastAPI process remains the single public service. Run this
in the existing vLLM environment; run_assistant.py can use its separate web env.
"""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--env-file', type=Path, required=True)
    args = p.parse_args()
    if not args.env_file.is_file():
        raise SystemExit('Missing paired service environment')
    from dotenv import load_dotenv
    load_dotenv(args.env_file, override=False)
    if not os.environ.get('CUDA_VISIBLE_DEVICES'):
        raise SystemExit('Set CUDA_VISIBLE_DEVICES explicitly')
    fractions = [float(os.environ.get('ASSISTANT_'+role+'_GPU_MEMORY_UTILIZATION','0')) for role in ('TEXT','VISION')]
    if not all(0<f<=.9 for f in fractions) or sum(fractions)>.9:
        raise SystemExit('Explicit GPU memory fractions must be positive and sum <= 0.9')
    children=[]
    def stop(signum, frame): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,stop)
    try:
        for role in ('text','vision'):
            children.append(subprocess.Popen([sys.executable,str(Path(__file__).with_name('launch_model.py')),
                role,'--env-file',str(args.env_file.resolve())],start_new_session=True))
        while True:
            for child in children:
                code=child.poll()
                if code is not None: raise SystemExit(code or 1)
            time.sleep(.5)
    except KeyboardInterrupt:
        pass
    finally:
        for child in children:
            if child.poll() is None: os.killpg(child.pid,signal.SIGTERM)
        for child in children:
            try: child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid,signal.SIGKILL)
                child.wait()


if __name__ == '__main__': main()
