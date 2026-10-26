import hashlib
import hmac
import json
import secrets
import socket
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from fastapi import HTTPException
from pwdlib import PasswordHash

PASSWORDS = PasswordHash.recommended()
ROLES = {
    "viewer": {"read"},
    "inspector": {"read", "case", "inspect", "retest", "file"},
    "engineer": {"read", "case", "inspect", "plan", "diagnose", "knowledge", "file", "master"},
    "approver": {"read", "approve", "knowledge_review", "route_review", "recipe_review"},
    "executor": {"read", "execute", "file"},
    "quality": {"read", "inspect", "file", "retest", "close", "release", "disposition"},
    "admin": {
        "read",
        "case",
        "inspect",
        "retest",
        "file",
        "plan",
        "diagnose",
        "knowledge",
        "master",
        "approve",
        "knowledge_review",
        "route_review",
        "recipe_review",
        "execute",
        "close",
        "release",
        "disposition",
        "admin",
    },
}


# Existing roles retain their authority; new industrial/business actions are explicit.
for _role in ("engineer", "admin"):
    ROLES[_role].update({"industrial_write", "digital_execute"})
for _role in ("approver", "admin"):
    ROLES[_role].update({"industrial_review", "policy_review"})
ROLES["quality"].add("digital_execute")
ROLES["admin"].update({"policy_admin", "digital_execute", "business_write", "business_review", "finance", "supply"})
ROLES["planner"] = {"read", "file", "supply", "business_write", "digital_execute"}
ROLES["storekeeper"] = {"read", "file", "supply", "business_write", "digital_execute"}
ROLES["finance"] = {"read", "file", "finance", "business_review"}
for _role in ("admin", "planner", "storekeeper", "finance"):
    ROLES[_role].add("business_read")


def digest(value):
    if not isinstance(value, bytes):
        value = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        ).encode()
    return hashlib.sha256(value).hexdigest()


def aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def fail(status, code, message):
    raise HTTPException(status_code=status, detail={"code": code, "message": message})


def check_password(password):
    if (
        len(password) < 14
        or len(set(password)) < 7
        or password.lower() in {"demopassword123!", "adminpassword123!"}
    ):
        fail(422, "weak_password", "密码至少14字符且不能使用通用默认密码")


@dataclass
class Actor:
    id: str
    role: str
    sites: list[str]
    username: str = ""
    lines: list[str] | None = None

    def require(self, action, site="", line=""):
        if (
            action not in ROLES.get(self.role, set())
            or (site and site not in self.sites)
            or (line and self.lines and line not in self.lines)
        ):
            fail(403, "forbidden", "没有该业务动作或工厂的权限")


def token():
    return secrets.token_urlsafe(36)


def sign(payload, secret):
    return hmac.new(
        secret.encode(),
        json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(),
        hashlib.sha256,
    ).hexdigest()


def endpoint(url, settings, schemes=("http", "https")):
    parsed = urlparse(url)
    if (
        parsed.scheme not in schemes
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        fail(422, "endpoint_invalid", "连接地址非法；凭据必须单独管理")
    if parsed.hostname not in settings.approved_hosts:
        fail(403, "endpoint_denied", "连接主机未加入受控白名单")
    # Reject link-local metadata even when an administrator mistakenly allowlists it.
    import ipaddress

    for addr in socket.getaddrinfo(parsed.hostname, parsed.port or 80):
        ip = ipaddress.ip_address(addr[4][0])
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            fail(403, "endpoint_denied", "禁止连接元数据、组播和未指定地址")
    return url


def current_time():
    return datetime.now(UTC)
