from pathlib import Path
from typing import Literal

from pydantic import model_validator, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="QUALITY_", env_file=".env", extra="ignore", hide_input_in_errors=True
    )
    mode: Literal["demo", "integration", "production"] = "demo"
    node_role: Literal["center", "site"] = "site"
    tenant_id: str = "demo-customer"
    site_id: str = "factory-a"
    site_timezone: str = "Asia/Shanghai"
    node_id: str = "site-a"
    database_url: str = "sqlite:///var/site.db"
    migration_database_url: str = ""
    data_dir: Path = Path("var")
    session_hours: int = 8
    offline_auth_hours: int = 24
    cookie_secure: bool = False
    trusted_origins: list[str] = ["http://127.0.0.1:8000", "http://localhost:8000", "http://localhost:5173"]
    approved_hosts: list[str] = ["127.0.0.1", "localhost"]
    storage_backend: Literal["local", "s3"] = "local"
    s3_endpoint: str = ""
    s3_bucket: str = "quality-evidence"
    s3_access_key: str = ""
    s3_secret_key: str = ""
    rabbitmq_url: str = ""
    center_url: str = ""
    sync_token: str = ""
    package_secret: str = ""
    package_public_key: str = ""
    center_delegations: dict[str, str] = {}
    identity_bindings: dict[str, str] = {}
    local_recovery_key: str = ""
    model_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_approved: bool = False
    model_external: bool = False
    model_timeout: float = 20
    model_max_output_tokens: int = Field(default=2500, ge=256, le=8192)
    model_concurrency: int = 2
    learning_concurrency: int = Field(default=2, ge=1, le=4)
    learning_timeout: int = Field(default=30, ge=2, le=120)
    learning_queue_limit: int = Field(default=100, ge=1, le=10000)
    digital_concurrency: int = Field(default=2, ge=1, le=4)
    digital_timeout: int = Field(default=30, ge=2, le=120)
    digital_queue_limit: int = Field(default=100, ge=1, le=10000)
    business_send_enabled: bool = False
    business_timeout: int = Field(default=10, ge=1, le=30)
    business_queue_limit: int = Field(default=100, ge=1, le=10000)
    model_allowed_data: list[
        Literal[
            "process_labels",
            "evidence_references",
            "candidates",
            "aggregated_features",
            "case_notes",
            "approved_knowledge",
            "embedding_query",
        ]
    ] = ["process_labels", "evidence_references"]
    model_cost_per_1k: float = 0
    max_case_tokens: int = 50000
    max_case_cost: float = 25
    max_upload_bytes: int = 10 * 1024 * 1024
    max_investigations: int = 3
    max_case_hours: int = 168
    max_model_calls: int = 6
    cache_max_bytes: int = 2 * 1024**3
    retention_days: int = 90
    warning_ratio: float = 0.8
    otlp_endpoint: str = ""
    embedding_url: str = ""
    embedding_model: str = ""
    embedding_dimensions: int = 384

    def __init__(self, **values):
        import os
        import re
        from dotenv import dotenv_values

        names = set(os.environ)
        files = values.get("_env_file", self.model_config.get("env_file"))
        for path in files if isinstance(files, (tuple, list)) else [files]:
            if path is not None and Path(path).is_file():
                names.update(dotenv_values(path))
        known = {"QUALITY_" + name.upper() for name in type(self).model_fields}
        known.update(
            {
                "QUALITY_LOCAL_INSTANCE",
                "QUALITY_LOCAL_BUILD",
                "QUALITY_CUSTOMER_ENV",
                "QUALITY_ADVANCED_FIXTURE",
                "QUALITY_BROWSER_REPORT",
                "QUALITY_BROWSER_SUITE",
                "QUALITY_BROWSER_PATH",
                "QUALITY_BROWSER_OUTPUT",
                "QUALITY_DELIVERY_SCOPE_FIXTURE",
                "QUALITY_E2E_URL",
                "QUALITY_E2E_CREDENTIALS",
                "QUALITY_E2E_ADMIN",
                "QUALITY_E2E_SCREENSHOTS",
                "QUALITY_TEST_RABBITMQ_URL",
            }
        )
        unknown = sorted(
            name
            for name in names
            if name.upper().startswith("QUALITY_")
            and name.upper() not in known
            and not re.fullmatch(r"QUALITY_(CONNECTOR_[A-Z0-9_]+|BUSINESS_SECRET_[A-Z0-9_]+)", name)
        )
        if unknown:
            # Values can contain credentials; errors intentionally expose field names only.
            raise ValueError("未知QUALITY配置项，请核对拼写：" + ", ".join(unknown))
        super().__init__(**values)

    @model_validator(mode="after")
    def validate_mode(self):
        from zoneinfo import ZoneInfo

        ZoneInfo(self.site_timezone)
        import math

        if not 1 <= self.session_hours <= self.offline_auth_hours <= 168:
            raise ValueError("会话与离线授权必须为1至168小时，并且会话不能长于授权")
        if (
            self.max_case_tokens < 1
            or not math.isfinite(self.max_case_cost)
            or self.max_case_cost <= 0
            or not math.isfinite(self.model_cost_per_1k)
            or self.model_cost_per_1k < 0
        ):
            raise ValueError("模型Token与成本预算必须有效；价格不能为负")
        if not self.tenant_id or not self.node_id:
            raise ValueError("企业与节点身份必须明确配置")
        if (
            not 0 < self.warning_ratio < 1
            or self.cache_max_bytes < self.max_upload_bytes
            or self.max_upload_bytes < 1
            or self.retention_days < 1
            or not 1 <= self.model_timeout <= 300
            or not 1 <= self.model_concurrency <= 16
            or self.max_model_calls < 1
            or self.max_case_hours < 1
        ):
            raise ValueError("容量、水位、保留期限与调用预算必须为有效正值；模型超时为1至300秒")
        if self.node_role == "site" and not self.site_id:
            raise ValueError("站点必须绑定工厂")
        if self.mode == "production":
            from urllib.parse import urlparse, unquote
            import base64

            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("production 必须使用 PostgreSQL 运行角色")
            if not self.cookie_secure:
                raise ValueError("production 必须启用 Secure cookie 并配置 HTTPS 入口")
            if self.storage_backend != "s3" or not all(
                (self.s3_endpoint, self.s3_access_key, self.s3_secret_key)
            ):
                raise ValueError("production 必须配置独立 S3 对象存储")
            if not self.rabbitmq_url or "guest:guest" in self.rabbitmq_url:
                raise ValueError("production 必须配置非默认 RabbitMQ 身份")
            secrets = [
                unquote(
                    urlparse(self.database_url.replace("postgresql+psycopg", "postgresql")).password or ""
                ),
                unquote(urlparse(self.rabbitmq_url).password or ""),
                self.s3_secret_key,
            ]
            if any(
                len(value) < 20 or value.lower() in {"minioadmin", "changeme", "password", "admin", "secret"}
                for value in secrets
            ) or self.s3_access_key.lower() in {"minioadmin", "admin", "access_key"}:
                raise ValueError("production数据库、消息和对象存储必须使用独立强凭据，拒绝通用默认值")
            if not self.trusted_origins or any(
                urlparse(origin).scheme != "https" for origin in self.trusted_origins
            ):
                raise ValueError("production必须明确配置HTTPS可信来源")
            if self.node_role == "center" and len(self.package_secret) < 32:
                raise ValueError("production 中心知识包签名密钥至少 32 字符")
            if self.node_role == "site" and not self.center_url.startswith("https://"):
                raise ValueError("production站点授权同步必须连接企业HTTPS中心地址")
            if self.node_role == "site" and not self.package_public_key:
                raise ValueError("production 站点必须预置可信中心公钥")
            if self.node_role == "site":
                try:
                    if len(base64.b64decode(self.package_public_key, validate=True)) != 32:
                        raise ValueError("bad key")
                except Exception:
                    raise ValueError("可信中心公钥必须是32字节Ed25519公钥的Base64编码") from None
            if self.database_url == self.migration_database_url:
                raise ValueError("迁移角色与应用运行角色必须分离")
        if self.model_url and not self.model_approved:
            raise ValueError("模型端点必须明确获准；默认不外发数据")
        if not 1 <= self.max_investigations <= 10 or self.session_hours > self.offline_auth_hours:
            raise ValueError("调查/离线授权预算非法")
        return self

    @property
    def sites(self):
        return [self.site_id] if self.node_role == "site" else []
