from __future__ import annotations

from datetime import datetime, time, timedelta
from dataclasses import dataclass, field
from typing import Any, cast
from zoneinfo import ZoneInfo

from visit_agent.domain.models import (
    ItineraryLeg,
    ItineraryPlan,
    VisitRequirement,
    haversine_minutes,
)
from visit_agent.infrastructure.db.repository import InMemoryRepository


ORIGIN_POINT = (31.20, 121.32)
DESTINATION_POINT = (31.1979, 121.3363)
BUFFER_MINUTES = 15
WORKDAY_START = time(8, 0)
WORKDAY_END = time(18, 0)
BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")
Point = tuple[float, float]


@dataclass
class TravelTimes:
    overrides: dict[tuple[Point, Point], int] = field(default_factory=dict)

    def minutes(self, start: Point, end: Point) -> int:
        return self.overrides.get((start, end), haversine_minutes(start, end))


class ItinerarySolver:
    def __init__(self, repo: InMemoryRepository) -> None:
        self.repo = repo
        self._cp_model: Any | None = None
        try:
            from ortools.sat.python import cp_model

            self.solver_name = "ortools"
            self._cp_model = cp_model
        except Exception:
            self.solver_name = "fallback-greedy"

    def plan(
        self,
        requirement_ids: list[str],
        lock_confirmed: bool = True,
        travel_times: TravelTimes | None = None,
        variant: str = "recommended",
    ) -> ItineraryPlan:
        if variant not in {"recommended", "minimal_change"}:
            raise ValueError(f"unknown plan variant: {variant}")
        routes = travel_times or TravelTimes()
        requirements = [self.repo.requirements[item] for item in requirement_ids]
        if self._cp_model:
            placements, unassigned = self._solve_with_ortools(
                requirements,
                lock_confirmed,
                routes,
                variant,
            )
        else:
            placements, unassigned = self._solve_greedy(requirements, routes)
        return self._build_plan(
            requirement_ids, placements, unassigned, lock_confirmed, routes, variant
        )

    def impact_preview(
        self,
        requirement_id: str,
        patch: dict[str, Any],
    ) -> dict[str, Any]:
        affected = [requirement_id]
        if patch.get("date_start") or patch.get("site_id"):
            affected.extend(
                [
                    requirement.id
                    for requirement in self.repo.requirements.values()
                    if requirement.id != requirement_id
                ][:2]
            )
        return {
            "affected_requirements": affected,
            "requires_approval": True,
            "reason": "reschedule may change subsequent route",
        }

    def _solve_with_ortools(
        self,
        requirements: list[VisitRequirement],
        lock_confirmed: bool,
        routes: TravelTimes,
        variant: str,
    ) -> tuple[
        list[tuple[VisitRequirement, datetime, datetime]],
        list[dict[str, str]],
    ]:
        cp_model = cast(Any, self._cp_model)
        model = cp_model.CpModel()
        valid: list[VisitRequirement] = []
        unassigned: list[dict[str, str]] = []
        windows_by_requirement: dict[str, list[Any]] = {}

        for requirement in requirements:
            site = self.repo.sites.get(requirement.draft.site_id or "")
            if not site or (site.geocode_status != "verified" and not site.is_temporary):
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": "site_missing_or_unconfirmed",
                    }
                )
                continue
            windows = sorted(
                [
                    window
                    for window in self.repo.availability
                    if window.requirement_id == requirement.id
                ],
                key=lambda window: window.start,
            )
            if not windows:
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": "no_supplier_availability",
                    }
                )
                continue
            valid.append(requirement)
            windows_by_requirement[requirement.id] = windows

        if not valid:
            return [], unassigned

        sites = {
            requirement.id: self.repo.sites[requirement.draft.site_id or ""]
            for requirement in valid
        }
        first_window_start = min(
            (
                requirement.draft.date_start
                for requirement in valid
                if requirement.draft.date_start is not None
            ),
            default=min(
                window.start for windows in windows_by_requirement.values() for window in windows
            ),
        )
        max_origin_travel = max(
            routes.minutes(
                ORIGIN_POINT,
                (site.latitude, site.longitude),
            )
            for site in sites.values()
        )
        horizon_start = first_window_start - timedelta(minutes=max_origin_travel + BUFFER_MINUTES)
        horizon_end = max(
            window.end for windows in windows_by_requirement.values() for window in windows
        )
        horizon_minutes = max(
            1,
            self._minutes_between(horizon_start, horizon_end) + 24 * 60,
        )

        present: dict[str, Any] = {}
        starts: dict[str, Any] = {}
        ends: dict[str, Any] = {}
        preference_terms: list[Any] = []

        for requirement in valid:
            duration = requirement.draft.duration_minutes or 90
            presence = model.NewBoolVar(f"present_{requirement.id}")
            start = model.NewIntVar(0, horizon_minutes, f"start_{requirement.id}")
            end = model.NewIntVar(0, horizon_minutes + duration, f"end_{requirement.id}")
            model.Add(end == start + duration).OnlyEnforceIf(presence)
            model.Add(start == 0).OnlyEnforceIf(presence.Not())
            model.Add(end == 0).OnlyEnforceIf(presence.Not())
            present[requirement.id] = presence
            starts[requirement.id] = start
            ends[requirement.id] = end

            choices: list[Any] = []
            for index, window in enumerate(windows_by_requirement[requirement.id]):
                choice = model.NewBoolVar(f"window_{requirement.id}_{index}")
                choices.append(choice)
                work_start, work_end = self._working_bounds(window.start)
                window_start = self._minutes_between(horizon_start, max(window.start, work_start))
                window_end = self._minutes_between(horizon_start, min(window.end, work_end))
                model.Add(start >= window_start).OnlyEnforceIf(choice)
                model.Add(end <= window_end).OnlyEnforceIf(choice)
                preference_terms.append(choice * window.preference * 100)
            model.Add(sum(choices) == presence)

            if lock_confirmed and requirement.locked_level != "none":
                locked_start = self._minutes_between(
                    horizon_start,
                    windows_by_requirement[requirement.id][0].start,
                )
                model.Add(presence == 1)
                model.Add(start == locked_start)

            site = sites[requirement.id]
            origin_travel = routes.minutes(
                ORIGIN_POINT,
                (site.latitude, site.longitude),
            )
            model.Add(start >= origin_travel + BUFFER_MINUTES).OnlyEnforceIf(presence)
            return_travel = routes.minutes(
                (site.latitude, site.longitude),
                DESTINATION_POINT,
            )
            if requirement.draft.return_deadline:
                deadline = self._minutes_between(
                    horizon_start,
                    requirement.draft.return_deadline,
                )
                model.Add(end + return_travel <= deadline).OnlyEnforceIf(presence)

            for person in requirement.draft.required_people:
                for busy_index, (busy_start, busy_end) in enumerate(
                    self.repo.people_busy.get(person, [])
                ):
                    before_busy = model.NewBoolVar(
                        f"before_busy_{requirement.id}_{person}_{busy_index}"
                    )
                    model.Add(
                        end <= self._minutes_between(horizon_start, busy_start)
                    ).OnlyEnforceIf([presence, before_busy])
                    model.Add(
                        start >= self._minutes_between(horizon_start, busy_end)
                    ).OnlyEnforceIf([presence, before_busy.Not()])

        travel_terms: list[Any] = []
        for left_index, left in enumerate(valid):
            for right in valid[left_index + 1 :]:
                left_before = model.NewBoolVar(f"{left.id}_before_{right.id}")
                right_before = model.NewBoolVar(f"{right.id}_before_{left.id}")
                model.Add(left_before + right_before <= 1)
                model.Add(left_before + right_before >= present[left.id] + present[right.id] - 1)
                model.Add(left_before <= present[left.id])
                model.Add(left_before <= present[right.id])
                model.Add(right_before <= present[left.id])
                model.Add(right_before <= present[right.id])

                left_site = sites[left.id]
                right_site = sites[right.id]
                left_to_right = routes.minutes(
                    (left_site.latitude, left_site.longitude),
                    (right_site.latitude, right_site.longitude),
                )
                right_to_left = routes.minutes(
                    (right_site.latitude, right_site.longitude),
                    (left_site.latitude, left_site.longitude),
                )
                model.Add(
                    starts[right.id] >= ends[left.id] + left_to_right + BUFFER_MINUTES
                ).OnlyEnforceIf(left_before)
                model.Add(
                    starts[left.id] >= ends[right.id] + right_to_left + BUFFER_MINUTES
                ).OnlyEnforceIf(right_before)
                travel_terms.extend(
                    [
                        left_before * left_to_right,
                        right_before * right_to_left,
                    ]
                )

        priority_score = sum(
            present[requirement.id] * requirement.draft.priority * 100_000 for requirement in valid
        )
        early_score = sum(starts[requirement.id] for requirement in valid)
        change_terms: list[Any] = []
        if variant == "minimal_change":
            for requirement in valid:
                appointment = next(
                    (
                        item
                        for item in self.repo.appointments.values()
                        if item.requirement_id == requirement.id and item.status != "cancelled"
                    ),
                    None,
                )
                if appointment is None:
                    continue
                target = self._minutes_between(horizon_start, appointment.start)
                deviation = model.NewIntVar(
                    0, horizon_minutes * 2, f"appointment_change_{requirement.id}"
                )
                model.Add(deviation >= starts[requirement.id] - target).OnlyEnforceIf(
                    present[requirement.id]
                )
                model.Add(deviation >= target - starts[requirement.id]).OnlyEnforceIf(
                    present[requirement.id]
                )
                model.Add(deviation == 0).OnlyEnforceIf(present[requirement.id].Not())
                change_terms.append(deviation)
        change_penalty = sum(change_terms) * 1_000 if change_terms else 0
        model.Maximize(
            priority_score
            + sum(preference_terms)
            - early_score
            - sum(travel_terms)
            - change_penalty
        )

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = 5.0
        solver.parameters.num_search_workers = 1
        status = solver.Solve(model)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return [], unassigned + [
                {
                    "requirement_id": requirement.id,
                    "reason": "solver_constraint_infeasible",
                }
                for requirement in valid
            ]

        placements: list[tuple[VisitRequirement, datetime, datetime]] = []
        for requirement in valid:
            if solver.Value(present[requirement.id]):
                start = horizon_start + timedelta(
                    minutes=cast(int, solver.Value(starts[requirement.id]))
                )
                end = horizon_start + timedelta(
                    minutes=cast(int, solver.Value(ends[requirement.id]))
                )
                placements.append((requirement, start, end))
            else:
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": self._unassigned_reason(
                            requirement,
                            windows_by_requirement[requirement.id],
                            routes,
                        ),
                    }
                )
        placements.sort(key=lambda item: item[1])
        return placements, unassigned

    def _solve_greedy(
        self,
        requirements: list[VisitRequirement],
        routes: TravelTimes,
    ) -> tuple[
        list[tuple[VisitRequirement, datetime, datetime]],
        list[dict[str, str]],
    ]:
        requirements.sort(
            key=lambda requirement: (
                -requirement.draft.priority,
                requirement.draft.date_start,
            )
        )
        current_point = ORIGIN_POINT
        current_time = min(
            requirement.draft.date_start
            for requirement in requirements
            if requirement.draft.date_start
        )
        placements: list[tuple[VisitRequirement, datetime, datetime]] = []
        unassigned: list[dict[str, str]] = []
        for requirement in requirements:
            site = self.repo.sites.get(requirement.draft.site_id or "")
            if not site or (site.geocode_status != "verified" and not site.is_temporary):
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": "site_missing_or_unconfirmed",
                    }
                )
                continue
            windows = sorted(
                [
                    window
                    for window in self.repo.availability
                    if window.requirement_id == requirement.id
                ],
                key=lambda window: window.start,
            )
            if not windows:
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": "no_supplier_availability",
                    }
                )
                continue
            travel = routes.minutes(
                current_point,
                (site.latitude, site.longitude),
            )
            duration = requirement.draft.duration_minutes or 90
            for window in windows:
                work_start, work_end = self._working_bounds(window.start)
                start = max(
                    current_time + timedelta(minutes=travel + BUFFER_MINUTES),
                    window.start,
                    work_start,
                )
                end = start + timedelta(minutes=duration)
                if end <= min(window.end, work_end) and (
                    not requirement.draft.return_deadline
                    or end <= requirement.draft.return_deadline
                ):
                    placements.append((requirement, start, end))
                    current_point = (site.latitude, site.longitude)
                    current_time = end
                    break
            else:
                unassigned.append(
                    {
                        "requirement_id": requirement.id,
                        "reason": "time_window_or_return_deadline_infeasible",
                    }
                )
        return placements, unassigned

    def _build_plan(
        self,
        requirement_ids: list[str],
        placements: list[tuple[VisitRequirement, datetime, datetime]],
        unassigned: list[dict[str, str]],
        lock_confirmed: bool,
        routes: TravelTimes,
        variant: str,
    ) -> ItineraryPlan:
        current_label = "上海虹桥酒店"
        current_point = ORIGIN_POINT
        current_end: datetime | None = None
        legs: list[ItineraryLeg] = []
        total_travel = 0
        total_wait = 0
        return_margins: list[int] = []
        for requirement, start, end in placements:
            site = self.repo.sites[requirement.draft.site_id or ""]
            travel = routes.minutes(
                current_point,
                (site.latitude, site.longitude),
            )
            if current_end is not None:
                arrival = current_end + timedelta(minutes=travel + BUFFER_MINUTES)
                total_wait += max(
                    0,
                    int((start - arrival).total_seconds() // 60),
                )
            legs.append(
                ItineraryLeg(
                    requirement_id=requirement.id,
                    from_label=current_label,
                    to_label=site.name,
                    start=start,
                    end=end,
                    travel_minutes=travel,
                    buffer_minutes=BUFFER_MINUTES,
                    route_geometry=[
                        current_point,
                        (site.latitude, site.longitude),
                    ],
                )
            )
            if requirement.draft.return_deadline:
                return_travel = routes.minutes(
                    (site.latitude, site.longitude),
                    DESTINATION_POINT,
                )
                return_margins.append(
                    int(
                        (
                            requirement.draft.return_deadline
                            - end
                            - timedelta(minutes=return_travel)
                        ).total_seconds()
                        // 60
                    )
                )
            total_travel += travel
            current_label = site.name
            current_point = (site.latitude, site.longitude)
            current_end = end

        changed_appointments = 0
        for requirement, start, _end in placements:
            if any(
                item.requirement_id == requirement.id
                and item.status != "cancelled"
                and item.start != start
                for item in self.repo.appointments.values()
            ):
                changed_appointments += 1

        return ItineraryPlan(
            objective=(
                "maximize priority; minimize appointment changes, travel, and waiting"
                if variant == "minimal_change"
                else "maximize priority; minimize travel, waiting, and early starts"
            ),
            solver=self.solver_name,
            legs=legs,
            total_travel_minutes=total_travel,
            total_wait_minutes=total_wait,
            total_buffer_minutes=len(legs) * BUFFER_MINUTES,
            changed_appointments=changed_appointments,
            return_margin_minutes=min(return_margins) if return_margins else None,
            requirement_ids=requirement_ids,
            variant=variant,
            unassigned=unassigned,
            explanation_codes=[
                "USES_SUPPLIER_WINDOWS",
                "USES_INTERNAL_BUSY_TIME",
                "USES_ROUTE_MATRIX",
                "USES_RETURN_DEADLINE",
                "LOCK_CONFIRMED" if lock_confirmed else "REPLAN_ALLOWED",
            ],
        )

    def _unassigned_reason(
        self,
        requirement: VisitRequirement,
        windows: list[Any],
        routes: TravelTimes,
    ) -> str:
        duration = timedelta(minutes=requirement.draft.duration_minutes or 90)
        if not any(
            max(window.start, self._working_bounds(window.start)[0]) + duration
            <= min(window.end, self._working_bounds(window.start)[1])
            for window in windows
        ):
            return "time_window_or_return_deadline_infeasible"
        if not any(window.start + duration <= window.end for window in windows):
            return "time_window_or_return_deadline_infeasible"
        if requirement.draft.return_deadline:
            site = self.repo.sites[requirement.draft.site_id or ""]
            return_travel = timedelta(
                minutes=routes.minutes(
                    (site.latitude, site.longitude),
                    DESTINATION_POINT,
                )
            )
            if not any(
                window.start + duration + return_travel <= requirement.draft.return_deadline
                for window in windows
            ):
                return "time_window_or_return_deadline_infeasible"
        return "solver_constraint_infeasible"

    @staticmethod
    def _minutes_between(start: datetime, end: datetime) -> int:
        return int((end - start).total_seconds() // 60)

    @staticmethod
    def _working_bounds(value: datetime) -> tuple[datetime, datetime]:
        local = value.astimezone(BUSINESS_TIMEZONE)
        start = datetime.combine(local.date(), WORKDAY_START, tzinfo=BUSINESS_TIMEZONE)
        end = datetime.combine(local.date(), WORKDAY_END, tzinfo=BUSINESS_TIMEZONE)
        return start.astimezone(value.tzinfo), end.astimezone(value.tzinfo)
