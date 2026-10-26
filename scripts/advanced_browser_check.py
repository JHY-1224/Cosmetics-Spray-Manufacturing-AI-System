"""Run browser rollout/tasks checks against isolated local center/site processes."""

import json
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from quality.app import create_app
from quality.cli import initialize, migrate
from quality.config import Settings
from quality.db import Database
from quality.security import token
from quality.sync import public_key, sync_once
from scripts.multisite_check import free_port, launch, post

parser = argparse.ArgumentParser()
parser.add_argument("--frontend-dir", type=Path, help="当前隔离构建目录，避免使用旧dist或覆盖运行中前端")
args = parser.parse_args()
root = Path(tempfile.mkdtemp(prefix="quality-browser-"))
center_port, site_port = free_port(), free_port()
center_url, site_url = f"http://127.0.0.1:{center_port}", f"http://127.0.0.1:{site_port}"
password = token()
center = Settings(
    _env_file=None,
    mode="integration",
    tenant_id="browser-reference",
    site_id="factory-a",
    node_role="center",
    node_id="center",
    package_secret=token(),
    trusted_origins=[center_url],
    data_dir=root / "center",
    database_url=f"sqlite:///{root / 'center.db'}",
)
migrate(center)
initialize(center, "center-admin", password, ["factory-a"])
center_client = TestClient(create_app(center))
post(center_client, "/auth/login", {"username": "center-admin", "password": password})
engineer = TestClient(create_app(center))
post(
    center_client,
    "/users",
    {
        "username": "engineer",
        "password": password,
        "role": "engineer",
        "allowed_sites": ["factory-a"],
        "allowed_lines": [],
    },
)
post(engineer, "/auth/login", {"username": "engineer", "password": password})
title = "浏览器签名发布参考知识"
knowledge = post(
    engineer,
    "/knowledge",
    {
        "category": "sop",
        "title": title,
        "content": "独立浏览器测试资料，不作为正式生产参数",
        "process": "plastic_spray",
        "source": {"provenance": "reference"},
    },
)
post(engineer, f"/knowledge/{knowledge['id']}/review", {"action": "submit", "reason": "提交独立审核验证"})
post(
    center_client,
    f"/knowledge/{knowledge['id']}/review",
    {"action": "publish", "reason": "通过浏览器软件测试审核"},
)
registered = post(
    center_client, "/sites", {"node_id": "browser-site", "site_id": "factory-a", "name": "浏览器独立站点"}
)
site = center.model_copy(
    update={
        "node_role": "site",
        "node_id": "browser-site",
        "package_secret": "",
        "package_public_key": public_key(center),
        "data_dir": root / "site",
        "database_url": f"sqlite:///{root / 'site.db'}",
        "center_url": center_url,
        "sync_token": registered["token"],
        "trusted_origins": [site_url],
    }
)
migrate(site)
initialize(site, "site-admin", password)
site_client = TestClient(create_app(site))
post(site_client, "/auth/login", {"username": "site-admin", "password": password})
line = post(
    site_client,
    "/masters",
    {
        "kind": "line",
        "code": "BROWSER-LINE",
        "name": "浏览器独立验证线",
        "data": {"process": "plastic_spray", "provenance": "reference"},
    },
)
evidence_name = "浏览器补证记录.txt"
response = site_client.post(
    "/api/files",
    files={"file": (evidence_name, b"Evidence task reference data", "text/plain")},
    headers={"Idempotency-Key": token()},
)
response.raise_for_status()
case_title = "浏览器补证任务核验"
post(
    site_client,
    "/cases",
    {
        "title": case_title,
        "line_id": line["id"],
        "process": "plastic_spray",
        "defect": "颗粒",
        "evidence_ids": [response.json()["id"]],
    },
)
fixture = root / "fixture.json"
fixture.write_text(
    json.dumps(
        {
            "center_url": center_url,
            "site_url": site_url,
            "password": password,
            "knowledge_title": title,
            "case_title": case_title,
            "evidence_name": evidence_name,
            "line_name": line["name"],
        }
    )
)
fixture.chmod(0o600)
processes = []
stopped = threading.Event()


def worker():
    while not stopped.wait(0.4):
        try:
            sync_once(site, Database(site))
        except Exception:
            if not stopped.is_set():
                time.sleep(0.2)


try:
    processes.append(launch(center, center_port, root, args.frontend_dir))
    processes.append(launch(site, site_port, root, args.frontend_dir))
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    env = {**os.environ, "QUALITY_ADVANCED_FIXTURE": str(fixture), "QUALITY_BROWSER_SUITE": "advanced"}
    result = subprocess.run(
        ["node", "node_modules/@playwright/test/cli.js", "test", "--config=playwright.config.ts"],
        cwd="frontend",
        env=env,
    )
    raise SystemExit(result.returncode)
finally:
    stopped.set()
    for process in processes:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
