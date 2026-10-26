"""Typed industrial decision and business coordination records; no equipment commands."""

from datetime import datetime
from decimal import Decimal
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .model_base import Base, Scoped

NUMBER = Numeric(28, 9)


class Governed:
    code: Mapped[str] = mapped_column(String(100), index=True)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(30), default="draft", index=True)
    version: Mapped[int] = mapped_column(default=1)
    revision: Mapped[int] = mapped_column(default=1)
    author_id: Mapped[str] = mapped_column(String(64))
    reviewer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    scope: Mapped[dict] = mapped_column(JSON)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list] = mapped_column(JSON)


class DecisionDefinition(Governed, Scoped, Base):
    """Discriminated, schema-validated method/standard/plan/mode definition versions."""

    __tablename__ = "decision_definitions"
    kind: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "kind", "code", "revision"),)


class QualityAssessment(Scoped, Base):
    __tablename__ = "quality_assessments"
    definition_id: Mapped[str | None] = mapped_column(ForeignKey("decision_definitions.id"), nullable=True)
    method_id: Mapped[str | None] = mapped_column(ForeignKey("decision_definitions.id"), nullable=True)
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("decision_definitions.id"), nullable=True)
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    granularity: Mapped[str] = mapped_column(String(30))
    product_id: Mapped[str] = mapped_column(String(100))
    process: Mapped[str] = mapped_column(String(40))
    step_id: Mapped[str] = mapped_column(String(100))
    outcome: Mapped[str] = mapped_column(String(30), index=True)
    version: Mapped[int] = mapped_column(default=1)
    source_kind: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(100))
    source_version: Mapped[str] = mapped_column(String(100))
    original_value: Mapped[str] = mapped_column(Text)
    value: Mapped[Decimal | None] = mapped_column(NUMBER, nullable=True)
    unit: Mapped[str] = mapped_column(String(40))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("quality_assessments.id"), nullable=True)
    __table_args__ = (CheckConstraint("outcome IN ('pass','fail','not_tested','invalid','review')"),)


class ProcessDecision(Scoped, Base):
    __tablename__ = "process_decisions"
    object_id: Mapped[str] = mapped_column(String(120), index=True)
    route_id: Mapped[str] = mapped_column(ForeignKey("routes.id"))
    mode_id: Mapped[str | None] = mapped_column(ForeignKey("decision_definitions.id"), nullable=True)
    from_step_id: Mapped[str] = mapped_column(String(100))
    next_step_id: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30))
    author_id: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)


class ProductionCohort(Scoped, Base):
    __tablename__ = "production_cohorts"
    external_id: Mapped[str] = mapped_column(String(120))
    granularity: Mapped[str] = mapped_column(String(30))
    product_id: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(default=1)
    expected_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    closed: Mapped[bool] = mapped_column(default=False)
    object_ids: Mapped[list] = mapped_column(JSON)
    ledger: Mapped[dict] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "external_id"),)


class MaterialItem(Scoped, Base):
    __tablename__ = "material_items"
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    unit: Mapped[str] = mapped_column(String(20))
    density_kg_l: Mapped[Decimal | None] = mapped_column(NUMBER, nullable=True)
    version: Mapped[int] = mapped_column(default=1)
    quality_requirements: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "code"),)


class MaterialFormulation(Governed, Scoped, Base):
    __tablename__ = "material_formulations"
    ratio_basis: Mapped[str] = mapped_column(String(20))
    dilution_basis: Mapped[str] = mapped_column(String(30))
    dilution_fraction: Mapped[Decimal] = mapped_column(NUMBER)
    resolution_kg: Mapped[Decimal] = mapped_column(NUMBER)
    tolerance_fraction: Mapped[Decimal] = mapped_column(NUMBER)
    pot_life_minutes: Mapped[int] = mapped_column()
    technical_version: Mapped[str] = mapped_column(String(100))
    prohibited_pairs: Mapped[list] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "code", "revision"),)


