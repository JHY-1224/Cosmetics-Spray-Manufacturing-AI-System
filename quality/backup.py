import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from sqlalchemy import inspect, select
from sqlalchemy.engine import make_url

from . import models as m
from .db import Database
from .security import digest, current_time
from .storage import Storage


def pg_tool(name):
    binary = shutil.which(name)
    if binary:
        return binary
    from pgserver._commands import POSTGRES_BIN_PATH

    return str(POSTGRES_BIN_PATH / name)


def pg_env(uri):
    url = make_url(uri)
    env = os.environ.copy()
    env.update(
        {
            "PGUSER": url.username or "",
            "PGPASSWORD": url.password or "",
            "PGDATABASE": url.database or "",
            "PGHOST": url.query.get("host", url.host or "localhost"),
            "PGPORT": str(url.port or 5432),
        }
    )
    return env


def backup(settings, destination):
    from .maintenance import gate

    with gate(settings.data_dir, exclusive=True):
        return _backup(settings, destination)


def _backup(settings, destination):
    from .versioning import RELEASE, SCHEMA_HEAD

    started = current_time().isoformat()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)
    db = Database(settings, migration=True)
    if db.engine.dialect.name == "sqlite":
        with (
            sqlite3.connect(db.engine.url.database) as src,
            sqlite3.connect(destination / "business.sqlite") as target,
        ):
            src.backup(target)
        checkpoint = settings.data_dir / "checkpoints.db"
        if checkpoint.exists():
            with (
                sqlite3.connect(checkpoint) as src,
                sqlite3.connect(destination / "checkpoints.sqlite") as target,
            ):
                src.backup(target)
    else:
        subprocess.run(
            [pg_tool("pg_dump"), "--format=custom", "--file", str(destination / "business.dump")],
            env=pg_env(settings.migration_database_url or settings.database_url),
            check=True,
            capture_output=True,
        )
    store = Storage(settings)
    manifest = {
        "version": RELEASE,
        "schema_required": SCHEMA_HEAD,
        "tenant_id": settings.tenant_id,
        "site_id": settings.site_id,
        "node_id": settings.node_id,
        "consistency": {
            "started_at": started,
            "cross_resource_atomic": False,
            "runtime_barrier": "exclusive_interprocess_and_thread_gate",
            "requirement": "所有API、Worker须运行支持维护屏障的同一版本并共享节点data_dir；外部直写数据库和多主机不同data_dir须先停写。持锁期间复制数据库、检查点、原件及原始接收日志。",
        },
        "objects": [],
        "secret_backup": "服务端环境密钥须由客户密钥托管系统独立加密备份，不写入清单",
    }
    with db.session(["*"]) as session:
        evidence = session.scalars(select(m.Evidence).where(m.Evidence.tenant_id == settings.tenant_id)).all()
        for row in evidence:
            data = store.get(row.object_key, row.sha256)
            path = destination / "objects" / row.object_key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            manifest["objects"].append(
                {"id": row.id, "key": row.object_key, "sha256": row.sha256, "size": len(data)}
            )
    for folder in ("adapter-spool", "model-journal", "ingestion-raw"):
        source = settings.data_dir / folder
        if source.exists():
            shutil.copytree(source, destination / "runtime" / folder)
    for path in destination.rglob("*"):
        if path.is_file():
            path.chmod(0o600)
    manifest["files"] = {
        str(p.relative_to(destination)): digest(p.read_bytes()) for p in destination.rglob("*") if p.is_file()
    }
    (destination / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (destination / "manifest.json").chmod(0o600)
    from .model_journal import write

    write(
        settings.data_dir / "backup-status.json",
        {
            "finished_at": current_time().isoformat(),
            "manifest_hash": digest(manifest),
            "objects": len(evidence),
            "status": "completed",
            "restore_verification": "not_implied_by_backup",
            "cross_resource_atomic": False,
        },
    )
    return {
        "database": "SQLite" if db.engine.dialect.name == "sqlite" else "PostgreSQL",
        "objects": len(evidence),
        "destination": str(destination),
    }


def verify(source):
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text())
    for filename, hash in manifest["files"].items():
        path = (source / filename).resolve()
        if not path.is_relative_to(source.resolve()) or digest(path.read_bytes()) != hash:
            raise ValueError("备份文件完整性失败: " + filename)
    return manifest


