"""Create customer-owned configuration with independent random secrets. Never overwrite."""

import argparse
import base64
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from quality.config import Settings
from quality.security import token
from quality.sync import public_key


def rabbit_hash(password):
    salt = os.urandom(4)
    return base64.b64encode(salt + hashlib.sha256(salt + password.encode()).digest()).decode()


def generate(customer, target, base_port=8000, mode="integration"):
    if not re.fullmatch(r"[a-z][a-z0-9-]{2,40}", customer):
        raise ValueError("客户ID必须是3至41位小写字母/数字/连字符")
    target = Path(target).resolve()
    target.mkdir(parents=True, exist_ok=False, mode=0o700)
    seed = token()
    values = {
        "CUSTOMER_ID": customer,
        "CUSTOMER_CONFIG_DIR": str(target),
        "DB_ADMIN_PASSWORD": token(),
        "DB_RUNTIME_PASSWORD": token(),
        "PACKAGE_SIGNING_SEED": seed,
        "PACKAGE_PUBLIC_KEY": public_key(Settings(_env_file=None, package_secret=seed)),
        "CENTER_PORT": str(base_port),
        "SITE_A_PORT": str(base_port + 1),
        "SITE_B_PORT": str(base_port + 2),
        "JAEGER_PORT": str(base_port + 3),
        "CENTER_HOST": "center.example.invalid",
        "SITE_A_HOST": "factory-a.example.invalid",
        "SITE_B_HOST": "factory-b.example.invalid",
        "SITE_A_SYNC_TOKEN": "",
        "SITE_A_RECOVERY_KEY": token(),
        "SITE_B_RECOVERY_KEY": token(),
        "SITE_B_SYNC_TOKEN": "",
    }
    identities = [
        {
            "name": "bootstrap",
            "credentials": [{"accessKey": "bootstrap-" + token()[:12], "secretKey": token()}],
            "actions": ["Admin", "Read", "Write", "List", "Tagging"],
        }
    ]
    users = []
    permissions = []
    for node, key, bucket in [
        ("center", "CENTER", "quality-center"),
        ("site-a", "SITE_A", "quality-factory-a"),
        ("site-b", "SITE_B", "quality-factory-b"),
    ]:
        password = token()
        values["RABBIT_" + key + "_PASSWORD"] = password
        values["S3_" + key + "_ACCESS"] = token()[:20]
        values["S3_" + key + "_SECRET"] = token()
        users.append(
            {
                "name": node,
                "password_hash": rabbit_hash(password),
                "hashing_algorithm": "rabbit_password_hashing_sha256",
                "tags": [],
            }
        )
        permissions.append({"user": node, "vhost": node, "configure": ".*", "write": ".*", "read": ".*"})
        identities.append(
            {
                "name": node,
                "credentials": [
                    {
                        "accessKey": values["S3_" + key + "_ACCESS"],
                        "secretKey": values["S3_" + key + "_SECRET"],
                    }
                ],
                "actions": [a + ":" + bucket for a in ("Read", "Write", "List", "Tagging")],
            }
        )
        worker = {
            "QUALITY_MODE": mode,
            "QUALITY_BUSINESS_SEND_ENABLED": "false",
            "QUALITY_TENANT_ID": customer,
            "QUALITY_NODE_ID": node,
            "QUALITY_NODE_ROLE": "center" if node == "center" else "site",
            "QUALITY_SITE_ID": "" if node == "center" else "factory-" + node[-1],
            "QUALITY_DATABASE_URL": f"postgresql+psycopg://quality_runtime:{values['DB_RUNTIME_PASSWORD']}@{node}-db:5432/quality",
            "QUALITY_RABBITMQ_URL": f"amqp://{node}:{password}@broker:5672/{node}",
            "QUALITY_DATA_DIR": "/data",
            "QUALITY_STORAGE_BACKEND": "s3",
            "QUALITY_S3_ENDPOINT": "http://storage:8333",
            "QUALITY_S3_BUCKET": bucket,
            "QUALITY_S3_ACCESS_KEY": values["S3_" + key + "_ACCESS"],
            "QUALITY_S3_SECRET_KEY": values["S3_" + key + "_SECRET"],
            "QUALITY_APPROVED_HOSTS": json.dumps(
                [
                    "storage",
                    "center",
                    "site-a",
                    "site-b",
                    "jaeger",
                    values["CENTER_HOST"],
                    values["SITE_A_HOST"],
                    values["SITE_B_HOST"],
                ]
            ),
            "QUALITY_PACKAGE_PUBLIC_KEY": values["PACKAGE_PUBLIC_KEY"],
            "QUALITY_COOKIE_SECURE": "true" if mode == "production" else "false",
            "QUALITY_TRUSTED_ORIGINS": json.dumps(
                ["https://" + values[key + "_HOST"]]
                if mode == "production"
                else ["http://127.0.0.1:" + values["CENTER_PORT"]]
            ),
        }
        if node == "center":
            worker["QUALITY_PACKAGE_SECRET"] = seed
        else:
            worker["QUALITY_CENTER_URL"] = (
                "https://" + values["CENTER_HOST"] if mode == "production" else "http://center:8000"
            )
            worker["QUALITY_LOCAL_RECOVERY_KEY"] = values[key + "_RECOVERY_KEY"]
            worker["QUALITY_SYNC_TOKEN"] = ""
        (target / (node + "-worker.env")).write_text("\n".join(k + "=" + v for k, v in worker.items()) + "\n")
    (target / ".env").write_text("\n".join(k + "=" + v for k, v in values.items()) + "\n")
    (target / "rabbit-definitions.json").write_text(
        json.dumps(
            {"vhosts": [{"name": u["name"]} for u in users], "users": users, "permissions": permissions},
            indent=2,
        )
    )
    (target / "s3.json").write_text(json.dumps({"identities": identities}, indent=2))
    (target / "site-survey.json").write_text(
        json.dumps(
            {
                "customer_id": customer,
                "status": "unconfirmed",
                "implementation": {
                    "organization_and_roles": None, "source_inventory_and_points": None,
                    "identity_and_inspection_coverage": None, "approved_sop_limits": None,
                    "knowledge_review_roles": None, "retention_and_offline_hours": None,
                    "approved_model_location_and_data_categories": None,
                    "backup_key_custodians": None,
                    "acceptance_targets": {"status": "customer_confirmation_required", "rto": None,
                                           "rpo": None, "latency": None, "throughput": None},
                    "missing_fields_effect": "缺项不得作为现场生产就绪；不得用示例阈值替代批准SOP",
                },
                "factories": [
                    {
                        "id": f"factory-{s}",
                        "timezone": "Asia/Shanghai",
                        "lines": [
                            {
                                "code": f"{s.upper()}-1",
                                "process": "plastic_spray",
                                "connection_acceptance": "not_performed",
                            },
                            {
                                "code": f"{s.upper()}-2",
                                "process": "glass_vacuum",
                                "connection_acceptance": "not_performed",
                            },
                        ],
                    }
                    for s in ("a", "b")
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    (target / "README.txt").write_text(
        "此目录含客户独立密钥，0600权限；不得提交Git或发送至模型。按项目根目录 docs/DEPLOYMENT_AND_OPERATIONS.md 的客户实施流程初始化数据库、对象桶、账号并注册站点。填写现场条件和HTTPS域名。生产无默认管理员密码。\n"
    )
    for p in target.iterdir():
        if p.is_file():
            p.chmod(0o600)
    return {
        "customer": customer,
        "configuration": str(target),
        "mode": mode,
        "field_acceptance": "not_performed",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("customer")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--base-port", type=int, default=8000)
    parser.add_argument("--mode", choices=["integration", "production"], default="integration")
    args = parser.parse_args()
    print(json.dumps(generate(args.customer, args.directory, args.base_port, args.mode), ensure_ascii=False))
