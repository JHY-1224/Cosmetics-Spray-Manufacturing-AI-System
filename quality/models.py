from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from pgvector.sqlalchemy import Vector
from .model_base import Base, Scoped, uid, now  # noqa: F401


class User(Scoped, Base):
    __tablename__ = "users"
    username: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(32))
    allowed_sites: Mapped[list] = mapped_column(JSON, default=list)
    allowed_lines: Mapped[list] = mapped_column(JSON, default=list)
    active: Mapped[bool] = mapped_column(default=True)
    auth_revision: Mapped[int] = mapped_column(default=1)
    auth_origin: Mapped[str] = mapped_column(String(30), default="local")
    center_revision: Mapped[int] = mapped_column(default=0)
    central_active: Mapped[bool] = mapped_column(default=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (UniqueConstraint("tenant_id", "username"),)


class Session(Scoped, Base):
    __tablename__ = "sessions"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    auth_revision: Mapped[int] = mapped_column()


class Master(Scoped, Base):
    __tablename__ = "masters"
    kind: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("masters.id"), nullable=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(default=1)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "kind", "code"),)


class Evidence(Scoped, Base):
    __tablename__ = "evidence"
    filename: Mapped[str] = mapped_column(String(200))
    object_key: Mapped[str] = mapped_column(String(500), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column()
    source: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="available")
    replication_status: Mapped[str] = mapped_column(String(24), default="local", index=True)


class Inspection(Scoped, Base):
    __tablename__ = "inspections"
    source_id: Mapped[str] = mapped_column(String(120))
    record_id: Mapped[str] = mapped_column(String(120))
    line_id: Mapped[str] = mapped_column(String(64), index=True)
    object_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    granularity: Mapped[str] = mapped_column(String(30))
    association: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(40))
    provenance: Mapped[str] = mapped_column(String(24))
    result: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_id", "record_id"),)


class Passage(Scoped, Base):
    __tablename__ = "passages"
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    granularity: Mapped[str] = mapped_column(String(30))
    route_id: Mapped[str] = mapped_column(String(64))
    step_id: Mapped[str] = mapped_column(String(64))
    sequence: Mapped[int] = mapped_column()
    entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    exited_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "object_id", "sequence"),)


class Measurement(Scoped, Base):
    __tablename__ = "measurements"
    source_id: Mapped[str] = mapped_column(String(100))
    record_id: Mapped[str] = mapped_column(String(120))
    equipment_id: Mapped[str] = mapped_column(String(120), index=True)
    metric: Mapped[str] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    value: Mapped[float] = mapped_column()
    unit: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(40))
    value_type: Mapped[str] = mapped_column(String(16))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_id", "record_id", "metric"),)


class Case(Scoped, Base):
    __tablename__ = "cases"
    title: Mapped[str] = mapped_column(String(200))
    line_id: Mapped[str] = mapped_column(String(64), index=True)
    process: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(40), default="accepted", index=True)
    version: Mapped[int] = mapped_column(default=1)
    author_id: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict] = mapped_column(JSON)
    diagnosis: Mapped[dict] = mapped_column(JSON, default=dict)
    rounds: Mapped[int] = mapped_column(default=0)
    model_calls: Mapped[int] = mapped_column(default=0)
    outcome: Mapped[dict] = mapped_column(JSON, default=dict)
    review_flags: Mapped[list] = mapped_column(JSON, default=list)
    anomaly_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    __table_args__ = (Index("ix_case_anomaly", "tenant_id", "site_id", "anomaly_key"),)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class Plan(Scoped, Base):
    __tablename__ = "plans"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    author_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="draft")
    content: Mapped[dict] = mapped_column(JSON)
    fingerprint: Mapped[str] = mapped_column(String(64))
    context_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("case_id", "revision"),)


class Approval(Scoped, Base):
    __tablename__ = "approvals"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    approved: Mapped[bool] = mapped_column()
    reason: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str] = mapped_column(String(64))