class FormulationComponent(Scoped, Base):
    __tablename__ = "formulation_components"
    formulation_id: Mapped[str] = mapped_column(ForeignKey("material_formulations.id"), index=True)
    material_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"))
    role: Mapped[str] = mapped_column(String(30))
    parts: Mapped[Decimal] = mapped_column(NUMBER)
    approved_substitute_ids: Mapped[list] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("formulation_id", "material_id"), CheckConstraint("parts > 0"))


class FormulationCalculation(Scoped, Base):
    __tablename__ = "formulation_calculations"
    formulation_id: Mapped[str] = mapped_column(ForeignKey("material_formulations.id"), index=True)
    formulation_version: Mapped[int] = mapped_column()
    author_id: Mapped[str] = mapped_column(String(64))
    basis: Mapped[str] = mapped_column(String(20))
    target_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    target_unit: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)


class MixingBatch(Scoped, Base):
    __tablename__ = "mixing_batches"
    calculation_id: Mapped[str] = mapped_column(ForeignKey("formulation_calculations.id"))
    external_id: Mapped[str] = mapped_column(String(120))
    author_id: Mapped[str] = mapped_column(String(64))
    mixed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30))
    snapshot: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "external_id"),)


class AutomationPolicy(Governed, Scoped, Base):
    __tablename__ = "automation_policies"
    priority: Mapped[int] = mapped_column(default=0)
    effect: Mapped[str] = mapped_column(String(10), default="allow")
    automation_level: Mapped[str] = mapped_column(String(3))
    actor_ids: Mapped[list] = mapped_column(JSON)
    actor_roles: Mapped[list] = mapped_column(JSON)
    actions: Mapped[list] = mapped_column(JSON)
    object_ids: Mapped[list] = mapped_column(JSON)
    max_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    max_amount: Mapped[Decimal] = mapped_column(NUMBER)
    currency: Mapped[str] = mapped_column(String(10))
    window_seconds: Mapped[int] = mapped_column()
    max_frequency: Mapped[int] = mapped_column()
    offline_allowed: Mapped[bool] = mapped_column(default=False)
    required_evidence_kinds: Mapped[list] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "code", "revision"),)


class PolicyDecision(Scoped, Base):
    __tablename__ = "policy_decisions"
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("automation_policies.id"), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(nullable=True)
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(120))
    allowed: Mapped[bool] = mapped_column()
    input_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    reasons: Mapped[list] = mapped_column(JSON)


class DigitalTask(Scoped, Base):
    __tablename__ = "digital_tasks"
    kind: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    policy_id: Mapped[str] = mapped_column(ForeignKey("automation_policies.id"))
    policy_decision_id: Mapped[str] = mapped_column(ForeignKey("policy_decisions.id"))
    responsibility: Mapped[str] = mapped_column(String(160))
    authorization_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    version: Mapped[int] = mapped_column(default=1)
    attempts: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    input: Mapped[dict] = mapped_column(JSON)
    target_snapshot: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(100), default="")
    batch_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    __table_args__ = (Index("ix_digital_queue", "tenant_id", "site_id", "status", "next_at"),)


class DomainApproval(Scoped, Base):
    __tablename__ = "domain_approvals"
    resource_kind: Mapped[str] = mapped_column(String(40))
    resource_id: Mapped[str] = mapped_column(String(100), index=True)
    resource_version: Mapped[int] = mapped_column()
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    approved: Mapped[bool] = mapped_column()
    fingerprint: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class BusinessConnector(Scoped, Base):
    __tablename__ = "business_connectors"
    name: Mapped[str] = mapped_column(String(200))
    system: Mapped[str] = mapped_column(String(20))
    endpoint: Mapped[str] = mapped_column(String(500))
    credential_reference: Mapped[str] = mapped_column(String(100))
    actions: Mapped[list] = mapped_column(JSON)
    enabled: Mapped[bool] = mapped_column(default=False)
    supports_idempotency: Mapped[bool] = mapped_column(default=False)
    supports_lookup: Mapped[bool] = mapped_column(default=False)
    version: Mapped[int] = mapped_column(default=1)
    contract_version: Mapped[str] = mapped_column(String(40), default="business-actions-v1")
    authority: Mapped[str] = mapped_column(String(120))


