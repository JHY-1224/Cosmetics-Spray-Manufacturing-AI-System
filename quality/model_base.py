"""Shared declarative metadata and immutable scope-column contract."""

import uuid
from datetime import UTC, datetime
from sqlalchemy import DateTime, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def uid():
    return str(uuid.uuid4())


def now():
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Scoped:
    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=uid)
    tenant_id: Mapped[str] = mapped_column(String(80), index=True)
    site_id: Mapped[str] = mapped_column(String(80), default="", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    scope_line_id: Mapped[str] = mapped_column(String(100), default="", index=True)