class Receipt(Scoped, Base):
    __tablename__ = "receipts"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"), unique=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    content: Mapped[dict] = mapped_column(JSON)
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Retest(Scoped, Base):
    __tablename__ = "retests"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    actor_id: Mapped[str] = mapped_column(String(64))
    result: Mapped[dict] = mapped_column(JSON)
    verdict: Mapped[str] = mapped_column(String(30))


class Knowledge(Scoped, Base):
    __tablename__ = "knowledge"
    category: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft")
    revision: Mapped[int] = mapped_column(default=1)
    author_id: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[dict] = mapped_column(JSON)
    source: Mapped[dict] = mapped_column(JSON)
    rules: Mapped[dict] = mapped_column(JSON, default=dict)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge.id"), nullable=True)
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class KnowledgeVector(Scoped, Base):
    __tablename__ = "knowledge_vectors"
    knowledge_id: Mapped[str] = mapped_column(ForeignKey("knowledge.id"), unique=True)
    embedding: Mapped[list] = mapped_column(Vector(384))
    model_version: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))


class LabResult(Scoped, Base):
    __tablename__ = "lab_results"
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    metric: Mapped[str] = mapped_column(String(100))
    method: Mapped[str] = mapped_column(String(200))
    value: Mapped[str] = mapped_column(String(100))
    unit: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    operator_id: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    data: Mapped[dict] = mapped_column(JSON)


class Route(Scoped, Base):
    __tablename__ = "routes"
    name: Mapped[str] = mapped_column(String(200))
    process: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30), default="reference")
    revision: Mapped[int] = mapped_column(default=1)
    steps: Mapped[list] = mapped_column(JSON)
    disposition: Mapped[dict] = mapped_column(JSON, default=dict)
    author_id: Mapped[str] = mapped_column(String(64))
    previous_id: Mapped[str | None] = mapped_column(ForeignKey("routes.id"), nullable=True)
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Adapter(Scoped, Base):
    __tablename__ = "adapters"
    name: Mapped[str] = mapped_column(String(200))
    protocol: Mapped[str] = mapped_column(String(32))
    config: Mapped[dict] = mapped_column(JSON)
    health: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(default=False)
    provenance: Mapped[str] = mapped_column(String(24))
    source_registry: Mapped[dict] = mapped_column(JSON, default=dict)


class Run(Scoped, Base):
    __tablename__ = "runs"
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id"), index=True)
    status: Mapped[str] = mapped_column(String(32))
    data: Mapped[dict] = mapped_column(JSON)


class Audit(Scoped, Base):
    __tablename__ = "audit"
    actor_id: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100), index=True)
    target_id: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[str] = mapped_column(String(120))
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Command(Scoped, Base):
    __tablename__ = "commands"
    key: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[str] = mapped_column(String(100))
    fingerprint: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON)
    authorization_hash: Mapped[str] = mapped_column(String(64), default='')
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "actor_id", "key"),)


class Outbox(Scoped, Base):
    __tablename__ = "outbox"
    kind: Mapped[str] = mapped_column(String(100))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    last_error: Mapped[str] = mapped_column(Text, default="")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    synced: Mapped[bool] = mapped_column(default=False, index=True)


class Inbox(Scoped, Base):
    __tablename__ = "inbox"
    event_id: Mapped[str] = mapped_column(String(64))
    source_node: Mapped[str] = mapped_column(String(100))
    sequence: Mapped[int] = mapped_column()
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "source_node", "event_id"),)


class Site(Scoped, Base):
    __tablename__ = "sites"
    node_id: Mapped[str] = mapped_column(String(100), unique=True)
    name: Mapped[str] = mapped_column(String(160))
    token_hash: Mapped[str] = mapped_column(String(64))
    active: Mapped[bool] = mapped_column(default=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sequence: Mapped[int] = mapped_column(default=0)
    acknowledged: Mapped[int] = mapped_column(default=0)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class Snapshot(Scoped, Base):
    __tablename__ = "snapshots"
    source_node: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column()
    data: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "source_node", "entity_id"),)


