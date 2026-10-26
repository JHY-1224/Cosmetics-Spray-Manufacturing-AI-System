import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quality.security import token


def main():
    parser = argparse.ArgumentParser(description="客户基础拓扑安装；只初始化新账号，不覆盖现有业务")
    parser.add_argument("configuration", type=Path)
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--offline", action="store_true", help="仅使用已校验载入的镜像，不构建或联网安装依赖")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    package = args.configuration.resolve()
    values = dict(dotenv_values(package / ".env"))
    compose = [
        "docker",
        "compose",
        "--env-file",
        str(package / ".env"),
        "-f",
        str(root / "deploy/compose.yaml"),
    ]
    if args.production:
        if any(str(values[k]).endswith(".invalid") for k in ("CENTER_HOST", "SITE_A_HOST", "SITE_B_HOST")):
            raise SystemExit("生产安装前必须填写实际HTTPS域名和受控网络；拒绝占位域名")
        compose += ["-f", str(root / "deploy/compose.production.yaml")]

    def run(*parts, env=None):
        subprocess.run(compose + list(parts), cwd=root, env=env, check=True)

    if not args.offline:
        run("build")
    run("up", "-d", "center-db", "site-a-db", "site-b-db", "broker", "storage", "jaeger")
    for node in ("center", "site-a", "site-b"):
        password_file = package / (node + "-initial-password.txt")
        if password_file.exists():
            raise SystemExit(
                "发现已有初始化密码文件；为保护现有账号，停止重复初始化。按运维手册检查状态并显式恢复后续阶段。"
            )
        password_file.write_text(token())
        password_file.chmod(0o600)
        env = os.environ.copy()
        env["QUALITY_MIGRATION_DATABASE_URL"] = (
            f"postgresql+psycopg://quality_migrator:{values['DB_ADMIN_PASSWORD']}@{node}-db:5432/quality"
        )
        run(
            "run",
            "--rm",
            "--user",
            "0:0",
            "-e",
            "QUALITY_MIGRATION_DATABASE_URL",
            node,
            "python",
            "-m",
            "quality.cli",
            "migrate",
            env=env,
        )
        sites = "factory-a,factory-b" if node == "center" else "factory-" + node[-1]
        run(
            "run",
            "--rm",
            "--user",
            "0:0",
            "-e",
            "QUALITY_MIGRATION_DATABASE_URL",
            "-v",
            str(password_file) + ":/run/initial-password:ro",
            node,
            "python",
            "-m",
            "quality.cli",
            "init",
            "--password-file",
            "/run/initial-password",
            "--sites",
            sites,
            env=env,
        )
    run(
        "run",
        "--rm",
        "--user",
        "0:0",
        "-v",
        str(package / "s3.json") + ":/run/s3-bootstrap.json:ro",
        "center",
        "python",
        "scripts/init_storage.py",
        "/run/s3-bootstrap.json",
    )
    run("up", "-d", "center", "site-a", "site-b", *(["edge"] if args.production else []))
    if not args.production:
        with httpx.Client(base_url="http://127.0.0.1:" + values["CENTER_PORT"], timeout=10) as client:
            for attempt in range(50):
                try:
                    client.get("/api/health").raise_for_status()
                    break
                except httpx.HTTPError:
                    if attempt == 49:
                        raise
                    time.sleep(1)
            response = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": (package / "center-initial-password.txt").read_text()},
            )
            response.raise_for_status()
            client.headers["X-CSRF-Token"] = client.cookies["quality_csrf"]
            for suffix in ("a", "b"):
                response = client.post(
                    "/api/sites",
                    json={
                        "node_id": "site-" + suffix,
                        "site_id": "factory-" + suffix,
                        "name": "工厂" + suffix,
                        "base_url": "http://site-" + suffix + ":8000",
                    },
                    headers={"Idempotency-Key": token()},
                )
                response.raise_for_status()
                registered = response.json()
                values["SITE_" + suffix.upper() + "_SYNC_TOKEN"] = registered["token"]
                worker = package / ("site-" + suffix + "-worker.env")
                worker.write_text(
                    worker.read_text().replace(
                        "QUALITY_SYNC_TOKEN=\n", "QUALITY_SYNC_TOKEN=" + registered["token"] + "\n"
                    )
                )
        (package / ".env").write_text("\n".join(k + "=" + str(v) for k, v in values.items()) + "\n")
        run("up", "-d", "site-a", "site-b")
        run("--profile", "workers", "up", "-d", "center-worker", "site-a-worker", "site-b-worker")
    else:
        print(
            "生产HTTPS入口需由客户验证；用中心页面注册站点，将一次性凭据写入站点/Worker配置后启动workers profile。"
        )
    print("基础安装流程结束；结果必须再执行验收清单。初始化凭据保存在配置目录0600文件，不输出到日志。")


if __name__ == "__main__":
    main()
