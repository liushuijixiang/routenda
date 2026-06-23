import { AlertTriangle, Clock3, Navigation, TimerReset } from "lucide-react";
import ItineraryMap from "../../../components/ItineraryMap";
import { apiGet, formatDateTime } from "../../../lib/server-api";

export const dynamic = "force-dynamic";

type Leg = {
  id: string;
  from_label: string;
  to_label: string;
  start: string;
  end: string;
  travel_minutes: number;
  buffer_minutes: number;
  route_geometry: number[][];
};
type Plan = {
  id: string;
  status: string;
  solver: string;
  total_travel_minutes: number;
  total_wait_minutes: number;
  total_buffer_minutes: number;
  changed_appointments: number;
  return_margin_minutes: number | null;
  explanation_codes: string[];
  unassigned: Array<{ requirement_id: string; reason: string }>;
  legs: Leg[];
};

const fallback: Plan = {
  id: "demo",
  status: "generated",
  solver: "ortools",
  total_travel_minutes: 126,
  total_wait_minutes: 20,
  total_buffer_minutes: 30,
  changed_appointments: 0,
  return_margin_minutes: 95,
  explanation_codes: ["USES_ROUTE_MATRIX"],
  unassigned: [],
  legs: [
    {
      id: "1",
      from_label: "上海虹桥酒店",
      to_label: "苏州安科",
      start: "2026-06-25T02:00:00Z",
      end: "2026-06-25T03:30:00Z",
      travel_minutes: 82,
      buffer_minutes: 15,
      route_geometry: [
        [31.2, 121.32],
        [31.3, 120.62],
      ],
    },
    {
      id: "2",
      from_label: "苏州安科",
      to_label: "昆山博远",
      start: "2026-06-25T05:00:00Z",
      end: "2026-06-25T06:30:00Z",
      travel_minutes: 44,
      buffer_minutes: 15,
      route_geometry: [
        [31.3, 120.62],
        [31.38, 120.98],
      ],
    },
  ],
};

export default async function Itinerary({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const plan = await apiGet<Plan>(`/api/v1/planning/${id}/result`, fallback);
  const points = plan.legs.flatMap((leg, index) =>
    leg.route_geometry
      .slice(index === 0 ? 0 : 1)
      .map(([lat, lng]) => [lng, lat]),
  );
  return (
    <main className="wide-main">
      <header className="page-header">
        <div>
          <p className="eyebrow">{plan.solver}</p>
          <h1>行程方案</h1>
        </div>
        <span className={`status status-${plan.status}`}>{plan.status}</span>
      </header>
      <section className="metric-grid itinerary-metrics">
        <div className="metric">
          <Navigation size={18} />
          <span>交通</span>
          <strong>{plan.total_travel_minutes} 分钟</strong>
        </div>
        <div className="metric">
          <Clock3 size={18} />
          <span>等待</span>
          <strong>{plan.total_wait_minutes} 分钟</strong>
        </div>
        <div className="metric">
          <TimerReset size={18} />
          <span>缓冲</span>
          <strong>{plan.total_buffer_minutes} 分钟</strong>
        </div>
        <div className="metric">
          <AlertTriangle size={18} />
          <span>未安排</span>
          <strong>{plan.unassigned.length}</strong>
        </div>
      </section>
      <section className="itinerary-layout">
        <div className="schedule-list">
          {plan.legs.map((leg, index) => (
            <article className="schedule-row" key={leg.id}>
              <span className="sequence">{index + 1}</span>
              <div>
                <strong>{leg.to_label}</strong>
                <p>
                  {leg.from_label} → {leg.to_label}
                </p>
                <small>
                  {formatDateTime(leg.start)} 至 {formatDateTime(leg.end)}
                </small>
              </div>
              <div className="travel-block">
                <strong>{leg.travel_minutes} 分钟</strong>
                <small>缓冲 {leg.buffer_minutes} 分钟</small>
              </div>
            </article>
          ))}
          {!plan.legs.length && (
            <div className="empty-state">该方案没有已安排拜访</div>
          )}
        </div>
        <ItineraryMap routePoints={points.length > 1 ? points : undefined} />
      </section>
      {plan.unassigned.length > 0 && (
        <section className="warning-band">
          <AlertTriangle size={18} />
          <div>
            <strong>未安排需求</strong>
            {plan.unassigned.map((item) => (
              <p key={item.requirement_id}>
                {item.requirement_id} · {item.reason}
              </p>
            ))}
          </div>
        </section>
      )}
    </main>
  );
}
