"""Movable, single-instance local launcher. Does not install dependencies or change credentials."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
import psutil

ROOT = Path(__file__).resolve().parents[1]
URL = "http://127.0.0.1:8000"
INSTANCE = hashlib.sha256(str(ROOT).encode()).hexdigest()[:24]


def build_id():
    paths = sorted((ROOT / "quality").glob("*.py")) + sorted((ROOT / "frontend/dist").rglob("*"))
    result = hashlib.sha256()
    for path in paths:
        if path.is_file():
            result.update(str(path.relative_to(ROOT)).encode())
            result.update(path.read_bytes())
    return result.hexdigest()[:24]


def health():
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            URL + "/api/health", timeout=2
        ) as response:
            return json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return {}


def launch(open_browser=True):
    os.chdir(ROOT)
    data = ROOT / "var"
    data.mkdir(mode=0o700, exist_ok=True)
    with (data / "local-launch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = health()
        build = build_id()
        if state and (state.get("local_instance") != INSTANCE or state.get("local_build") != build):
            metadata = data / "local-server.json"
            try:
                saved = json.loads(metadata.read_text())
                process = psutil.Process(saved["pid"])
                owned = (
                    state.get("local_instance") == saved["instance"]
                    and Path(process.cwd()).resolve() == ROOT
                    and "quality.local_runtime" in process.cmdline()
                )
            except (OSError, KeyError, ValueError, psutil.Error):
                owned = False
            if not owned:
                raise RuntimeError("8000端口正在运行其他实例，请先由其所属程序关闭，启动器不会强行终止。")
            process.terminate()
            try:
                process.wait(timeout=15)
            except psutil.TimeoutExpired as exc:
                raise RuntimeError("原实例未完成优雅退出，未强制终止，请查看日志。") from exc
            state = {}
        if not state:
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", 8000))
                except OSError as exc:
                    raise RuntimeError("8000端口已占用且健康检查失败，请查看占用进程。") from exc
            python = ROOT / ".venv/bin/python"
            if not python.exists():
                raise RuntimeError("缺少 .venv/bin/python；请按 README 安装依赖后重试。")
            if not (ROOT / "frontend/dist/index.html").exists():
                raise RuntimeError("缺少前端构建，请先在 frontend 执行 pnpm build。")
            subprocess.run([str(python), "scripts/bootstrap_demo.py"], cwd=ROOT, check=True)
            env = {
                **os.environ,
                "QUALITY_LOCAL_INSTANCE": INSTANCE,
                "QUALITY_LOCAL_BUILD": build,
                "PYTHONUNBUFFERED": "1",
            }
            with (data / "local-server.log").open("ab") as log:
                process = subprocess.Popen(
                    [str(python), "-m", "quality.local_runtime"],
                    cwd=ROOT,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                    close_fds=True,
                )
            (data / "local-server.json").write_text(
                json.dumps({"pid": process.pid, "instance": INSTANCE, "build": build, "url": URL})
            )
            for _ in range(80):
                state = health()
                if state.get("local_instance") == INSTANCE and state.get("status") == "ready":
                    break
                if process.poll() is not None:
                    raise RuntimeError("服务启动失败，请查看 var/local-server.log。")
                time.sleep(0.25)
            else:
                raise RuntimeError("服务未在20秒内就绪，请查看 var/local-server.log。")
        if state.get("status") != "ready":
            raise RuntimeError("数据库未就绪，请查看 var/local-server.log。")
    if open_browser:
        webbrowser.open(URL)
    return {"url": URL, "status": "ready", "instance": INSTANCE}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(launch(not args.no_browser), ensure_ascii=False))
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
