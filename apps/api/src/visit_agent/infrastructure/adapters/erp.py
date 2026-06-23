from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote
from xml.etree import ElementTree
from zipfile import ZipFile

import httpx

from visit_agent.agent.tools.result import ToolResult
from visit_agent.domain.models import Contact, Supplier, SupplierSite
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call
from visit_agent.infrastructure.db.repository import InMemoryRepository


class ERPPort(Protocol):
    async def search_suppliers(self, query: str) -> ToolResult: ...
    async def get_supplier(self, supplier_id: str) -> ToolResult: ...
    async def list_contacts(self, supplier_id: str) -> ToolResult: ...
    async def list_sites(self, supplier_id: str) -> ToolResult: ...
    async def update_visit_status(self, requirement_id: str, status: str) -> ToolResult: ...
    async def propose_or_update_contact(self, payload: dict[str, Any]) -> ToolResult: ...
    async def propose_or_update_site(self, payload: dict[str, Any]) -> ToolResult: ...


class MockERPAdapter:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo

    async def search_suppliers(self, query: str) -> ToolResult:
        data = [
            s
            for s in self.repo.suppliers.values()
            if query in s.display_name or query in s.legal_name
        ]
        return ToolResult.success(data)

    async def get_supplier(self, supplier_id: str) -> ToolResult:
        return ToolResult.success(self.repo.suppliers.get(supplier_id))

    async def list_contacts(self, supplier_id: str) -> ToolResult:
        contact_ids = [a.contact_id for a in self.repo.assignments if a.supplier_id == supplier_id]
        return ToolResult.success([self.repo.contacts[cid] for cid in contact_ids])

    async def list_sites(self, supplier_id: str) -> ToolResult:
        return ToolResult.success(
            [s for s in self.repo.sites.values() if s.supplier_id == supplier_id]
        )

    async def update_visit_status(self, requirement_id: str, status: str) -> ToolResult:
        return ToolResult.success({"requirement_id": requirement_id, "erp_visit_status": status})

    async def propose_or_update_contact(self, payload: dict[str, Any]) -> ToolResult:
        return ToolResult.success({"change_request": payload, "approval_required": True})

    async def propose_or_update_site(self, payload: dict[str, Any]) -> ToolResult:
        return ToolResult.success({"change_request": payload, "approval_required": True})


class ExcelERPAdapter(MockERPAdapter):
    """ERP substitute backed by an Excel-exported CSV or simple .xlsx workbook."""

    def __init__(self, repo: InMemoryRepository, path: str) -> None:
        super().__init__(repo)
        self.path = Path(path)
        self._loaded = False

    async def search_suppliers(self, query: str) -> ToolResult:
        self._ensure_loaded()
        return await super().search_suppliers(query)

    async def get_supplier(self, supplier_id: str) -> ToolResult:
        self._ensure_loaded()
        return await super().get_supplier(supplier_id)

    async def list_contacts(self, supplier_id: str) -> ToolResult:
        self._ensure_loaded()
        return await super().list_contacts(supplier_id)

    async def list_sites(self, supplier_id: str) -> ToolResult:
        self._ensure_loaded()
        return await super().list_sites(supplier_id)

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        if not self.path.exists():
            self._loaded = True
            return
        rows = self._read_rows()
        for row in rows:
            erp_id = row.get("erp_id") or row.get("supplier_code") or row.get("供应商编码")
            display_name = (
                row.get("display_name") or row.get("supplier_name") or row.get("供应商名称")
            )
            if not erp_id or not display_name:
                continue
            supplier = next(
                (item for item in self.repo.suppliers.values() if item.erp_id == erp_id),
                None,
            )
            if supplier is None:
                supplier = Supplier(
                    erp_id=erp_id,
                    legal_name=row.get("legal_name") or display_name,
                    display_name=display_name,
                    aliases=[item for item in (row.get("aliases") or "").split("|") if item],
                    status=row.get("status") or "active",
                    source_system="excel",
                )
                self.repo.suppliers[supplier.id] = supplier
            site_name = row.get("site_name") or row.get("厂区") or "默认厂区"
            if not any(
                site.supplier_id == supplier.id and site.name == site_name
                for site in self.repo.sites.values()
            ):
                site = SupplierSite(
                    supplier_id=supplier.id,
                    name=site_name,
                    raw_address=row.get("address") or row.get("地址") or "",
                    normalized_address=row.get("address") or row.get("地址") or "",
                    latitude=float(row.get("latitude") or row.get("lat") or 31.2304),
                    longitude=float(row.get("longitude") or row.get("lon") or 121.4737),
                    geocode_status=row.get("geocode_status") or "imported",
                )
                self.repo.sites[site.id] = site
            contact_name = row.get("contact_name") or row.get("联系人")
            if contact_name:
                contact = Contact(
                    name=contact_name,
                    emails=[row["email"]] if row.get("email") else [],
                    phones=[row["phone"]] if row.get("phone") else [],
                    status="active",
                )
                self.repo.contacts[contact.id] = contact
        self._loaded = True

    def _read_rows(self) -> list[dict[str, str]]:
        if self.path.suffix.lower() == ".xlsx":
            return self._read_xlsx_rows()
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]

    def _read_xlsx_rows(self) -> list[dict[str, str]]:
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        with ZipFile(self.path) as workbook:
            shared: list[str] = []
            if "xl/sharedStrings.xml" in workbook.namelist():
                root = ElementTree.fromstring(workbook.read("xl/sharedStrings.xml"))
                for item in root.findall("s:si", ns):
                    shared.append("".join(text.text or "" for text in item.findall(".//s:t", ns)))
            sheet = ElementTree.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
        rows: list[list[str]] = []
        for row in sheet.findall(".//s:sheetData/s:row", ns):
            values: list[str] = []
            for cell in row.findall("s:c", ns):
                value = cell.find("s:v", ns)
                if value is None or value.text is None:
                    values.append("")
                elif cell.attrib.get("t") == "s":
                    values.append(shared[int(value.text)])
                else:
                    values.append(value.text)
            rows.append(values)
        if not rows:
            return []
        headers = rows[0]
        return [dict(zip(headers, row, strict=False)) for row in rows[1:]]


