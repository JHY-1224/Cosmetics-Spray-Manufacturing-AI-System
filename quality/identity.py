from datetime import datetime, timedelta
import hmac

from sqlalchemy import select

from . import models as m
from .security import ROLES, aware, current_time, digest, fail


def local_lease(settings):
    return (
        current_time() + timedelta(hours=settings.offline_auth_hours)
        if settings.node_role == "site" and settings.center_url
        else None
    )


def renew_local_lease(db, settings, username, password, recovery_key, hours, reason):
    """Physical operator recovery; never grants roles, scopes, or central authority."""
    from .security import PASSWORDS, Actor
    from .service import Service

    if (
        settings.node_role != "site"
        or len(settings.local_recovery_key) < 32
        or not hmac.compare_digest(settings.local_recovery_key, recovery_key)
    ):
        fail(403, "recovery_key", "未配置或未通过本地应急凭据校验")
    if not 1 <= hours <= 4 or len(reason.strip()) < 10:
        fail(422, "recovery_bounds", "应急授权限1至4小时，须填写至少10字的原因")
    user = db.scalar(
        select(m.User).where(m.User.tenant_id == settings.tenant_id, m.User.username == username)
    )
    if (
        not user
        or user.auth_origin != "local"
        or not user.active
        or not user.central_active
        or settings.site_id not in user.allowed_sites
        or not PASSWORDS.verify(password, user.password_hash)
    ):
        fail(403, "recovery_identity", "应急流程仅可延长已启用的本地账号，须校验原密码")
    user.lease_expires_at = current_time() + timedelta(hours=hours)
    user.auth_revision += 1
    svc = Service(
        db, settings, Actor(user.id, user.role, user.allowed_sites, user.username, user.allowed_lines)
    )
    svc.audit(
        "identity.emergency_lease",
        user.id,
        {"hours": hours, "reason": reason, "expires_at": user.lease_expires_at.isoformat()},
    )
    return {
        "username": username,
        "expires_at": user.lease_expires_at.isoformat(),
        "role": user.role,
        "scope_unchanged": True,
    }


def validate_identity(user, settings):
    if not user.active or not user.central_active:
        fail(401, "authorization_revoked", "账号授权已撤销")
    if (
        user.auth_origin == "center" or settings.node_role == "site" and settings.center_url
    ) and user.lease_expires_at is None:
        fail(401, "lease_missing", "缺少有效的企业授权租约")
    if user.lease_expires_at and aware(user.lease_expires_at) <= current_time():
        fail(401, "offline_lease_expired", "授权租约已到期，请恢复中心同步或使用受控本地应急流程")


def authorization_snapshot(db, settings, site):
    users = db.scalars(select(m.User).where(m.User.tenant_id == settings.tenant_id)).all()
    issued = current_time()
    rows = []
    for user in users:
        if site.site_id in user.allowed_sites:
            rows.append(
                {
                    "id": user.id,
                    "username": user.username,
                    "role": user.role,
                    "revision": user.auth_revision,
                    "active": user.active,
                    "allowed_sites": user.allowed_sites,
                    "allowed_lines": user.allowed_lines,
                    "password_hash": user.password_hash,
                }
            )
    if len(rows) > 5000:
        fail(
            409,
            "authorization_capacity",
            "授权快照超过首期5000用户容量，须先扩展分页协议，不发送不完整授权集",
        )
    return {
        "issued_at": issued.isoformat(),
        "expires_at": (issued + timedelta(hours=settings.offline_auth_hours)).isoformat(),
        "complete": True,
        "users": rows,
    }


