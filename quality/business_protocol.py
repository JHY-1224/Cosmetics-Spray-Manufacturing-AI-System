"""Fixed business HTTP contract. No arbitrary operations, SQL, equipment writes or payment actions."""

from typing import Literal
from pydantic import Field
from .schemas import Strict
from .enterprise_schemas import BusinessActionPayload, Nonnegative, Timestamp

EFFECTS = {
    "retest.create": "work_order_created",
    "inventory.reserve": "inventory_reserved",
    "inventory.release": "inventory_released",
    "purchase.draft": "purchase_draft_created",
    "purchase.submit": "purchase_submitted",
}


class SourceIdentity(Strict):
    system: str = Field(min_length=1, max_length=120)
    object_reference: str = Field(min_length=1, max_length=160)
    source_version: str = Field(min_length=1, max_length=100)
    material_code: str = Field(default="", max_length=100)
    warehouse: str = Field(default="", max_length=100)
    lot: str = Field(default="", max_length=120)
    reservation_reference: str = Field(default="", max_length=160)
    draft_reference: str = Field(default="", max_length=160)


class BusinessRequest(Strict):
    contract: Literal["business-actions-v1"] = "business-actions-v1"
    tenant_id: str = Field(min_length=1, max_length=100)
    site_id: str = Field(min_length=1, max_length=100)
    node_id: str = Field(min_length=1, max_length=100)
    node_epoch: str = Field(min_length=16, max_length=100)
    idempotency_key: str = Field(min_length=16, max_length=120)
    input_hash: str = Field(min_length=64, max_length=64)
    fencing: int = Field(ge=1)
    payload: BusinessActionPayload
    source_identity: SourceIdentity
    expected_effect: str = Field(min_length=1, max_length=60)


class BusinessReceiptBody(Strict):
    contract: Literal["business-actions-v1"] = "business-actions-v1"
    tenant_id: str
    site_id: str
    node_id: str
    idempotency_key: str = Field(min_length=16, max_length=120)
    input_hash: str = Field(min_length=64, max_length=64)
    action: Literal[
        "retest.create", "inventory.reserve", "inventory.release", "purchase.draft", "purchase.submit"
    ]
    target_id: str
    target_version: str
    status: Literal["accepted", "applied", "partial", "rejected", "not_found"]
    source_revision: int = Field(ge=0)
    external_id: str = Field(max_length=160)
    effect: str = Field(max_length=60)
    quantity: Nonnegative
    unit: str = Field(max_length=20)
    amount: Nonnegative = 0
    currency: str = Field(default="CNY", max_length=10)
    source_version: str = Field(default="", max_length=100)
    generated_at: Timestamp
    error_code: str = Field(default="", max_length=100)


class SignedBusinessReceipt(Strict):
    body: BusinessReceiptBody
    signature: str = Field(min_length=64, max_length=64)
