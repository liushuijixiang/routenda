import asyncio
from datetime import datetime
import json
import unittest

import httpx

from visit_agent.domain.models import UTC, Appointment
from visit_agent.infrastructure.adapters.calendar import (
    FeishuCalendarAdapter,
    IcsCalendarAdapter,
    MicrosoftGraphCalendarAdapter,
)
from visit_agent.infrastructure.adapters.erp import ERPNextAdapter, ExcelERPAdapter
from visit_agent.infrastructure.adapters.geo import NominatimGeocoder, OSRMRouteMatrix
from visit_agent.infrastructure.adapters.search import SerperSearchAdapter
from visit_agent.infrastructure.db.repository import InMemoryRepository


class HTTPAdapterTests(unittest.TestCase):
    def test_excel_erp_adapter_reads_csv_as_erp_substitute(self) -> None:
        async def run() -> None:
            repo = InMemoryRepository()
            adapter = ExcelERPAdapter(repo, "../../data/erpnext-suppliers.csv")
            suppliers = await adapter.search_suppliers("Excel")

            self.assertTrue(suppliers.ok)
            self.assertEqual(suppliers.data[0].source_system, "excel")
            self.assertEqual(len(repo.sites), 1)

        asyncio.run(run())

    def test_nominatim_maps_candidates_rate_contract_and_cache(self) -> None:
        async def run() -> None:
            calls = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal calls
                calls += 1
                self.assertEqual(request.url.path, "/search")
                self.assertEqual(request.headers["user-agent"], "visit-agent-tests")
                return httpx.Response(
                    200,
                    json=[
                        {
                            "lat": "31.30",
                            "lon": "120.62",
                            "display_name": "苏州工业园",
                            "importance": 0.35,
                            "type": "industrial",
                        },
                        {
                            "lat": "31.31",
                            "lon": "120.63",
                            "display_name": "苏州工业园东门",
                            "importance": 0.2,
                            "type": "gate",
                        },
                    ],
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = NominatimGeocoder(
                "https://nominatim.test",
                "visit-agent-tests",
                client=client,
                min_interval_seconds=0,
            )
            first = await adapter.geocode("苏州工业园")
            cached = await adapter.geocode("苏州工业园")

            self.assertTrue(first.ok)
            self.assertEqual(first.data["point"], (31.3, 120.62))
            self.assertTrue(first.data["needs_human_confirmation"])
            self.assertTrue(cached.data["cache_hit"])
            self.assertEqual(calls, 1)
            await client.aclose()

        asyncio.run(run())

    def test_osrm_calls_table_and_route_and_caches_by_profile(self) -> None:
        async def run() -> None:
            calls: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if "/table/" in request.url.path:
                    return httpx.Response(
                        200,
                        json={
                            "code": "Ok",
                            "durations": [[0, 3600], [3500, 0]],
                            "distances": [[0, 80000], [79000, 0]],
                        },
                    )
                return httpx.Response(
                    200,
                    json={
                        "code": "Ok",
                        "routes": [
                            {
                                "duration": 3600,
                                "distance": 80000,
                                "geometry": {
                                    "type": "LineString",
                                    "coordinates": [[121.3, 31.2], [120.62, 31.3]],
                                },
                            }
                        ],
                    },
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = OSRMRouteMatrix("https://osrm.test", client=client)
            points = [(31.2, 121.3), (31.3, 120.62)]
            matrix = await adapter.duration_minutes(points)
            cached = await adapter.duration_minutes(points)
            route = await adapter.route_geometry(points[0], points[1])

            self.assertTrue(matrix.ok)
            self.assertEqual(matrix.data["matrix"][0][1], 60)
            self.assertTrue(cached.data["cache_hit"])
            self.assertEqual(route.data["geometry"][-1], [120.62, 31.3])
            self.assertEqual(len(calls), 2)
            self.assertIn("/table/v1/driving/121.3,31.2;120.62,31.3", calls[0])
            await client.aclose()

        asyncio.run(run())

    def test_erpnext_uses_token_auth_field_mapping_and_write_endpoint(self) -> None:
        async def run() -> None:
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                self.assertEqual(request.headers["authorization"], "token key:secret")
                if request.method == "GET":
                    filters = json.loads(request.url.params["filters"])
                    self.assertEqual(filters[0][2], "like")
                    return httpx.Response(
                        200,
                        json={
                            "data": [
                                {"name": "SUP-001", "supplier_name": "苏州安科", "disabled": 0}
                            ]
                        },
                    )
                return httpx.Response(
                    200,
                    json={"data": {"name": "REQ-1", "custom_appointment_status": "confirmed"}},
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = ERPNextAdapter("https://erp.test", "key", "secret", client=client)
            suppliers = await adapter.search_suppliers("安科")
            updated = await adapter.update_visit_status("REQ-1", "confirmed")

            self.assertEqual(suppliers.data[0]["erp_id"], "SUP-001")
            self.assertEqual(suppliers.data[0]["status"], "active")
            self.assertTrue(updated.ok)
            self.assertEqual(requests[1].method, "PUT")
            self.assertEqual(requests[1].url.path, "/api/resource/Visit Requirement/REQ-1")
            await client.aclose()

        asyncio.run(run())

    def test_graph_auth_busy_hold_confirmation_and_token_cache(self) -> None:
        async def run() -> None:
            token_calls = 0
            graph_calls: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal token_calls
                if "login.test" in request.url.host:
                    token_calls += 1
                    return httpx.Response(
                        200, json={"access_token": "access-token", "expires_in": 3600}
                    )
                self.assertEqual(request.headers["authorization"], "Bearer access-token")
                graph_calls.append(request.url.path)
                if request.url.path.endswith("/calendar/getSchedule"):
                    return httpx.Response(
                        200,
                        json={
                            "value": [
                                {
                                    "scheduleId": "person@example.test",
                                    "scheduleItems": [
                                        {
                                            "start": {"dateTime": "2026-06-25T09:00:00"},
                                            "end": {"dateTime": "2026-06-25T10:00:00"},
                                            "status": "busy",
                                        }
                                    ],
                                }
                            ]
                        },
                    )
                return httpx.Response(200, json={"id": "event-1", "@odata.etag": "etag-1"})

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = MicrosoftGraphCalendarAdapter(
                "tenant",
                "client",
                "secret",
                client=client,
                graph_base_url="https://graph.test/v1.0",
                login_base_url="https://login.test",
                calendar_user="coordinator@example.test",
            )
            start = datetime(2026, 6, 25, 1, tzinfo=UTC)
            busy = await adapter.query_busy(["person@example.test"], start, start.replace(hour=5))
            appointment = Appointment(
                requirement_id="requirement-1",
                site_id="site-1",
                start=start,
                end=start.replace(hour=3),
                participants=["person@example.test"],
            )
            hold = await adapter.create_tentative_hold(appointment)
            appointment.calendar_external_event_id = hold.data["external_event_id"]
            confirmed = await adapter.confirm_event(appointment)

            self.assertEqual(len(busy.data["person@example.test"]), 1)
            self.assertEqual(hold.data["etag"], "etag-1")
            self.assertEqual(confirmed.data["status"], "confirmed")
            self.assertEqual(token_calls, 1)
            self.assertEqual(len(graph_calls), 3)
            await client.aclose()

        asyncio.run(run())

    def test_feishu_auth_and_tentative_event(self) -> None:
        async def run() -> None:
            calls: list[str] = []

            def handler(request: httpx.Request) -> httpx.Response:
                calls.append(request.url.path)
                if request.url.path.endswith("/auth/v3/tenant_access_token/internal"):
                    return httpx.Response(
                        200,
                        json={"code": 0, "tenant_access_token": "tenant-token", "expire": 7200},
                    )
                self.assertEqual(request.headers["authorization"], "Bearer tenant-token")
                return httpx.Response(
                    200,
                    json={"code": 0, "data": {"event": {"event_id": "feishu-event-1"}}},
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = FeishuCalendarAdapter(
                "app-id",
                "app-secret",
                base_url="https://feishu.test/open-apis",
                client=client,
            )
            appointment = Appointment(
                requirement_id="requirement-1",
                site_id="site-1",
                start=datetime(2026, 6, 25, 1, tzinfo=UTC),
                end=datetime(2026, 6, 25, 2, tzinfo=UTC),
                participants=[],
            )
            hold = await adapter.create_tentative_hold(appointment)

            self.assertTrue(hold.ok)
            self.assertEqual(hold.data["external_event_id"], "feishu-event-1")
            self.assertEqual(len(calls), 2)
            await client.aclose()

        asyncio.run(run())

    def test_serper_search_uses_api_key_and_normalizes_payload(self) -> None:
        async def run() -> None:
            def handler(request: httpx.Request) -> httpx.Response:
                self.assertEqual(request.headers["x-api-key"], "serper-key")
                self.assertEqual(request.url.path, "/search")
                return httpx.Response(
                    200,
                    json={
                        "organic": [{"title": "result", "link": "https://example.test"}],
                        "answerBox": {"answer": "ok"},
                    },
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            adapter = SerperSearchAdapter("serper-key", "https://serper.test/search", client=client)
            result = await adapter.search("供应商地址")

            self.assertTrue(result.ok)
            self.assertEqual(result.data["organic"][0]["title"], "result")
            self.assertEqual(result.data["answer_box"]["answer"], "ok")
            await client.aclose()

        asyncio.run(run())

    def test_ics_adapter_exports_and_imports_events(self) -> None:
        async def run() -> None:
            adapter = IcsCalendarAdapter({})
            appointment = Appointment(
                requirement_id="requirement-1",
                site_id="site-1",
                start=datetime(2026, 6, 25, 1, tzinfo=UTC),
                end=datetime(2026, 6, 25, 2, tzinfo=UTC),
                participants=[],
            )
            await adapter.create_tentative_hold(appointment)
            exported = adapter.export_ics()
            imported = adapter.import_ics(exported)

            self.assertIn("BEGIN:VEVENT", exported)
            self.assertEqual(imported.data["event_count"], 1)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
