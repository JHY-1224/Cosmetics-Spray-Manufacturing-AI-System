import argparse
import getpass
import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from . import models as m
from .config import Settings
from .db import Database
from .security import PASSWORDS, check_password, token
from .templates import reference_routes
from .identity import local_lease, renew_local_lease


def migrate(settings):
    from .maintenance import gate

    with gate(settings.data_dir, exclusive=True):
        return _migrate(settings)


def _migrate(settings):
    db = Database(settings, migration=True)
    with db.engine.begin() as conn:
        if db.engine.dialect.name == "postgresql":
            conn.execute(
                text(
                    "SELECT set_config('app.tenant',:tenant,true),set_config('app.sites','*',true),set_config('app.lines','*',true)"
                ),
                {"tenant": settings.tenant_id},
            )
        config = Config(str(Path(__file__).resolve().parent.parent / "alembic.ini"))
        config.attributes["connection"] = conn
        command.upgrade(config, "head")
    if settings.database_url.startswith("postgresql"):
        from langgraph.checkpoint.postgres import PostgresSaver

        uri = (settings.migration_database_url or settings.database_url).replace(
            "postgresql+psycopg", "postgresql"
        )
        with PostgresSaver.from_conn_string(uri) as saver:
            saver.setup()


def initialize(settings, username, password, sites=None):
    check_password(password)
    db = Database(settings, migration=True)
    allowed = sites or settings.sites
    if not allowed:
        raise ValueError("中心初始化需明确 --sites 工厂授权范围")
    with db.session(allowed) as session:
        if session.scalar(
            select(m.User).where(m.User.tenant_id == settings.tenant_id, m.User.username == username)
        ):
            raise ValueError("用户已存在；初始化不会覆盖密码")
        user = m.User(
            tenant_id=settings.tenant_id,
            site_id="",
            username=username,
            password_hash=PASSWORDS.hash(password),
            role="admin",
            allowed_sites=allowed,
            lease_expires_at=local_lease(settings),
        )
        session.add(user)
        session.flush()
        if not session.scalar(select(m.Route).where(m.Route.tenant_id == settings.tenant_id)):
            session.add_all(reference_routes(settings.tenant_id, user.id))
    return user.id


def seed_demo(settings):
    if settings.mode == "production":
        raise ValueError("生产拒绝导入演示数据")
    db = Database(settings)
    credentials = {}
    with db.session() as session:
        for role in ("engineer", "approver", "executor", "quality", "inspector", "viewer"):
            if not session.scalar(
                select(m.User).where(m.User.tenant_id == settings.tenant_id, m.User.username == role)
            ):
                password = token()
                session.add(
                    m.User(
                        tenant_id=settings.tenant_id,
                        site_id="",
                        username=role,
                        password_hash=PASSWORDS.hash(password),
                        role=role,
                        allowed_sites=settings.sites,
                        lease_expires_at=local_lease(settings),
                    )
                )
                credentials[role] = password
        for i, process in enumerate(("plastic_spray", "glass_vacuum"), start=1):
            code = f"LINE-{i}"
            if not session.scalar(
                select(m.Master).where(
                    m.Master.tenant_id == settings.tenant_id,
                    m.Master.site_id == settings.site_id,
                    m.Master.kind == "line",
                    m.Master.code == code,
                )
            ):
                id = m.uid()
                session.add(
                    m.Master(
                        id=id,
                        scope_line_id=id,
                        tenant_id=settings.tenant_id,
                        site_id=settings.site_id,
                        kind="line",
                        code=code,
                        name="塑料瓶喷涂一线" if i == 1 else "玻璃瓶蒸镀二线",
                        data={"process": process, "provenance": "demo"},
                    )
                )
    if credentials:
        target = settings.data_dir / "demo-credentials.json"
        target.write_text(json.dumps(credentials, ensure_ascii=False, indent=2))
        target.chmod(0o600)
    return list(credentials)


def main():
    parser = argparse.ArgumentParser(description="涂镀智溯安装和验证工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate-config")
    sub.add_parser("migrate")
    init = sub.add_parser("init")
    init.add_argument("--username", default="admin")
    init.add_argument("--password-file", type=Path)
    init.add_argument("--sites", default="")
    sub.add_parser("seed-demo")
    sub.add_parser("worker")
    sub.add_parser("sync-once")
    recovery = sub.add_parser("renew-local-lease")
    recovery.add_argument("--username", required=True)
    recovery.add_argument("--hours", type=int, default=1)
    recovery.add_argument("--reason", required=True)
    recovery.add_argument("--key-file", type=Path, required=True)
    recovery.add_argument("--password-file", type=Path, required=True)
    archive_parser = sub.add_parser("archive-evidence")
    archive_parser.add_argument("destination", type=Path)
    backup_parser = sub.add_parser("backup")
    backup_parser.add_argument("destination", type=Path)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    verify_parser = sub.add_parser("verify-backup")
    verify_parser.add_argument("source", type=Path)
    args = parser.parse_args()
    settings = Settings()
    if args.command == "validate-config":
        print(
            json.dumps({"valid": True, "mode": settings.mode, "node": settings.node_id}, ensure_ascii=False)
        )
    elif args.command == "migrate":
        migrate(settings)
        print("数据库与检查点迁移完成")
    elif args.command == "init":
        password = (
            args.password_file.read_text().strip()
            if args.password_file
            else getpass.getpass("管理员初始密码（至少14字符）: ")
        )
        print(
            "已创建管理员",
            initialize(settings, args.username, password, args.sites.split(",") if args.sites else None),
        )
    elif args.command == "seed-demo":
        print("新增演示角色", seed_demo(settings), "随机密码保存在节点数据目录 demo-credentials.json（0600）")
    elif args.command == "worker":
        from .worker import run

        run(settings)
    elif args.command == "sync-once":
        from .sync import sync_once

        print(sync_once(settings, Database(settings)))
    elif args.command == "renew-local-lease":
        with Database(settings).session() as db:
            print(
                renew_local_lease(
                    db,
                    settings,
                    args.username,
                    args.password_file.read_text().strip(),
                    args.key_file.read_text().strip(),
                    args.hours,
                    args.reason,
                )
            )
    elif args.command == "archive-evidence":
        from .operations import archive

        print(archive(settings, args.destination))
    elif args.command in ("backup", "restore", "verify-backup"):
        from .backup import backup, restore, verify

        result = (
            backup(settings, args.destination)
            if args.command == "backup"
            else restore(settings, args.source)
            if args.command == "restore"
            else {"verified": True, "objects": len(verify(args.source)["objects"])}
        )
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
