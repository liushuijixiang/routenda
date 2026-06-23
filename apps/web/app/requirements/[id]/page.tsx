import {
  CalendarRange,
  History,
  Mail,
  MapPinned,
  Route,
  Users,
} from "lucide-react";
import {
  apiGet,
  formatDateTime,
  type Requirement,
  statusLabel,
} from "../../../lib/server-api";
import RequirementActions from "../../../components/RequirementActions";

export const dynamic = "force-dynamic";

type Revision = {
  id: string;
  version?: number;
  source: string;
  actor: string;
  created_at: string;
  diff: Record<string, unknown>;
};
type Window = {
  id: string;
  participant: string;
  start: string;
  end: string;
  preference: number;
  source: string;
};
type Message = {
  id: string;
  direction: string;
  body: string;
  send_status: string;
  created_at: string;
  parsed_result: Record<string, unknown>;
};
type Plan = {
  id: string;
  status: string;
  solver: string;
  total_travel_minutes: number;
  total_wait_minutes: number;
  unassigned: Array<{ requirement_id: string; reason: string }>;
  legs: Array<{
    to_label: string;
    start: string;
    end: string;
    travel_minutes: number;
  }>;
};
type Appointment = { id: string; requirement_id: string; status: string };

export default async function RequirementDetail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [requirement, revisions, windows, messages, plans, appointments] =
    await Promise.all([
      apiGet<Requirement | null>(`/api/v1/requirements/${id}`, null),
      apiGet<Revision[]>(`/api/v1/requirements/${id}/revisions`, []),
      apiGet<Window[]>(`/api/v1/availability-polls?requirement_id=${id}`, []),
      apiGet<Message[]>(`/api/v1/messages?requirement_id=${id}`, []),
      apiGet<Plan[]>(`/api/v1/planning?requirement_id=${id}`, []),
      apiGet<Appointment[]>(`/api/v1/appointments?requirement_id=${id}`, []),
    ]);
  if (!requirement)
    return (
      <main>
        <div className="empty-state">未找到该需求</div>
      </main>
    );
  const draft = requirement.draft;
  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">需求 v{requirement.version}</p>
          <h1>{draft.supplier_name || "未匹配供应商"}</h1>
        </div>
        <div className="header-actions">
          <span className={`status status-${requirement.status.toLowerCase()}`}>
            {statusLabel(requirement.status)}
          </span>
          <a className="secondary-link" href={`/requirements/${id}/update`}>
            快速更新
          </a>
          <RequirementActions
            requirementId={id}
            status={requirement.status}
            plans={plans}
            appointments={appointments}
          />
        </div>
      </header>
      <section className="detail-strip">
        <div>
          <CalendarRange size={17} />
          <span>时间范围</span>
          <strong>
            {formatDateTime(draft.date_start)} 至{" "}
            {formatDateTime(draft.date_end)}
          </strong>
        </div>
        <div>
          <Users size={17} />
          <span>参与人</span>
          <strong>{draft.required_people.join("、") || "未指定"}</strong>
        </div>
        <div>
          <MapPinned size={17} />
          <span>起点</span>
          <strong>{draft.origin || "未设置"}</strong>
        </div>
        <div>
          <Route size={17} />
          <span>时长</span>
          <strong>
            {draft.duration_minutes
              ? `${draft.duration_minutes} 分钟`
              : "未设置"}
          </strong>
        </div>
      </section>
      <div className="detail-columns">
        <section className="section-band">
          <div className="section-heading">
            <h2>候选时间</h2>
            <span>{windows.length}</span>
          </div>
          <div className="record-list compact">
            {windows.map((item) => (
              <div className="record-row" key={item.id}>
                <div>
                  <strong>{formatDateTime(item.start)}</strong>
                  <small>
                    {item.participant} · 偏好 {item.preference}
                  </small>
                </div>
                <span>{item.source}</span>
              </div>
            ))}
            {!windows.length && (
              <div className="empty-state">尚未收到候选时间</div>
            )}
          </div>
        </section>
        <section className="section-band">
          <div className="section-heading">
            <h2>
              <Mail size={17} />
              消息
            </h2>
            <span>{messages.length}</span>
          </div>
          <div className="timeline">
            {messages.map((item) => (
              <div className="timeline-item" key={item.id}>
                <span className="timeline-dot" />
                <div>
                  <strong>
                    {item.direction === "inbound" ? "供应商回复" : "发出消息"}
                  </strong>
                  <p>{item.body || "无正文"}</p>
                  <small>
                    {formatDateTime(item.created_at)} · {item.send_status}
                  </small>
                </div>
              </div>
            ))}
            {!messages.length && <div className="empty-state">暂无消息</div>}
          </div>
        </section>
      </div>
      <section className="section-band">
        <div className="section-heading">
          <h2>规划方案</h2>
          <span>{plans.length}</span>
        </div>
        <div className="plan-grid">
          {plans.map((plan) => (
            <a
              className="plan-card"
              href={`/itineraries/${plan.id}`}
              key={plan.id}
            >
              <div>
                <strong>
                  {plan.status === "accepted" ? "已接受方案" : "推荐方案"}
                </strong>
                <small>{plan.solver}</small>
              </div>
              <div className="plan-metrics">
                <span>交通 {plan.total_travel_minutes} 分钟</span>
                <span>等待 {plan.total_wait_minutes} 分钟</span>
                <span>{plan.legs.length} 场拜访</span>
              </div>
            </a>
          ))}
          {!plans.length && <div className="empty-state">尚未生成方案</div>}
        </div>
      </section>
      <section className="section-band">
        <div className="section-heading">
          <h2>
            <History size={17} />
            修订历史
          </h2>
          <span>{revisions.length}</span>
        </div>
        <div className="timeline">
          {revisions
            .slice()
            .reverse()
            .map((item) => (
              <div className="timeline-item" key={item.id}>
                <span className="timeline-dot" />
                <div>
                  <strong>{item.source}</strong>
                  <small>
                    {item.actor} · {formatDateTime(item.created_at)}
                  </small>
                  <pre className="diff-preview">
                    {JSON.stringify(item.diff, null, 2)}
                  </pre>
                </div>
              </div>
            ))}
        </div>
      </section>
    </main>
  );
}
