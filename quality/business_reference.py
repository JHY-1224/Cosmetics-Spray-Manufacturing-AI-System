"""Persistent, authenticated HTTP reference counterparty for business-actions-v1.

An integration fixture with real source entities and failure injection. It is never an
adapter to a customer's ERP and does not provide payment or equipment operations.
"""

import argparse
import hmac
import json
import os
import socket
import sqlite3
import uuid
from contextlib import contextmanager
from decimal import Decimal, localcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .business_protocol import BusinessRequest, EFFECTS
from .business_gateway import intent_hash
from .security import current_time, sign


class ReferenceStore:
    def __init__(self, path, secret, admin_secret, authority="reference-wms"):
        self.path, self.secret, self.admin_secret, self.authority = (
            Path(path),
            secret,
            admin_secret,
            authority,
        )
        if min(len(secret), len(admin_secret)) < 16 or secret == admin_secret:
            raise ValueError("reference requires two distinct server-side secrets of at least 16 characters")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with sqlite3.connect(self.path) as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS reference_state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)"
            )
            db.execute(
                "INSERT OR IGNORE INTO reference_state VALUES (1, ?)",
                (
                    json.dumps(
                        {
                            "contract": "business-actions-v1",
                            "nodes": {},
                            "commands": {},
                            "inventory": {},
                            "reservations": {},
                            "work_orders": {},
                            "purchases": {},
                            "applied_count": 0,
                            "fault": "none",
                        }
                    ),
                ),
            )
        self.path.chmod(0o600)

    @contextmanager
    def transaction(self):
        with sqlite3.connect(self.path, timeout=10) as db:
            db.execute("BEGIN IMMEDIATE")
            state = json.loads(db.execute("SELECT body FROM reference_state WHERE id=1").fetchone()[0])
            yield state
            db.execute("UPDATE reference_state SET body=? WHERE id=1", (json.dumps(state),))

    @staticmethod
    def stock_key(identity):
        return json.dumps([identity.material_code, identity.warehouse, identity.lot])

    def receipt(self, request, status, revision, external_id, *, quantity=None, version="", error=""):
        p = request.payload
        with localcontext() as ctx:
            ctx.prec = 128
            amount = (
                p.quantity * p.unit_price * (1 + p.tax_rate if p.tax_basis == "tax_exclusive" else 1)
                if p.action.startswith("purchase.")
                else Decimal(0)
            )
        body = {
            "contract": request.contract,
            "tenant_id": request.tenant_id,
            "site_id": request.site_id,
            "node_id": request.node_id,
            "idempotency_key": request.idempotency_key,
            "input_hash": request.input_hash,
            "action": p.action,
            "target_id": p.target_id,
            "target_version": p.target_version,
            "status": status,
            "source_revision": revision,
            "external_id": external_id,
            "effect": EFFECTS[p.action],
            "quantity": str(p.quantity if quantity is None else quantity),
            "unit": p.unit,
            "amount": str(amount),
            "currency": p.currency,
            "source_version": version,
            "generated_at": current_time().isoformat(),
            "error_code": error,
        }
        return {"body": body, "signature": sign(body, self.secret)}

    def apply(self, state, item, partial=False):
        request = BusinessRequest.model_validate(item["request"])
        p, source = request.payload, request.source_identity
        id, version = item["external_id"], "1"
        q = p.quantity / 2 if partial else p.quantity
        error = ""
        if p.action.startswith("inventory."):
            stock = state["inventory"].get(self.stock_key(source))
            if stock is None or stock["unit"] != p.unit:
                error = "source_inventory_missing_or_unit"
            elif p.action == "inventory.reserve":
                if str(stock["version"]) != source.source_version:
                    error = "source_inventory_version"
                elif Decimal(stock["on_hand"]) - Decimal(stock["held"]) - Decimal(stock["reserved"]) < q:
                    error = "source_inventory_insufficient"
                else:
                    stock["reserved"] = str(Decimal(stock["reserved"]) + q)
                    stock["version"] += 1
                    state["reservations"][id] = {
                        "stock_key": self.stock_key(source),
                        "quantity": str(q),
                        "status": "reserved",
                        "version": 1,
                    }
            else:
                hold = state["reservations"].get(source.reservation_reference)
                if (
                    not hold
                    or hold["status"] != "reserved"
                    or str(hold["version"]) != source.source_version
                    or Decimal(hold["quantity"]) != p.quantity
                    or hold["stock_key"] != self.stock_key(source)
                ):
                    error = "source_reservation_identity_version"
                else:
                    stock["reserved"] = str(Decimal(stock["reserved"]) - q)
                    stock["version"] += 1
                    hold["version"] += 1
                    hold["status"] = "released"
        elif p.action == "retest.create":
            state["work_orders"][id] = {
                "case_reference": source.object_reference,
                "case_version": source.source_version,
                "status": "open",
                "actual_test_result": None,
            }
        elif p.action == "purchase.draft":
            state["purchases"][id] = {
                "order_reference": source.object_reference,
                "order_version": source.source_version,
                "material_code": source.material_code,
                "quantity": str(p.quantity),
                "unit": p.unit,
                "unit_price": str(p.unit_price),
                "currency": p.currency,
                "tax_basis": p.tax_basis,
                "tax_rate": str(p.tax_rate),
                "status": "draft",
                "version": 1,
            }
        elif p.action == "purchase.submit":
            draft = state["purchases"].get(source.draft_reference)
            if (
                not draft
                or draft["status"] != "draft"
                or draft["order_reference"] != source.object_reference
                or draft["order_version"] != source.source_version
                or any(
                    str(getattr(p, k)) != draft[k]
                    for k in ["quantity", "unit", "unit_price", "currency", "tax_basis", "tax_rate"]
                )
            ):
                error = "source_purchase_draft_conflict"
            elif draft["material_code"] != source.material_code:
                error = "source_purchase_material_conflict"
            else:
                draft["status"] = "submitted"
                draft["version"] += 1
                version = str(draft["version"])
                id = source.draft_reference
        if not error:
            state["applied_count"] += 1
        item["receipt"] = self.receipt(
            request,
            "rejected" if error else "partial" if partial else "applied",
            item["receipt"]["body"]["source_revision"] + 1,
            id,
            quantity=q,
            version=version,
            error=error,
        )

    def handle(self, path, body, authorization):
        admin = path.startswith("/admin/")
        secret = self.admin_secret if admin else self.secret
        if not hmac.compare_digest(authorization, "Bearer " + secret):
            return 403, {"error": "authentication_required"}, False
        with self.transaction() as state:
            if path == "/admin/state":
                return 200, state, False
            if path == "/admin/control":
                if set(body) - {"fault", "inventory", "handover"}:
                    return 422, {"error": "unknown_control"}, False
                fault = body.get("fault", "none")
                if fault not in {"none", "accepted", "response_lost", "rejected", "partial"}:
                    return 422, {"error": "unknown_fault"}, False
                state["fault"] = fault
                for row in body.get("inventory", []):
                    if (
                        any(Decimal(str(row[k])) < 0 for k in ["on_hand", "held", "reserved"])
                        or int(row["version"]) < 1
                    ):
                        return 422, {"error": "invalid_inventory"}, False
                    key = json.dumps([row["material_code"], row["warehouse"], row["lot"]])
                    state["inventory"][key] = row
                # Explicit operator handover, never automatic reassignment of a restored node.
                for key, value in body.get("handover", {}).items():
                    state["nodes"][key] = value
                return 200, {"configured": True}, False
            if path == "/admin/tick":
                for item in state["commands"].values():
                    if item["receipt"]["body"]["status"] == "accepted":
                        self.apply(state, item)
                return 200, {"applied_count": state["applied_count"]}, False
            if path not in {"/commands", "/commands/lookup"}:
                return 404, {"error": "fixed_contract_only"}, False
            request = BusinessRequest.model_validate(body)
            if (
                request.source_identity.system != self.authority
                or request.expected_effect != EFFECTS[request.payload.action]
                or intent_hash(request.payload, request.source_identity.model_dump(mode="json"))
                != request.input_hash
            ):
                return 409, {"error": "source_identity_or_input_contract"}, False
            key = json.dumps([request.tenant_id, request.site_id, request.node_id, request.idempotency_key])
            item = state["commands"].get(key)
            if item:
                if item["request"]["input_hash"] != request.input_hash:
                    return 409, {"error": "idempotency_input_conflict"}, False
                return 200, item["receipt"], False
            if path == "/commands/lookup":
                return 200, self.receipt(request, "not_found", 0, ""), False
            node = json.dumps([request.tenant_id, request.site_id, request.node_id])
            if node in state["nodes"] and state["nodes"][node] != request.node_epoch:
                return 409, {"error": "consumer_epoch_fenced_requires_handover"}, False
            state["nodes"][node] = request.node_epoch
            fault, state["fault"] = state["fault"], "none"
            id = "reference-" + uuid.uuid4().hex
            item = {
                "request": request.model_dump(mode="json"),
                "external_id": id,
                "receipt": self.receipt(request, "accepted", 1, id),
            }
            state["commands"][key] = item
            if fault == "rejected":
                item["receipt"] = self.receipt(
                    request, "rejected", 2, id, error="reference_injected_rejection"
                )
            elif fault != "accepted":
                self.apply(state, item, partial=fault == "partial")
            return 200, item["receipt"], fault == "response_lost"


def server(store, host="127.0.0.1", port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("request_size")
                body = json.loads(self.rfile.read(size))
                status, result, lost = store.handle(self.path, body, self.headers.get("Authorization", ""))
            except (ValueError, KeyError, TypeError):
                status, result, lost = 422, {"error": "invalid_typed_contract"}, False
            if lost:
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    return ThreadingHTTPServer((host, port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8099)
    parser.add_argument("--authority", default="reference-wms")
    args = parser.parse_args()
    store = ReferenceStore(
        args.state,
        os.environ["QUALITY_BUSINESS_SECRET_REFERENCE"],
        os.environ["QUALITY_BUSINESS_SECRET_REFERENCE_ADMIN"],
        args.authority,
    )
    server(store, port=args.port).serve_forever()


if __name__ == "__main__":
    main()
