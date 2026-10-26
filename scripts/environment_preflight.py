"""Read-only prerequisite inventory; never installs Docker or modifies services."""

import argparse
import importlib.metadata
import json
import platform
import shutil
import subprocess
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--docker-required", action="store_true")
args = parser.parse_args()


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return {
            "available": result.returncode == 0,
            "version": result.stdout.strip()[:160] if result.returncode == 0 else None,
        }
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "version": None}


docker = (
    command(["docker", "version", "--format", "{{.Server.Version}}"])
    if shutil.which("docker")
    else {"available": False, "version": None}
)
compose = (
    command(["docker", "compose", "version", "--short"])
    if shutil.which("docker")
    else {"available": False, "version": None}
)
result = {
    "os": platform.platform(),
    "cpu_arch": platform.machine(),
    "python": platform.python_version(),
    "fastapi": importlib.metadata.version("fastapi"),
    "docker_engine": docker,
    "docker_compose": compose,
    "frontend_built": Path("frontend/dist/index.html").exists(),
    "deferred_validation": "docs/VALIDATION_AND_DELIVERY.md",
    "field_devices": "not_provided",
}
print(json.dumps(result, ensure_ascii=False, indent=2))
if args.docker_required and not (docker["available"] and compose["available"]):
    raise SystemExit(2)
