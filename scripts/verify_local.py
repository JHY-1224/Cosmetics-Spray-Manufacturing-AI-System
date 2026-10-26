"""Run repository checks with isolated default storage and fresh, non-overwriting reports."""

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from quality.config import Settings
    from quality.versioning import RELEASE, SCHEMA_HEAD

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, help="全新报告目录，不覆盖已有报告")
    parser.add_argument("--python", type=Path, help="已安装的离线虚拟环境解释器；默认当前Python")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="--之后可指定需复验的pytest用例")
    args = parser.parse_args()
    report = (args.report_dir or ROOT / "reports" / ("local-verification-" + uuid.uuid4().hex[:12])).resolve()
    if report.exists():
        parser.error("报告目录已存在，请指定新目录以保留历史证据")
    # Preserve the venv launcher symlink: resolving it would select the base interpreter.
    python = (args.python or Path(sys.executable)).absolute()
    if not python.is_file():
        parser.error("指定Python解释器不存在")
    report.mkdir(parents=True, mode=0o700)
    # Never inherit customer endpoints, data paths, credentials or send switches into checks.
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("QUALITY_")}
    env["PYTEST_ARTIFACT_DIR"] = str(report / "artifacts")
    test_args = args.pytest_args[1:] if args.pytest_args[:1] == ["--"] else args.pytest_args
    with tempfile.TemporaryDirectory(prefix="quality-check-default-") as temporary:
        for key, field in Settings.model_fields.items():
            value = field.get_default(call_default_factory=True)
            if key == "data_dir":
                value = temporary
            elif key == "database_url":
                value = "sqlite:///" + str(Path(temporary) / "default.db")
            env["QUALITY_" + key.upper()] = json.dumps(value) if isinstance(value, (dict, list, bool)) else str(value)
        command = [str(python), "-m", "pytest", "-q", "--junitxml=" + str(report / "pytest.xml"), *test_args]
        with (report / "pytest.log").open("x") as log:
            result = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
        metadata = {
            "application": RELEASE,
            "schema": SCHEMA_HEAD,
            "completed_at": datetime.now(UTC).isoformat(),
            "platform": platform.platform(),
            "python_executable": str(python),
            "pytest_args": test_args,
            "exit_code": result.returncode,
            "isolated_default_target": True,
            "inherited_quality_configuration": False,
            "rabbitmq": "not_configured; actual integration test remains explicitly skipped",
            "field_acceptance": "not_performed",
        }
        (report / "run.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2))
    print(json.dumps({"exit_code": result.returncode, "report": str(report)}, ensure_ascii=False))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