@dataclass(frozen=True)
class ERPNextFieldMap:
    supplier_doctype: str = "Supplier"
    contact_doctype: str = "Contact"
    address_doctype: str = "Address"
    visit_doctype: str = "Visit Requirement"
    supplier_name: str = "supplier_name"
    supplier_status: str = "disabled"
    visit_status: str = "custom_appointment_status"


class ERPNextAdapter:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        *,
        field_map: ERPNextFieldMap | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.api_secret = api_secret
        self.fields = field_map or ERPNextFieldMap()
        self.breaker = CircuitBreaker("erpnext")
        self.client = client or httpx.AsyncClient(timeout=5.0, trust_env=False)
        self._owns_client = client is None

    async def search_suppliers(self, query: str) -> ToolResult:
        params = {
            "fields": json.dumps(["name", self.fields.supplier_name, self.fields.supplier_status]),
            "filters": json.dumps(
                [[self.fields.supplier_doctype, self.fields.supplier_name, "like", f"%{query}%"]]
            ),
            "limit_page_length": 20,
        }
        result = await self._request(
            "search_suppliers", "GET", self._resource(self.fields.supplier_doctype), params=params
        )
        if result.ok:
            result.data = [self._map_supplier(item) for item in result.data]
        return result

    async def get_supplier(self, supplier_id: str) -> ToolResult:
        result = await self._request(
            "get_supplier",
            "GET",
            f"{self._resource(self.fields.supplier_doctype)}/{quote(supplier_id, safe='')}",
        )
        if result.ok:
            result.data = self._map_supplier(result.data)
        return result

    async def list_contacts(self, supplier_id: str) -> ToolResult:
        params = {
            "fields": json.dumps(
                ["name", "first_name", "last_name", "email_id", "mobile_no", "links"]
            ),
            "filters": json.dumps([["Contact", "link_name", "=", supplier_id]]),
            "limit_page_length": 100,
        }
        return await self._request(
            "list_contacts", "GET", self._resource(self.fields.contact_doctype), params=params
        )

    async def list_sites(self, supplier_id: str) -> ToolResult:
        params = {
            "fields": json.dumps(
                [
                    "name",
                    "address_title",
                    "address_line1",
                    "address_line2",
                    "city",
                    "state",
                    "country",
                    "pincode",
                    "links",
                ]
            ),
            "filters": json.dumps([["Address", "link_name", "=", supplier_id]]),
            "limit_page_length": 100,
        }
        return await self._request(
            "list_sites", "GET", self._resource(self.fields.address_doctype), params=params
        )

    async def update_visit_status(self, requirement_id: str, status: str) -> ToolResult:
        return await self._request(
            "update_visit_status",
            "PUT",
            f"{self._resource(self.fields.visit_doctype)}/{quote(requirement_id, safe='')}",
            payload={self.fields.visit_status: status},
        )

    async def propose_or_update_contact(self, payload: dict[str, Any]) -> ToolResult:
        return await self._upsert("upsert_contact", self.fields.contact_doctype, payload)

    async def propose_or_update_site(self, payload: dict[str, Any]) -> ToolResult:
        return await self._upsert("upsert_site", self.fields.address_doctype, payload)

    async def _upsert(self, operation: str, doctype: str, payload: dict[str, Any]) -> ToolResult:
        document_name = payload.get("name")
        if document_name:
            return await self._request(
                operation,
                "PUT",
                f"{self._resource(doctype)}/{quote(str(document_name), safe='')}",
                payload=payload,
            )
        return await self._request(operation, "POST", self._resource(doctype), payload=payload)

    async def _request(
        self,
        operation: str,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        async def call() -> ToolResult:
            if not self.base_url or not self.api_key or not self.api_secret:
                return ToolResult.failure(
                    "missing_credentials", "ERPNext credentials are not configured"
                )
            response = await self.client.request(
                method,
                f"{self.base_url}{path}",
                params=params,
                json=payload,
                headers={
                    "Authorization": f"token {self.api_key}:{self.api_secret}",
                    "Accept": "application/json",
                },
            )
            if response.status_code == 429 or response.status_code >= 500:
                return ToolResult.failure(
                    "erp_unavailable",
                    f"ERPNext returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                return ToolResult.failure(
                    "erp_rejected", f"ERPNext returned HTTP {response.status_code}"
                )
            body = response.json()
            return ToolResult.success(body.get("data", body))

        return await resilient_tool_call(
            f"erpnext.{operation}", call, self.breaker, attempts=2, timeout_seconds=6
        )

    def _resource(self, doctype: str) -> str:
        return f"/api/resource/{quote(doctype, safe='')}"

    def _map_supplier(self, item: dict[str, Any]) -> dict[str, Any]:
        return {
            "erp_id": item.get("name"),
            "legal_name": item.get(self.fields.supplier_name) or item.get("name"),
            "status": "inactive" if item.get(self.fields.supplier_status) else "active",
            "raw": item,
        }

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
