import csv
import io
import json

from fastapi import Depends, File, Request, UploadFile
from pydantic import ValidationError

from . import schemas as s
from .security import fail, digest
from .storage import validate_file


class ReceiptRow(s.ReceiptInput):
    case_id: str


SCHEMAS = {"masters": s.MasterInput, "lab-results": s.LabInput, "receipts": ReceiptRow}


def register_imports(app, service, command, settings):
    @app.post("/api/import/structured/{kind}/preview")
    async def preview(kind: str, file: UploadFile = File(...), svc=Depends(service)):
        if kind not in SCHEMAS:
            fail(404, "import_kind", "不支持该导入类型")
        raw = await file.read(settings.max_upload_bytes + 1)
        validate_file(file.filename or "", raw, settings.max_upload_bytes)
        try:
            rows = (
                json.loads(raw)
                if file.filename.endswith(".json")
                else list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
            )
            if not isinstance(rows, list) or len(rows) > 2000:
                raise ValueError("应为不超过2000行的记录数组")
        except (ValueError, UnicodeError):
            fail(422, "import_parse", "导入文件格式无效")
        valid, errors = [], []
        for index, row in enumerate(rows):
            try:
                if not isinstance(row, dict):
                    raise ValueError("每行必须是结构化记录")
                for key in ("data", "object_ids", "evidence_ids"):
                    if isinstance(row.get(key), str):
                        row[key] = json.loads(row[key])
                valid.append(SCHEMAS[kind].model_validate(row).model_dump(mode="json"))
            except (ValidationError, ValueError, TypeError) as exc:
                errors.append(
                    {
                        "row": index + 1,
                        "error_type": type(exc).__name__,
                        "message": "字段、类型或JSON结构无效",
                    }
                )
        return {
            "valid": valid,
            "errors": errors,
            "count": len(rows),
            "database_validated": False,
            "commit_allowed": not errors,
            "file_hash": digest(raw),
        }

    @app.post("/api/import/structured/{kind}/commit")
    def commit(kind: str, data: list[dict], request: Request, svc=Depends(service)):
        if kind not in SCHEMAS or len(data) > 2000:
            fail(422, "import_kind", "导入类型或数量无效")

        def apply():
            results = []
            for index, raw in enumerate(data):
                try:
                    row = SCHEMAS[kind].model_validate(raw)
                    if kind == "masters":
                        result = svc.create_master(row)
                    elif kind == "lab-results":
                        result = svc.record_lab(row)
                    else:
                        result = svc.execute(
                            row.case_id, s.ReceiptInput.model_validate(row.model_dump(exclude={"case_id"}))
                        )
                    results.append(result)
                except ValidationError:
                    fail(422, "import_row", f"第{index + 1}行字段校验失败；本批次未提交")
            svc.audit(
                "import.structured",
                request.state.request_id,
                {"kind": kind, "count": len(results), "payload_hash": digest(data)},
            )
            return {"count": len(results), "records": results}

        return command(svc, request, {"kind": kind, "rows": data}, apply)