class RemoteRequest(Scoped, Base):
    __tablename__ = "remote_requests"
    author_id: Mapped[str] = mapped_column(String(64))
    target_node: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Disposition(Scoped, Base):
    __tablename__ = "dispositions"
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    route_id: Mapped[str] = mapped_column(ForeignKey("routes.id"))
    case_id: Mapped[str | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="pending")
    author_id: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text)
    cycle: Mapped[int] = mapped_column(default=0)
    return_step_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(default=1)
    data: Mapped[dict] = mapped_column(JSON)
    __mapper_args__ = {"version_id_col": version, "version_id_generator": False}


class Distribution(Scoped, Base):
    __tablename__ = "distributions"
    target_node: Mapped[str] = mapped_column(String(100), index=True)
    author_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(30))
    record_id: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(20))
    generation: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    package: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("tenant_id", "target_node", "kind", "record_id", "generation"),)


class RawRecord(Scoped, Base):
    __tablename__ = "raw_records"
    source_id: Mapped[str] = mapped_column(String(100), index=True)
    record_id: Mapped[str] = mapped_column(String(160))
    source_session_id: Mapped[str] = mapped_column(String(100), default="")
    payload_hash: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(500), unique=True)
    mapping_version: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(32), index=True)
    source_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    normalized_ids: Mapped[list] = mapped_column(JSON, default=list)
    errors: Mapped[list] = mapped_column(JSON, default=list)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_id", "source_session_id", "record_id", "payload_hash"),)


class LearningRecord(Scoped, Base):
    """Versioned, schema-validated expert material; never a trained model artifact."""
    __tablename__ = 'learning_records'
    kind: Mapped[str] = mapped_column(String(30), index=True)
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default='draft', index=True)
    version: Mapped[int] = mapped_column(default=1)
    revision: Mapped[int] = mapped_column(default=1)
    author_id: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    case_id: Mapped[str | None] = mapped_column(ForeignKey('cases.id'), nullable=True, index=True)
    file_id: Mapped[str | None] = mapped_column(ForeignKey('evidence.id'), nullable=True)
    previous_id: Mapped[str | None] = mapped_column(ForeignKey('learning_records.id'), nullable=True)
    knowledge_id: Mapped[str | None] = mapped_column(ForeignKey('knowledge.id'), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    activation: Mapped[dict] = mapped_column(JSON, default=dict)
    __mapper_args__ = {'version_id_col': version, 'version_id_generator': False}
    __table_args__ = (Index('ix_learning_queue', 'tenant_id', 'site_id', 'kind', 'status', 'created_at'),)


class LearningTask(Scoped, Base):
    __tablename__ = 'learning_tasks'
    record_id: Mapped[str] = mapped_column(ForeignKey('learning_records.id'), index=True)
    kind: Mapped[str] = mapped_column(String(30))
    actor_id: Mapped[str] = mapped_column(String(64))
    authorization_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default='queued', index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str] = mapped_column(String(64), default='')
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(100), default='')


class StudyGroup(Scoped, Base):
    """Exclusive assignment of every known source group to one dataset split."""
    __tablename__ = 'study_groups'
    dataset_id: Mapped[str] = mapped_column(ForeignKey('learning_records.id'), index=True)
    group_key: Mapped[str] = mapped_column(String(160))
    split: Mapped[str] = mapped_column(String(30))
    __table_args__ = (UniqueConstraint('tenant_id', 'site_id', 'group_key'),)


# Industrial and business coordination entities share existing tenant/site/line isolation.
from .enterprise_models import (  # noqa: E402,F401
    ENTERPRISE_MODELS, DecisionDefinition, QualityAssessment, ProcessDecision, ProductionCohort, MaterialItem,
    MaterialFormulation, FormulationComponent, FormulationCalculation, MixingBatch, AutomationPolicy,
    PolicyDecision, DigitalTask, DomainApproval, BusinessConnector, BusinessCommand, BusinessCommandAttempt,
    BusinessReceipt, BillOfMaterials, BomComponent, BusinessOrder, BusinessOrderLine, InventorySnapshot,
    SupplyPlan, MaterialDemand, InventoryReservation, ActualMaterialUsage, BusinessDocument,
    BusinessDocumentLine, CostReport, ToolInvocation,
)
