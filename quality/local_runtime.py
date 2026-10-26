"""Loopback-only development runtime; customer deployment uses separate API/workers."""

import threading
import uvicorn
from .config import Settings
from .worker import run

if __name__ == "__main__":
    settings = Settings()
    if settings.mode == "production":
        raise SystemExit("生产请使用正式部署流程；本地启动器仅用于本机演示/集成。")
    stopping = threading.Event()
    worker = threading.Thread(target=run, args=(settings, stopping), daemon=True, name="local-worker")
    worker.start()
    try:
        uvicorn.run("quality.app:app", host="127.0.0.1", port=8000, log_level="warning")
    finally:
        stopping.set()
        worker.join(timeout=15)
