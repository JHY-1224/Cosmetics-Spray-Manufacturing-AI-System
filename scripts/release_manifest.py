import importlib.metadata
import json
import platform
import subprocess
import sys
from pathlib import Path

images = [
    "quality-platform:0.2.0rc1",
    "pgvector/pgvector:0.8.7-pg17-trixie",
    "rabbitmq:4.3.6-management",
    "chrislusf/seaweedfs:4.48",
    "jaegertracing/jaeger:2.14.1",
    "caddy:2.10.2-alpine",
]
dependencies = []
for distribution in importlib.metadata.distributions():
    meta = distribution.metadata
    dependencies.append(
        {
            "name": meta["Name"],
            "version": distribution.version,
            "license": meta.get("License-Expression") or meta.get("License") or "需审查包内LICENSE",
            "homepage": meta.get("Home-page") or meta.get("Project-URL", ""),
        }
    )
resolved = []
for image in images:
    try:
        digests = json.loads(
            subprocess.check_output(
                ["docker", "image", "inspect", image, "--format", "{{json .RepoDigests}}"],
                stderr=subprocess.DEVNULL,
            )
        )
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        digests = None
    resolved.append(
        {
            "image": image,
            "digests": digests,
            "container_validation": "not_performed"
            if digests is None
            else "available_locally_not_acceptance",
        }
    )
result = {
    "application": "0.2.0rc1",
    "build_images": ["node:24.19.0-alpine", "python:3.12.14-slim-trixie"],
    "target_os_packages": {
        "postgresql-client-17": {"version": "17.11-0+deb13u1", "validation": "not_performed"}
    },
    "python": platform.python_version(),
    "platform": platform.platform(),
    "dependencies": sorted(dependencies, key=lambda d: d["name"].lower()),
    "images": resolved,
    "rules": "客户审核发布版本；无内置正式生产参数",
    "templates": ["plastic_spray/reference-v1", "glass_vacuum/reference-v1"],
    "models": "not configured",
    "field_acceptance": "not_performed",
    "license_note": "SeaweedFS Apache-2.0；RabbitMQ MPL-2.0；PostgreSQL PostgreSQL License；pgvector PostgreSQL License；客户分发前复核完整第三方声明",
}
front = Path("frontend/package.json")
if front.exists():
    import hashlib

    package = json.loads(front.read_text())
    result["frontend"] = {
        "declared": package.get("dependencies", {}),
        "development": package.get("devDependencies", {}),
        "lock_sha256": hashlib.sha256(Path("frontend/pnpm-lock.yaml").read_bytes()).hexdigest(),
        "licenses": [],
    }
    for name in [*package.get("dependencies", {}), *package.get("devDependencies", {})]:
        path = Path("frontend/node_modules") / name / "package.json"
        if path.exists():
            metadata = json.loads(path.read_text())
            result["frontend"]["licenses"].append(
                {
                    "name": name,
                    "version": metadata["version"],
                    "license": metadata.get("license", "见包内LICENSE"),
                }
            )
target = Path(sys.argv[1] if len(sys.argv) > 1 else "RELEASE_MANIFEST.json")
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(json.dumps(result, ensure_ascii=False, indent=2))
print(str(target))
