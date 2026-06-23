from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from time import monotonic
from typing import Any, Protocol

import httpx

from visit_agent.agent.tools.result import ToolResult
from visit_agent.domain.models import haversine_minutes
from visit_agent.infrastructure.adapters.resilience import CircuitBreaker, resilient_tool_call


class AddressNormalizerPort(Protocol):
    def normalize(self, address: str) -> ToolResult: ...


class GeocoderPort(Protocol):
    async def geocode(self, address: str) -> ToolResult: ...


class RouteMatrixPort(Protocol):
    async def duration_minutes(self, points: list[tuple[float, float]]) -> ToolResult: ...


class BasicAddressNormalizer:
    def normalize(self, address: str) -> ToolResult:
        text = " ".join(address.strip().split())
        return ToolResult.success(
            {
                "normalized": text,
                "confidence": 0.8 if len(text) > 5 else 0.4,
                "provider": "basic",
            }
        )


class LibpostalAddressNormalizer(BasicAddressNormalizer):
    def normalize(self, address: str) -> ToolResult:
        try:
            from postal.parser import parse_address  # type: ignore[import-not-found]
        except ImportError:
            fallback = super().normalize(address)
            fallback.data["provider"] = "basic-libpostal-unavailable"
            return fallback
        parts = parse_address(address)
        normalized = " ".join(value for value, _label in parts)
        return ToolResult.success(
            {
                "normalized": normalized,
                "components": {label: value for value, label in parts},
                "confidence": 0.9,
                "provider": "libpostal",
            }
        )


@dataclass
class MockGeocoder:
    known: dict[str, tuple[float, float]]

    async def geocode(self, address: str) -> ToolResult:
        if address in self.known:
            return ToolResult.success(
                {"point": self.known[address], "confidence": 0.95, "provider": "mock"}
            )
        return ToolResult.success(
            {
                "point": (31.23, 121.47),
                "confidence": 0.45,
                "provider": "mock",
                "needs_human_confirmation": True,
            }
        )