def restore(settings, source):
    from .maintenance import gate

    with gate(settings.data_dir, exclusive=True):
        return _restore(settings, source)


def _restore(settings, source):
    source = Path(source)
    manifest = verify(source)
    if (
        manifest["tenant_id"] != settings.tenant_id
        or manifest["site_id"] != settings.site_id
        or manifest.get("node_id") != settings.node_id
    ):
        raise ValueError("备份企业/工厂身份与恢复目标不匹配")
    db = Database(settings, migration=True)
    if inspect(db.engine).get_table_names():
        raise ValueError("恢复仅允许全新空数据库，拒绝覆盖现有业务")
    for folder in ("adapter-spool", "model-journal", "ingestion-raw"):
        if (source / "runtime" / folder).exists() and (settings.data_dir / folder).exists():
            raise ValueError("目标运行日志或接收缓存已存在，不覆盖")
    if (source / "checkpoints.sqlite").exists() and (settings.data_dir / "checkpoints.db").exists():
        raise ValueError("目标检查点已存在，不覆盖")
    # Freeze BEFORE any restored command becomes visible to a concurrently started worker.
    from .model_journal import write

    write(
        settings.data_dir / "business-frozen.json",
        {
            "allow_send": False,
            "reason": "restored_requires_external_reconciliation",
            "restored_at": current_time().isoformat(),
            "backup_manifest_hash": digest(manifest),
        },
    )
    if db.engine.dialect.name == "sqlite":
        with (
            sqlite3.connect(source / "business.sqlite") as src,
            sqlite3.connect(db.engine.url.database) as target,
        ):
            src.backup(target)
        if (source / "checkpoints.sqlite").exists():
            target = settings.data_dir / "checkpoints.db"
            if target.exists():
                raise ValueError("目标检查点已存在，不覆盖")
            with sqlite3.connect(source / "checkpoints.sqlite") as src, sqlite3.connect(target) as out:
                src.backup(out)
    else:
        subprocess.run(
            [
                pg_tool("pg_restore"),
                "--exit-on-error",
                "--no-owner",
                "--no-acl",
                "--dbname",
                make_url(settings.migration_database_url or settings.database_url).database,
                str(source / "business.dump"),
            ],
            env=pg_env(settings.migration_database_url or settings.database_url),
            check=True,
            capture_output=True,
        )
    for folder in ("adapter-spool", "model-journal", "ingestion-raw"):
        runtime = source / "runtime" / folder
        if runtime.exists():
            shutil.copytree(runtime, settings.data_dir / folder)
    # In-flight sends are uncertain, never transformed back into a fresh send.
    if "business_commands" in inspect(db.engine).get_table_names():
        from sqlalchemy import update

        with db.session() as session:
            session.execute(
                update(m.BusinessCommand)
                .where(m.BusinessCommand.status == "sending")
                .values(
                    status="unknown",
                    lease_token="",
                    lease_until=None,
                    error_code="restored_inflight_requires_lookup",
                    fencing=m.BusinessCommand.fencing + 1,
                    version=m.BusinessCommand.version + 1,
                )
            )
            session.execute(
                update(m.DigitalTask)
                .where(m.DigitalTask.status.in_(["queued", "running", "awaiting_approval"]))
                .values(status="paused", lease_token="", lease_until=None, version=m.DigitalTask.version + 1)
            )
    storage = Storage(settings)
    for entry in manifest["objects"]:
        path = source / "objects" / entry["key"]
        state = storage.put(entry["key"], path.read_bytes(), "application/octet-stream")
        if state != "available":
            raise RuntimeError("恢复对象未同步就绪，保留当前恢复副本供排查；切勿将其上线")
        storage.get(entry["key"], entry["sha256"])
    return {
        "restored": True,
        "objects": len(manifest["objects"]),
        "business_send_frozen": True,
        "note": "PostgreSQL恢复后须按部署手册重新授予运行角色权限并验证RLS；上线前执行健康检查",
    }
