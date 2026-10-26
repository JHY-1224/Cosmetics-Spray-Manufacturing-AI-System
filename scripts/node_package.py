"""Generate a NEW private package for one independent HTTPS host; never overwrite a node."""

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quality.config import Settings
from quality.security import token
from quality.sync import public_key
from quality.versioning import RELEASE, SCHEMA_HEAD
from scripts.customer_package import rabbit_hash


def generate(
    customer,
    destination,
    node,
    host,
    *,
    role,
    site_id="",
    center_url="",
    center_public_key="",
    allowed_sites=None,
):
    if not all(re.fullmatch(r"[a-z][a-z0-9-]{2,40}", v) for v in [customer, node]):
        raise ValueError("客户及节点须为明确的小写身份")
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9.-]{2,250}", host):
        raise ValueError("节点主机名非法")
    allowed_sites = allowed_sites or ([site_id] if site_id else [])
    if not allowed_sites or any(not re.fullmatch(r"[a-zA-Z0-9-]{1,80}", s) for s in allowed_sites):
        raise ValueError("须明确初始化管理员可访问的工厂，禁止默认全客户授权")
    if role not in {"center", "site"}:
        raise ValueError("不支持的节点角色")
    seed = token()
    key = public_key(Settings(_env_file=None, package_secret=seed)) if role == "center" else center_public_key
    if role == "site" and (
        not site_id or allowed_sites != [site_id] or urlparse(center_url).scheme != "https" or not key
    ):
        raise ValueError("工厂须绑定本厂、HTTPS中心及预置信任公钥")
    db_runtime, db_admin, rabbit, access, secret, recovery = [token() for _ in range(6)]
    bucket = "quality-" + node
    runtime = dict(
        mode="production",
        tenant_id=customer,
        node_id=node,
        node_role=role,
        site_id=site_id if role == "site" else "",
        data_dir="/data",
        database_url=f"postgresql+psycopg://quality_runtime:{db_runtime}@db:5432/quality",
        rabbitmq_url=f"amqp://{node}:{rabbit}@broker:5672/{node}",
        storage_backend="s3",
        s3_endpoint="http://storage:8333",
        s3_bucket=bucket,
        s3_access_key=access,
        s3_secret_key=secret,
        cookie_secure=True,
        approved_hosts=list(
            dict.fromkeys(
                [
                    "db",
                    "broker",
                    "storage",
                    host,
                    *([urlparse(center_url).hostname] if role == "site" else []),
                ]
            )
        ),
        trusted_origins=["https://" + host],
        package_public_key=key,
        business_send_enabled=False,
        model_approved=False,
        model_url="",
        package_secret=seed if role == "center" else "",
        center_url=center_url if role == "site" else "",
        local_recovery_key=recovery,
    )
    # Validate before creating any package, including Secure-cookie and role separation checks.
    Settings(_env_file=None, **runtime)
    target = Path(destination).resolve()
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    runtime_env = {
        "QUALITY_" + k.upper(): json.dumps(v) if isinstance(v, (bool, list, dict)) else str(v)
        for k, v in runtime.items()
    }
    files = {
        "runtime.env": "\n".join(k + "=" + v for k, v in runtime_env.items()) + "\n",
        ".env": "\n".join(
            k + "=" + v
            for k, v in {
                "NODE_PROJECT": customer + "-" + node,
                "NODE_CONFIG_DIR": str(target),
                "NODE_HOST": host,
                "NODE_DB_ADMIN_PASSWORD": db_admin,
                "NODE_DB_RUNTIME_PASSWORD": db_runtime,
                "INITIAL_ALLOWED_SITES": ",".join(allowed_sites),
                "NODE_BUCKET": bucket,
            }.items()
        )
        + "\n",
        "center-public-key.txt": key + "\n",
        "rabbit-definitions.json": json.dumps(
            {
                "vhosts": [{"name": node}],
                "users": [
                    {
                        "name": node,
                        "password_hash": rabbit_hash(rabbit),
                        "hashing_algorithm": "rabbit_password_hashing_sha256",
                        "tags": [],
                    }
                ],
                "permissions": [
                    {"user": node, "vhost": node, "configure": ".*", "write": ".*", "read": ".*"}
                ],
            },
            indent=2,
        ),
        "s3.json": json.dumps(
            {
                "identities": [
                    {
                        "name": "bootstrap",
                        "credentials": [{"accessKey": "bootstrap-" + token()[:12], "secretKey": token()}],
                        "actions": ["Admin", "Read", "Write", "List", "Tagging"],
                    },
                    {
                        "name": node,
                        "credentials": [{"accessKey": access, "secretKey": secret}],
                        "actions": [a + ":" + bucket for a in ["Read", "Write", "List", "Tagging"]],
                    },
                ]
            },
            indent=2,
        ),
        "deployment-status.json": json.dumps(
            {
                "application": RELEASE,
                "schema": SCHEMA_HEAD,
                "node": node,
                "role": role,
                "topology": "independent_host",
                "tls_dns_acceptance": "not_performed",
                "container_execution": "not_performed",
                "customer_acceptance": "not_performed",
                "business_send_enabled": False,
            },
            indent=2,
        ),
    }
    for name, value in files.items():
        (target / name).write_text(value)
        (target / name).chmod(0o600)
    return {
        "created": str(target),
        "node": node,
        "version": RELEASE,
        "secrets_in_output": False,
        "field_acceptance": "not_performed",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("customer")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--node", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--role", choices=["center", "site"], required=True)
    parser.add_argument("--site-id", default="")
    parser.add_argument("--center-url", default="")
    parser.add_argument("--center-public-key-file", type=Path)
    parser.add_argument("--allowed-sites", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            generate(
                args.customer,
                args.destination,
                args.node,
                args.host,
                role=args.role,
                site_id=args.site_id,
                center_url=args.center_url,
                center_public_key=args.center_public_key_file.read_text().strip()
                if args.center_public_key_file
                else "",
                allowed_sites=args.allowed_sites.split(","),
            ),
            ensure_ascii=False,
        )
    )