def apply_authorizations(svc, snapshot):
    if not snapshot:
        return {"imported": 0, "revoked": 0, "conflicts": []}
    if len(snapshot.get("users", [])) > 5000:
        fail(422, "authorization_capacity", "授权集超过支持容量")
    issued = aware(datetime.fromisoformat(snapshot["issued_at"]))
    until = min(
        aware(datetime.fromisoformat(snapshot["expires_at"])),
        issued + timedelta(hours=svc.settings.offline_auth_hours),
    )
    if issued > current_time() + timedelta(minutes=2) or until <= current_time():
        fail(409, "authorization_time", "企业授权快照过期或时钟异常")
    imported, revoked, conflicts = 0, 0, []
    covered = set()
    for item in snapshot["users"]:
        if (
            item.get("role") not in ROLES
            or not isinstance(item.get("revision"), int)
            or item["revision"] < 1
            or not str(item.get("password_hash", "")).startswith("$argon2id$")
        ):
            fail(422, "authorization_schema", "企业授权快照结构无效")
        if svc.settings.site_id not in item["allowed_sites"]:
            continue
        local_id = svc.settings.identity_bindings.get(item["id"], item["id"])
        user = svc.db.get(m.User, local_id)
        if user and user.tenant_id != svc.settings.tenant_id:
            fail(403, "authorization_tenant", "授权用户标识与其他企业冲突")
        collision = svc.db.scalar(svc.scoped(m.User).where(m.User.username == item["username"]))
        if (user and user.auth_origin != "center" and item["id"] not in svc.settings.identity_bindings) or (
            collision and collision.id != local_id
        ):
            conflicts.append({"center_user_id": item["id"], "reason": "local_identity_collision"})
            key = "identity-conflict:" + digest([item["id"], item["revision"]])[:40]
            if not svc.db.scalar(
                svc.scoped(m.Command).where(m.Command.actor_id == "identity-sync", m.Command.key == key)
            ):
                svc.audit("identity.binding_conflict", item["id"])
                svc.add(
                    m.Command,
                    key=key,
                    actor_id="identity-sync",
                    fingerprint=digest(item["id"]),
                    response={"status": "conflict"},
                )
            continue
        if user and user.center_revision > item["revision"]:
            conflicts.append({"center_user_id": item["id"], "reason": "stale_authorization"})
            continue
        if user and user.center_revision == item["revision"] and user.auth_origin == "center":
            if (
                user.role != item["role"]
                or user.password_hash != item["password_hash"]
                or user.central_active != item["active"]
                or user.allowed_lines != item["allowed_lines"]
            ):
                conflicts.append({"center_user_id": item["id"], "reason": "same_revision_conflict"})
                continue
            if user.lease_expires_at and issued < aware(user.lease_expires_at) - timedelta(
                hours=svc.settings.offline_auth_hours
            ):
                conflicts.append({"center_user_id": item["id"], "reason": "out_of_order_lease"})
                continue
        if user is None:
            user = svc.add(
                m.User,
                site="",
                id=local_id,
                username=item["username"],
                role=item["role"],
                password_hash=item["password_hash"],
                allowed_sites=[svc.settings.site_id],
                allowed_lines=item["allowed_lines"],
                auth_origin="center",
                center_revision=item["revision"],
                central_active=item["active"],
                lease_expires_at=until,
            )
            imported += 1
        else:
            changed = user.center_revision != item["revision"] or user.auth_origin != "center"
            user.role, user.password_hash = item["role"], item["password_hash"]
            user.allowed_sites, user.allowed_lines = [svc.settings.site_id], item["allowed_lines"]
            user.central_active, user.center_revision, user.auth_origin = (
                item["active"],
                item["revision"],
                "center",
            )
            user.lease_expires_at = until
            if changed:
                user.auth_revision += 1
                svc.audit(
                    "identity.authorization_updated",
                    user.id,
                    {"center_revision": item["revision"], "active": item["active"]},
                )
                imported += 1
        covered.add(user.id)
    if snapshot.get("complete"):
        for user in svc.db.scalars(svc.scoped(m.User).where(m.User.auth_origin == "center")).all():
            if user.id not in covered and user.central_active:
                user.central_active = False
                user.auth_revision += 1
                revoked += 1
                svc.audit("identity.scope_removed", user.id)
    return {"imported": imported, "revoked": revoked, "conflicts": conflicts}
