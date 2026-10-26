from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session

from .models import Base


class Database:
    def __init__(self, settings, migration=False):
        self.settings = settings
        url = (
            settings.migration_database_url
            if migration and settings.migration_database_url
            else settings.database_url
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        if settings.mode == "production" and settings.data_dir.stat().st_mode & 0o077:
            raise RuntimeError("生产数据目录权限必须为0700；请由部署管理员受控配置，不自动修改现有目录")
        self.engine = create_engine(
            url,
            pool_pre_ping=True,
            **(
                {"connect_args": {"check_same_thread": False, "timeout": 20}}
                if url.startswith("sqlite")
                else {}
            ),
        )
        if url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def sqlite_constraints(conn, record):
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA journal_mode=WAL")

    @contextmanager
    def session(self, sites=None, lines=None):
        from .maintenance import gate

        with gate(self.settings.data_dir), Session(self.engine, expire_on_commit=False) as session:
            try:
                if self.engine.dialect.name == "postgresql":
                    session.execute(
                        text(
                            "SELECT set_config('app.tenant', :tenant, true), set_config('app.sites', :sites, true), set_config('app.lines', :lines, true)"
                        ),
                        {
                            "tenant": self.settings.tenant_id,
                            "sites": ",".join(self.settings.sites if sites is None else sites),
                            "lines": ",".join(lines if lines is not None else ["*"]),
                        },
                    )
                yield session
                session.commit()
                for callback in session.info.get("after_commit", []):
                    try:
                        callback()
                    except OSError as exc:
                        import logging

                        logging.getLogger("quality.db").warning(
                            "post_commit_marker_failed %s", type(exc).__name__
                        )
            except BaseException:
                session.rollback()
                raise

    def check_runtime_role(self):
        if self.engine.dialect.name == "postgresql":
            with self.engine.connect() as c:
                role = c.execute(
                    text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user")
                ).one()
                if role[0] or role[1]:
                    raise RuntimeError("拒绝使用 superuser/BYPASSRLS 应用连接")
                rows = c.execute(
                    text(
                        "SELECT relname,relrowsecurity,relforcerowsecurity,pg_get_userbyid(relowner)=current_user FROM pg_class WHERE relname=ANY(:names)"
                    ),
                    {"names": list(Base.metadata.tables)},
                ).all()
                if len(rows) != len(Base.metadata.tables) or any(
                    not row[1] or not row[2] or row[3] for row in rows
                ):
                    raise RuntimeError("业务表缺少RLS/FORCE策略或应用角色拥有迁移表；请修复数据库部署")

    def create_schema(self):
        Base.metadata.create_all(self.engine)