class BusinessCommand(Scoped, Base):
    __tablename__ = "business_commands"
    connector_id: Mapped[str] = mapped_column(ForeignKey("business_connectors.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("digital_tasks.id"), nullable=True)
    action: Mapped[str] = mapped_column(String(80))
    contract_version: Mapped[str] = mapped_column(String(40))
    idempotency_key: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    policy_id: Mapped[str | None] = mapped_column(ForeignKey("automation_policies.id"), nullable=True)
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("domain_approvals.id"), nullable=True)
    target_id: Mapped[str] = mapped_column(String(120))
    target_version: Mapped[str] = mapped_column(String(100))
    input_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    expected_result: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="proposed", index=True)
    version: Mapped[int] = mapped_column(default=1)
    fencing: Mapped[int] = mapped_column(default=0)
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    external_id: Mapped[str] = mapped_column(String(160), default="")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str] = mapped_column(String(100), default="")
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "connector_id", "idempotency_key"),)


class BusinessCommandAttempt(Scoped, Base):
    __tablename__ = "business_command_attempts"
    command_id: Mapped[str] = mapped_column(ForeignKey("business_commands.id"), index=True)
    fencing: Mapped[int] = mapped_column()
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30))
    request_hash: Mapped[str] = mapped_column(String(64))
    response: Mapped[dict] = mapped_column(JSON, default=dict)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BusinessReceipt(Scoped, Base):
    __tablename__ = "business_receipts"
    command_id: Mapped[str] = mapped_column(ForeignKey("business_commands.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(160))
    source_revision: Mapped[int] = mapped_column()
    status: Mapped[str] = mapped_column(String(30))
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint("command_id", "source_revision"),)


class BillOfMaterials(Governed, Scoped, Base):
    __tablename__ = "bills_of_materials"
    product_id: Mapped[str] = mapped_column(String(100))
    product_version: Mapped[str] = mapped_column(String(100))
    output_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    output_unit: Mapped[str] = mapped_column(String(20))
    route_id: Mapped[str] = mapped_column(ForeignKey("routes.id"))
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "code", "revision"),)


class BomComponent(Scoped, Base):
    __tablename__ = "bom_components"
    bom_id: Mapped[str] = mapped_column(ForeignKey("bills_of_materials.id"), index=True)
    material_id: Mapped[str | None] = mapped_column(ForeignKey("material_items.id"), nullable=True)
    child_bom_id: Mapped[str | None] = mapped_column(ForeignKey("bills_of_materials.id"), nullable=True)
    quantity: Mapped[Decimal] = mapped_column(NUMBER)
    unit: Mapped[str] = mapped_column(String(20))
    loss_fraction: Mapped[Decimal] = mapped_column(NUMBER)
    conversion_factor: Mapped[Decimal] = mapped_column(NUMBER)
    conversion_basis: Mapped[str] = mapped_column(String(500))
    __table_args__ = (
        CheckConstraint("(material_id IS NULL) <> (child_bom_id IS NULL)"),
        CheckConstraint("quantity > 0"),
    )


class BusinessOrder(Scoped, Base):
    __tablename__ = "business_orders"
    external_id: Mapped[str] = mapped_column(String(120))
    customer_reference: Mapped[str] = mapped_column(String(120))
    source_system: Mapped[str] = mapped_column(String(100))
    source_version: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(default=1)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    currency: Mapped[str] = mapped_column(String(10))
    tax_basis: Mapped[str] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_system", "external_id"),)


