"""Two customers, each with a real center and two isolated site OS processes.

Uses local SQLite to exercise transport/authority/offline behavior; PostgreSQL RLS
and checkpoint persistence are tested separately in test_postgres.py.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quality.cli import initialize, migrate, seed_demo
from quality.config import Settings
from quality.db import Database
from quality.sync import sync_once
from quality.security import token


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def launch(settings, port, root, frontend_dir=None):
    env = os.environ.copy()
    env.update(
        {
            "QUALITY_" + k.upper(): (json.dumps(v) if isinstance(v, (dict, list, bool)) else str(v))
            for k, v in settings.model_dump(mode="json").items()
        }
    )
    log = (root / f"{settings.node_id}.log").open("a")
    launch_command = [
            sys.executable,
            "-m",
            "uvicorn",
            "quality.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ]
    if frontend_dir is not None:
        frontend_dir = Path(frontend_dir).resolve()
        if not (frontend_dir / "index.html").is_file():
            raise ValueError("隔离前端构建缺少index.html")
        launch_command = [sys.executable, "-c", "import uvicorn; from quality.app import create_app; " +
                          f"uvicorn.run(create_app(frontend_dir={str(frontend_dir)!r}), host='127.0.0.1', port={port}, log_level='warning')"]
    process = subprocess.Popen(
        launch_command,
        env=env,
        stdout=log,
        stderr=log,
    )
    for _ in range(100):
        if process.poll() is not None:
            raise RuntimeError(f"{settings.node_id} exited, see {log.name}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/api/health").status_code == 200:
                return process
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    process.terminate()
    raise TimeoutError("node_startup")


def post(client, path, body):
    headers = {"Idempotency-Key": token()}
    if client.cookies.get("quality_csrf"):
        headers["X-CSRF-Token"] = client.cookies["quality_csrf"]
    res = client.post("/api" + path, json=body, headers=headers)
    res.raise_for_status()
    if client.cookies.get("quality_csrf"):
        client.headers["X-CSRF-Token"] = client.cookies["quality_csrf"]
    return res.json()


def check_customer(customer, root, browser_probe=False, frontend_dir=None):
    password = token()
    center_port = free_port()
    center = Settings(
        _env_file=None,
        mode="integration",
        tenant_id=customer,
        node_role="center",
        node_id="center",
        site_id="",
        data_dir=root / "center",
        database_url=f"sqlite:///{root / 'center.db'}",
        package_secret=token(),
    )
    migrate(center)
    initialize(center, "admin", password, ["factory-a", "factory-b"])
    center_process = launch(center, center_port, root, frontend_dir)
    processes = [center_process]
    center_client = httpx.Client(base_url=f"http://127.0.0.1:{center_port}", timeout=15)
    post(center_client, "/auth/login", {"username": "admin", "password": password})
    sites = []
    try:
        employee = post(
            center_client,
            "/users",
            {
                "username": "central-engineer",
                "password": password,
                "role": "engineer",
                "allowed_sites": ["factory-a"],
                "allowed_lines": [],
            },
        )
        for factory in ("a", "b"):
            port = free_port()
            registration = post(
                center_client,
                "/sites",
                {
                    "node_id": f"site-{factory}",
                    "site_id": f"factory-{factory}",
                    "name": f"{customer}参考工厂{factory}",
                    "base_url": f"http://127.0.0.1:{port}",
                },
            )
            site = Settings(
                _env_file=None,
                mode="integration",
                tenant_id=customer,
                node_id=f"site-{factory}",
                site_id=f"factory-{factory}",
                data_dir=root / factory,
                database_url=f"sqlite:///{root / (factory + '.db')}",
                center_url=f"http://127.0.0.1:{center_port}",
                sync_token=registration["token"],
                package_public_key=registration["package_public_key"],
            )
            migrate(site)
            initialize(site, "admin", password)
            seed_demo(site)
            process = launch(site, port, root, frontend_dir)
            processes.append(process)
            client = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=15)
            post(client, "/auth/login", {"username": "admin", "password": password})
            lines = client.get("/api/masters?kind=line").json()
            assert len(lines) == 2
            sites.append((site, client, lines))
        sync_once(sites[0][0], Database(sites[0][0]))
        sync_once(sites[1][0], Database(sites[1][0]))
        employee_client = httpx.Client(base_url=str(sites[0][1].base_url), timeout=15)
        post(employee_client, "/auth/login", {"username": "central-engineer", "password": password})
        assert (
            sites[1][1]
            .post("/api/auth/login", json={"username": "central-engineer", "password": password})
            .status_code
            == 401
        )
        # A true transport outage: stop only the center process we just created.
        center_process.terminate()
        center_process.wait(timeout=5)
        assert employee_client.get("/api/auth/me").status_code == 200
        cases = []
        for site, client, lines in sites:
            for line in lines:
                c = post(
                    client,
                    "/cases",
                    {
                        "title": f"{site.site_id}断网人工异常",
                        "line_id": line["id"],
                        "process": line["data"]["process"],
                        "defect": "颗粒",
                    },
                )
                c = post(client, f"/cases/{c['id']}/diagnose", {"version": c["version"]})
                p = post(
                    client,
                    f"/cases/{c['id']}/plans",
                    {
                        "version": c["version"],
                        "actions": [{"type": "inspect", "description": "断网期间现场人工核对记录"}],
                        "retest": {
                            "method": "合成数据软件闭环验证",
                            "target_defects": ["颗粒"],
                            "sample_unit": "bottle",
                            "min_samples": 2,
                            "max_defect_rate": 0,
                            "stabilization_minutes": 0,
                            "observation_minutes": 0,
                        },
                    },
                )
                credentials = json.loads((site.data_dir / "demo-credentials.json").read_text())
                role_clients = {}
                try:
                    for role in ("approver", "executor", "quality"):
                        rc = httpx.Client(base_url=str(client.base_url), timeout=15)
                        post(rc, "/auth/login", {"username": role, "password": credentials[role]})
                        role_clients[role] = rc
                    c = post(
                        role_clients["approver"],
                        f"/cases/{c['id']}/approve",
                        {
                            "version": p["case"]["version"],
                            "plan_id": p["plan"]["id"],
                            "approved": True,
                            "reason": "断网期间按本地授权审核",
                        },
                    )
                    upload = role_clients["executor"].post(
                        "/api/files",
                        files={"file": ("offline-evidence.txt", b"synthetic offline execution evidence")},
                        headers={"Idempotency-Key": token()},
                    )
                    upload.raise_for_status()
                    evidence = upload.json()["id"]
                    from datetime import UTC, datetime

                    c = post(
                        role_clients["executor"],
                        f"/cases/{c['id']}/execute",
                        {
                            "version": c["version"],
                            "plan_id": p["plan"]["id"],
                            "executed_at": datetime.now(UTC).isoformat(),
                            "object_ids": ["OFFLINE-LOAD"],
                            "actual_actions": "断网期间授权人员现场人工核验",
                            "deviations": "无",
                            "evidence_ids": [evidence],
                            "success": True,
                        },
                    )
                    stamp = datetime.now(UTC).isoformat()
                    c = post(
                        role_clients["quality"],
                        f"/cases/{c['id']}/retest",
                        {
                            "version": c["version"],
                            "plan_id": p["plan"]["id"],
                            "started_at": stamp,
                            "finished_at": stamp,
                            "sample_count": 2,
                            "defective_count": 0,
                            "sample_unit": "bottle",
                            "evidence_ids": [evidence],
                            "conditions_met": True,
                            "other_constraints_met": True,
                            "notes": "合成复测只验证软件行为",
                        },
                    )
                    c = post(
                        role_clients["quality"],
                        f"/cases/{c['id']}/close",
                        {
                            "version": c["version"],
                            "root_cause_confirmation": "unverified",
                            "quality_disposition": "await_release",
                            "reason": "断网期间本地完整闭环归档",
                        },
                    )
                    assert c["status"] == "closed" and c["outcome"]["released"] is False
                finally:
                    for rc in role_clients.values():
                        rc.close()
                cases.append(c)
            try:
                sync_once(site, Database(site))
                raise AssertionError("outage_not_observed")
            except httpx.ConnectError:
                pass
        # Sites remain alive, persisted accounts and manual workflow do not depend on center.
        center_process = launch(center, center_port, root, frontend_dir)
        processes.append(center_process)
        reports = []
        for site, client, lines in sites:
            first = sync_once(site, Database(site))
            second = sync_once(site, Database(site))
            assert first["acknowledged"] > 0 and not first["conflicts"] and second["sent"] == 0
            reports.append(first)
        snapshots = center_client.get("/api/site-snapshots").json()
        assert len(snapshots) == 4
        assert all(row["data"].get("records", {}).get("receipts") for row in snapshots)
        reference = snapshots[0]["data"]["evidence_refs"][0]
        file_result = center_client.get(f"/api/site-snapshots/{snapshots[0]['id']}/files/{reference['id']}")
        assert (
            file_result.status_code == 200 and file_result.content == b"synthetic offline execution evidence"
        )
        post(center_client, f"/users/{employee['id']}/revoke", {})
        sync_once(sites[0][0], Database(sites[0][0]))
        assert employee_client.get("/api/auth/me").status_code == 401
        assert (
            employee_client.post(
                "/api/auth/login", json={"username": "central-engineer", "password": password}
            ).status_code
            == 401
        )
        employee_client.close()
        # A station cannot forge another station identity with an authenticated token.
        from quality.security import sign

        wrong = {"source": "site-b", "target": "center", "events": []}
        r = center_client.post(
            "/api/sync/push",
            headers={"Authorization": "Bearer " + sites[0][0].sync_token},
            json={"body": wrong, "signature": sign(wrong, sites[0][0].sync_token)},
        )
        assert r.status_code == 403
        foreign = sites[0][1].get("/api/cases/" + cases[-1]["id"])
        assert foreign.status_code == 404
        if browser_probe:
            fixture = root/'browser-scope-fixture.json'
            fixture.write_text(json.dumps({'center_url': f'http://127.0.0.1:{center_port}', 'password': password}))
            fixture.chmod(0o600)
            result = subprocess.run(['node','node_modules/@playwright/test/cli.js','test'], cwd='frontend',
                env={**os.environ, 'QUALITY_DELIVERY_SCOPE_FIXTURE':str(fixture), 'QUALITY_BROWSER_SUITE':'delivery',
                     'QUALITY_BROWSER_REPORT':os.environ.get('QUALITY_BROWSER_REPORT', '../reports/browser-delivery-multifactory.json'),
                     'QUALITY_E2E_URL':f'http://127.0.0.1:{center_port}'})
            if result.returncode:
                raise RuntimeError('multifactory_browser_failed; see reports/browser-delivery-multifactory.json')
        return {
            "customer": customer,
            "centers": 1,
            "sites": 2,
            "lines": 4,
            "snapshots": len(snapshots),
            "offline_cases": len(cases),
            "central_authorization_scope_offline_and_revocation": "passed",
            "center_full_closure_snapshot_and_evidence_http": "passed",
            "sync": reports,
            "customer_field_acceptance": "not_performed",
            "database": "SQLite integration; PostgreSQL independently tested",
            "browser_scope": 'passed' if browser_probe else 'not_requested',
        }
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        center_client.close()
        for _, c, _ in sites:
            c.close()


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser',action='store_true',help='同时验证实际运行中心的多厂页面筛选')
    parser.add_argument('--frontend-dir', type=Path, help='当前隔离构建目录，默认保持既有dist入口')
    args = parser.parse_args()
    target = Path("var/validation/multisite-" + time.strftime("%Y%m%d-%H%M%S")).resolve()
    target.mkdir(parents=True)
    results = []
    for customer in ("customer-alpha", "customer-beta"):
        root = target / customer
        root.mkdir()
        results.append(check_customer(customer, root, args.browser and customer == 'customer-alpha', args.frontend_dir))
    report = {
        "scenario": "two independently deployed synthetic customers",
        "results": results,
        "completed_at": time.time(),
    }
    (target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