class NominatimGeocoder:
    def __init__(
        self,
        base_url: str,
        user_agent: str,
        *,
        client: httpx.AsyncClient | None = None,
        min_interval_seconds: float = 1.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.breaker = CircuitBreaker("nominatim")
        self.client = client or httpx.AsyncClient(timeout=5.0, trust_env=False)
        self._owns_client = client is None
        self.min_interval_seconds = min_interval_seconds
        self._last_request_at = 0.0
        self._rate_lock = asyncio.Lock()
        self._cache: dict[tuple[str, str, str], dict[str, Any]] = {}

    async def geocode(self, address: str) -> ToolResult:
        normalized = " ".join(address.strip().split())
        key = ("nominatim", normalized, date.today().isoformat())
        cached = self._cache.get(key)
        if cached is not None:
            return ToolResult.success({**cached, "cache_hit": True})

        async def operation() -> ToolResult:
            if not self.user_agent:
                return ToolResult.failure("missing_user_agent", "Nominatim requires a user-agent")
            await self._respect_rate_limit()
            response = await self.client.get(
                f"{self.base_url}/search",
                params={
                    "q": normalized,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "limit": 5,
                },
                headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            )
            if response.status_code == 429 or response.status_code >= 500:
                return ToolResult.failure(
                    "geocoder_unavailable",
                    f"Nominatim returned HTTP {response.status_code}",
                    retryable=True,
                )
            if response.status_code >= 400:
                return ToolResult.failure(
                    "geocoder_rejected", f"Nominatim returned HTTP {response.status_code}"
                )
            rows = response.json()
            if not rows:
                return ToolResult.failure("address_not_found", "Nominatim found no address")
            candidates = [
                {
                    "point": (float(row["lat"]), float(row["lon"])),
                    "display_name": row.get("display_name", ""),
                    "importance": float(row.get("importance", 0.0)),
                    "type": row.get("type"),
                }
                for row in rows
            ]
            top = candidates[0]
            confidence = min(0.99, max(0.3, 0.5 + float(top["importance"])))
            result = {
                "provider": "nominatim",
                "point": top["point"],
                "display_name": top["display_name"],
                "confidence": confidence,
                "candidates": candidates,
                "needs_human_confirmation": len(candidates) > 1 or confidence < 0.75,
                "cache_hit": False,
            }
            self._cache[key] = result
            return ToolResult.success(result)

        return await resilient_tool_call(
            "nominatim.geocode", operation, self.breaker, attempts=2, timeout_seconds=6
        )

    async def _respect_rate_limit(self) -> None:
        async with self._rate_lock:
            elapsed = monotonic() - self._last_request_at
            if elapsed < self.min_interval_seconds:
                await asyncio.sleep(self.min_interval_seconds - elapsed)
            self._last_request_at = monotonic()

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


class HaversineRouteMatrix:
    async def duration_minutes(self, points: list[tuple[float, float]]) -> ToolResult:
        matrix = [[0 for _ in points] for _ in points]
        for i, a in enumerate(points):
            for j, b in enumerate(points):
                matrix[i][j] = 0 if i == j else haversine_minutes(a, b)
        return ToolResult.success(
            {"provider": "haversine-estimate", "profile": "estimated-45-kmh", "matrix": matrix}
        )


class OSRMRouteMatrix(HaversineRouteMatrix):
    def __init__(
        self,
        base_url: str,
        *,
        profile: str = "driving",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.breaker = CircuitBreaker("osrm")
        self.client = client or httpx.AsyncClient(timeout=5.0, trust_env=False)
        self._owns_client = client is None
        self._cache: dict[tuple[Any, ...], dict[str, Any]] = {}

    async def duration_minutes(self, points: list[tuple[float, float]]) -> ToolResult:
        if len(points) < 2:
            return ToolResult.failure(
                "insufficient_points", "At least two route points are required"
            )
        key = self._cache_key("table", points)
        cached = self._cache.get(key)
        if cached is not None:
            return ToolResult.success({**cached, "cache_hit": True})

        async def operation() -> ToolResult:
            if not self.base_url:
                return ToolResult.failure("missing_base_url", "OSRM base URL is not configured")
            response = await self.client.get(
                f"{self.base_url}/table/v1/{self.profile}/{self._coordinates(points)}",
                params={"annotations": "duration,distance"},
            )
            failure = self._http_failure(response, "table")
            if failure:
                return failure
            payload = response.json()
            if payload.get("code") != "Ok" or payload.get("durations") is None:
                return ToolResult.failure("route_unavailable", "OSRM returned no duration matrix")
            result = {
                "provider": "osrm",
                "profile": self.profile,
                "matrix": [
                    [None if value is None else round(float(value) / 60) for value in row]
                    for row in payload["durations"]
                ],
                "distance_meters": payload.get("distances"),
                "cache_hit": False,
            }
            self._cache[key] = result
            return ToolResult.success(result)

        return await resilient_tool_call(
            "osrm.duration_minutes", operation, self.breaker, attempts=2, timeout_seconds=6
        )

    async def route_geometry(
        self,
        start: tuple[float, float],
        end: tuple[float, float],
    ) -> ToolResult:
        points = [start, end]
        key = self._cache_key("route", points)
        cached = self._cache.get(key)
        if cached is not None:
            return ToolResult.success({**cached, "cache_hit": True})

        async def operation() -> ToolResult:
            if not self.base_url:
                return ToolResult.failure("missing_base_url", "OSRM base URL is not configured")
            response = await self.client.get(
                f"{self.base_url}/route/v1/{self.profile}/{self._coordinates(points)}",
                params={"overview": "full", "geometries": "geojson", "steps": "false"},
            )
            failure = self._http_failure(response, "route")
            if failure:
                return failure
            payload = response.json()
            routes = payload.get("routes") or []
            if payload.get("code") != "Ok" or not routes:
                return ToolResult.failure("route_unavailable", "OSRM returned no route")
            route = routes[0]
            result = {
                "provider": "osrm",
                "profile": self.profile,
                "duration_minutes": round(float(route["duration"]) / 60),
                "distance_meters": float(route["distance"]),
                "geometry": route["geometry"]["coordinates"],
                "cache_hit": False,
            }
            self._cache[key] = result
            return ToolResult.success(result)

        return await resilient_tool_call(
            "osrm.route_geometry", operation, self.breaker, attempts=2, timeout_seconds=6
        )

    def _cache_key(self, operation: str, points: list[tuple[float, float]]) -> tuple[Any, ...]:
        rounded = tuple((round(lat, 5), round(lon, 5)) for lat, lon in points)
        return ("osrm", operation, self.profile, rounded, date.today().isoformat())

    @staticmethod
    def _coordinates(points: list[tuple[float, float]]) -> str:
        return ";".join(f"{lon},{lat}" for lat, lon in points)

    @staticmethod
    def _http_failure(response: httpx.Response, operation: str) -> ToolResult | None:
        if response.status_code == 429 or response.status_code >= 500:
            return ToolResult.failure(
                "routing_unavailable",
                f"OSRM {operation} returned HTTP {response.status_code}",
                retryable=True,
            )
        if response.status_code >= 400:
            return ToolResult.failure(
                "routing_rejected", f"OSRM {operation} returned HTTP {response.status_code}"
            )
        return None

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()