class BusinessOrderLine(Scoped, Base):
    __tablename__ = "business_order_lines"
    order_id: Mapped[str] = mapped_column(ForeignKey("business_orders.id"), index=True)
    external_line: Mapped[str] = mapped_column(String(100))
    product_id: Mapped[str] = mapped_column(String(100))
    product_version: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[Decimal] = mapped_column(NUMBER)
    unit: Mapped[str] = mapped_column(String(20))
    bom_id: Mapped[str] = mapped_column(ForeignKey("bills_of_materials.id"))
    version: Mapped[int] = mapped_column(default=1)
    __table_args__ = (UniqueConstraint("order_id", "external_line"), CheckConstraint("quantity > 0"))


class InventorySnapshot(Scoped, Base):
    __tablename__ = "inventory_snapshots"
    material_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"), index=True)
    warehouse: Mapped[str] = mapped_column(String(100))
    lot: Mapped[str] = mapped_column(String(120))
    quality_status: Mapped[str] = mapped_column(String(30))
    quantity_basis: Mapped[str] = mapped_column(String(20))
    on_hand: Mapped[Decimal] = mapped_column(NUMBER)
    held: Mapped[Decimal] = mapped_column(NUMBER)
    reserved: Mapped[Decimal] = mapped_column(NUMBER)
    in_transit: Mapped[Decimal] = mapped_column(NUMBER)
    reported_available: Mapped[Decimal | None] = mapped_column(NUMBER, nullable=True)
    unit: Mapped[str] = mapped_column(String(20))
    source_system: Mapped[str] = mapped_column(String(100))
    source_version: Mapped[str] = mapped_column(String(100))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    fresh_seconds: Mapped[int] = mapped_column()
    evidence_ids: Mapped[list] = mapped_column(JSON)
    included_command_ids: Mapped[list] = mapped_column(JSON)
    quality_assessment_id: Mapped[str | None] = mapped_column(
        ForeignKey("quality_assessments.id"), nullable=True
    )
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "site_id", "source_system", "material_id", "warehouse", "lot", "source_version"
        ),
    )


