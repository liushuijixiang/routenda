from __future__ import annotations

import csv
from difflib import SequenceMatcher
from io import StringIO
import json
from typing import Any

from visit_agent.domain.models import MasterDataChangeRequest
from visit_agent.infrastructure.db.repository import InMemoryRepository


SUPPLIER_COLUMNS = (
    "id",
    "erp_id",
    "legal_name",
    "display_name",
    "aliases",
    "status",
    "source_system",
    "version",
)
REQUIRED_IMPORT_COLUMNS = {"id", "erp_id", "legal_name", "display_name", "status"}


def export_suppliers_csv(repo: InMemoryRepository) -> str:
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=SUPPLIER_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for supplier in sorted(repo.suppliers.values(), key=lambda item: item.erp_id):
        writer.writerow(
            {
                "id": supplier.id,
                "erp_id": supplier.erp_id,
                "legal_name": supplier.legal_name,
                "display_name": supplier.display_name,
                "aliases": json.dumps(supplier.aliases, ensure_ascii=False),
                "status": supplier.status,
                "source_system": supplier.source_system,
                "version": supplier.version,
            }
        )
    return output.getvalue()


def preview_supplier_import(repo: InMemoryRepository, content: str) -> dict[str, Any]:
    reader = csv.DictReader(StringIO(content))
    headers = set(reader.fieldnames or [])
    missing_headers = sorted(REQUIRED_IMPORT_COLUMNS - headers)
    if missing_headers:
        return {
            "valid": False,
            "headers": list(reader.fieldnames or []),
            "row_count": 0,
            "changes": [],
            "unchanged": [],
            "errors": [
                {
                    "row": 1,
                    "field": field,
                    "code": "missing_required_header",
                    "message": f"Missing required CSV header: {field}",
                }
                for field in missing_headers
            ],
        }

    changes: list[dict[str, Any]] = []
    unchanged: list[str] = []
    errors: list[dict[str, Any]] = []
    row_count = 0
    for row_number, row in enumerate(reader, start=2):
        row_count += 1
        stable_id = (row.get("id") or "").strip()
        if not stable_id:
            errors.append(
                {
                    "row": row_number,
                    "field": "id",
                    "code": "stable_id_required",
                    "message": "Stable supplier ID is required for reconciliation imports",
                }
            )
            continue
        supplier = repo.suppliers.get(stable_id)
        if supplier is None:
            errors.append(
                {
                    "row": row_number,
                    "field": "id",
                    "code": "unknown_stable_id",
                    "message": f"Supplier ID does not exist: {stable_id}",
                }
            )
            continue
        erp_id = (row.get("erp_id") or "").strip()
        if erp_id != supplier.erp_id:
            errors.append(
                {
                    "row": row_number,
                    "field": "erp_id",
                    "code": "erp_id_mismatch",
                    "message": "ERP ID cannot be remapped through reconciliation import",
                }
            )
            continue
        proposed = {
            field: (row.get(field) or "").strip()
            for field in ("legal_name", "display_name", "status")
        }
        original = {field: getattr(supplier, field) for field in proposed}
        field_diff = {
            field: {"before": original[field], "after": value}
            for field, value in proposed.items()
            if original[field] != value
        }
        if field_diff:
            changes.append(
                {
                    "row": row_number,
                    "supplier_id": supplier.id,
                    "erp_id": supplier.erp_id,
                    "original": original,
                    "proposed": proposed,
                    "diff": field_diff,
                    "requires_approval": True,
                }
            )
        else:
            unchanged.append(supplier.id)

    return {
        "valid": not errors,
        "headers": list(reader.fieldnames or []),
        "row_count": row_count,
        "changes": changes,
        "unchanged": unchanged,
        "errors": errors,
    }


def create_supplier_change_requests(
    repo: InMemoryRepository,
    content: str,
) -> tuple[dict[str, Any], list[MasterDataChangeRequest]]:
    preview = preview_supplier_import(repo, content)
    if not preview["valid"]:
        return preview, []
    created: list[MasterDataChangeRequest] = []
    for item in preview["changes"]:
        change = MasterDataChangeRequest(
            entity_type="supplier",
            entity_id=str(item["supplier_id"]),
            original_value=dict(item["original"]),
            proposed_value=dict(item["proposed"]),
            source_message_id=None,
        )
        repo.save_master_data_change(change)
        created.append(change)
    return preview, created


def duplicate_supplier_candidates(repo: InMemoryRepository) -> list[dict[str, Any]]:
    suppliers = sorted(repo.suppliers.values(), key=lambda item: item.id)
    candidates: list[dict[str, Any]] = []
    for index, left in enumerate(suppliers):
        left_names = {_normalize(left.legal_name), _normalize(left.display_name)} | {
            _normalize(alias) for alias in left.aliases
        }
        for right in suppliers[index + 1 :]:
            right_names = {_normalize(right.legal_name), _normalize(right.display_name)} | {
                _normalize(alias) for alias in right.aliases
            }
            exact_alias = bool((left_names - {""}) & (right_names - {""}))
            score = SequenceMatcher(
                None,
                _normalize(left.display_name),
                _normalize(right.display_name),
            ).ratio()
            if exact_alias or score >= 0.72:
                candidates.append(
                    {
                        "left": {"id": left.id, "erp_id": left.erp_id, "name": left.display_name},
                        "right": {
                            "id": right.id,
                            "erp_id": right.erp_id,
                            "name": right.display_name,
                        },
                        "score": round(score, 3),
                        "reason": "shared_alias" if exact_alias else "similar_display_name",
                        "automatic_merge": False,
                    }
                )
    return candidates


def _normalize(value: str) -> str:
    return "".join(value.lower().split())