class SupplyPlan(Scoped, Base):
    __tablename__ = "supply_plans"
    order_id: Mapped[str] = mapped_column(ForeignKey("business_orders.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(30))
    version: Mapped[int] = mapped_column(default=1)
    snapshot: Mapped[dict] = mapped_column(JSON)
    delivery_assessment: Mapped[dict] = mapped_column(JSON)


class MaterialDemand(Scoped, Base):
    __tablename__ = "material_demands"
    plan_id: Mapped[str] = mapped_column(ForeignKey("supply_plans.id"), index=True)
    order_line_id: Mapped[str] = mapped_column(ForeignKey("business_order_lines.id"))
    material_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"))
    required_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    available_quantity: Mapped[Decimal] = mapped_column(NUMBER)
    shortage: Mapped[Decimal] = mapped_column(NUMBER)
    unit: Mapped[str] = mapped_column(String(20))
    calculation: Mapped[dict] = mapped_column(JSON)


class InventoryReservation(Scoped, Base):
    __tablename__ = "inventory_reservations"
    plan_id: Mapped[str | None] = mapped_column(ForeignKey("supply_plans.id"), nullable=True)
    inventory_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshots.id"), index=True)
    quantity: Mapped[Decimal] = mapped_column(NUMBER)
    status: Mapped[str] = mapped_column(String(30))
    command_id: Mapped[str | None] = mapped_column(ForeignKey("business_commands.id"), nullable=True)
    source_reference: Mapped[str] = mapped_column(String(160), default="")
    version: Mapped[int] = mapped_column(default=1)
    __table_args__ = (CheckConstraint("quantity > 0"),)


class ActualMaterialUsage(Scoped, Base):
    __tablename__ = "actual_material_usage"
    material_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"))
    inventory_id: Mapped[str] = mapped_column(ForeignKey("inventory_snapshots.id"))
    mixing_batch_id: Mapped[str | None] = mapped_column(ForeignKey("mixing_batches.id"), nullable=True)
    order_line_id: Mapped[str | None] = mapped_column(ForeignKey("business_order_lines.id"), nullable=True)
    source_id: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(NUMBER)
    unit: Mapped[str] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(30))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    author_id: Mapped[str] = mapped_column(String(64))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    deviation_reason: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_id"), CheckConstraint("quantity > 0"))


class BusinessDocument(Scoped, Base):
    __tablename__ = "business_documents"
    kind: Mapped[str] = mapped_column(String(30))
    external_id: Mapped[str] = mapped_column(String(120))
    source_system: Mapped[str] = mapped_column(String(100))
    order_id: Mapped[str] = mapped_column(ForeignKey("business_orders.id"))
    currency: Mapped[str] = mapped_column(String(10))
    tax_basis: Mapped[str] = mapped_column(String(20))
    tax_rate: Mapped[Decimal] = mapped_column(NUMBER)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    evidence_ids: Mapped[list] = mapped_column(JSON)
    author_id: Mapped[str] = mapped_column(String(64))
    __table_args__ = (UniqueConstraint("tenant_id", "site_id", "source_system", "kind", "external_id"),)


class BusinessDocumentLine(Scoped, Base):
    __tablename__ = "business_document_lines"
    document_id: Mapped[str] = mapped_column(ForeignKey("business_documents.id"), index=True)
    external_line: Mapped[str] = mapped_column(String(100))
    order_line_id: Mapped[str] = mapped_column(ForeignKey("business_order_lines.id"))
    material_id: Mapped[str] = mapped_column(ForeignKey("material_items.id"))
    lot: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(NUMBER)
    unit: Mapped[str] = mapped_column(String(20))
    unit_price: Mapped[Decimal] = mapped_column(NUMBER)
    reference_line_id: Mapped[str | None] = mapped_column(
        ForeignKey("business_document_lines.id"), nullable=True
    )
    __table_args__ = (
        UniqueConstraint("document_id", "external_line"),
        CheckConstraint("quantity >= 0"),
        CheckConstraint("unit_price >= 0"),
    )


class CostReport(Scoped, Base):
    __tablename__ = "cost_reports"
    order_id: Mapped[str] = mapped_column(ForeignKey("business_orders.id"), index=True)
    author_id: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(10))
    allocation_version: Mapped[str] = mapped_column(String(100))
    actual_amount: Mapped[Decimal | None] = mapped_column(NUMBER, nullable=True)
    estimated_amount: Mapped[Decimal | None] = mapped_column(NUMBER, nullable=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    reconciliation: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30))


class ToolInvocation(Scoped, Base):
    __tablename__ = "tool_invocations"
    actor_id: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(40))
    tool: Mapped[str] = mapped_column(String(100))
    tool_version: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    input_hash: Mapped[str] = mapped_column(String(64))
    input: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    authorization: Mapped[dict] = mapped_column(JSON)
    evidence_ids: Mapped[list] = mapped_column(JSON)


ENTERPRISE_MODELS = [
    DecisionDefinition,
    QualityAssessment,
    ProcessDecision,
    ProductionCohort,
    MaterialItem,
    MaterialFormulation,
    FormulationComponent,
    FormulationCalculation,
    MixingBatch,
    AutomationPolicy,
    PolicyDecision,
    DigitalTask,
    DomainApproval,
    BusinessConnector,
    BusinessCommand,
    BusinessCommandAttempt,
    BusinessReceipt,
    BillOfMaterials,
    BomComponent,
    BusinessOrder,
    BusinessOrderLine,
    InventorySnapshot,
    SupplyPlan,
    MaterialDemand,
    InventoryReservation,
    ActualMaterialUsage,
    BusinessDocument,
    BusinessDocumentLine,
    CostReport,
    ToolInvocation,
]
