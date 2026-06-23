import {
  AlertTriangle,
  CalendarClock,
  CircleHelp,
  Clock3,
  Stamp,
} from "lucide-react";
import {
  apiGet,
  type Appointment,
  type Approval,
  formatDateTime,
  type Requirement,
  statusLabel,
} from "../../lib/server-api";

export const dynamic = "force-dynamic";

export default async function Dashboard() {
  const [requirements, approvals, appointments, conflicts, tasks] =
    await Promise.all([
      apiGet<Requirement[]>("/api/v1/requirements", []),
      apiGet<Approval[]>("/api/v1/approvals", []),
      apiGet<Appointment[]>("/api/v1/appointments", []),
      apiGet<Array<{ id: string; reason: string; status: string }>>(
        "/api/v1/calendars/conflicts",
        [],
      ),
      apiGet<Array<{ id: string; title: string; status: string }>>(
        "/api/v1/tasks?status=open",
        [],
      ),
    ]);
  const queues = [
    {
      label: "待补充",
      count: requirements.filter((item) =>
        ["DRAFT", "NEED_MORE_INFORMATION"].includes(item.status),
      ).length,
      icon: CircleHelp,
    },
    {
      label: "待回复",
      count: requirements.filter((item) => item.status === "WAITING_REPLY")
        .length,
      icon: Clock3,
    },
    {
      label: "待审批",
      count:
        approvals.filter((item) => item.status === "pending").length +
        tasks.length,
      icon: Stamp,
    },
    {
      label: "即将拜访",
      count: appointments.filter((item) => item.status !== "cancelled").length,
      icon: CalendarClock,
    },
    {
      label: "同步异常",
      count: conflicts.filter((item) => item.status === "open").length,
      icon: AlertTriangle,
    },
  ];

  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">今日工作</p>
          <h1>工作台</h1>
        </div>
        <a className="primary-link" href="/requirements/new">
          新建需求
        </a>
      </header>
      <section className="metric-grid" aria-label="工作队列">
        {queues.map(({ label, count, icon: Icon }) => (
          <div className="metric" key={label}>
            <Icon size={19} />
            <span>{label}</span>
            <strong>{count}</strong>
          </div>
        ))}
      </section>
      <section className="section-band">
        <div className="section-heading">
          <h2>最近需求</h2>
          <span>{requirements.length} 项</span>
        </div>
        <div className="data-table" role="table">
          {requirements.slice(0, 8).map((item) => (
            <a
              className="data-row"
              href={`/requirements/${item.id}`}
              key={item.id}
            >
              <div>
                <strong>{item.draft.supplier_name || "未匹配供应商"}</strong>
                <small>{item.draft.purpose_category || "未设置目的"}</small>
              </div>
              <span className={`status status-${item.status.toLowerCase()}`}>
                {statusLabel(item.status)}
              </span>
              <span>{formatDateTime(item.draft.date_start)}</span>
              <span>优先级 {item.draft.priority}</span>
            </a>
          ))}
          {!requirements.length && <div className="empty-state">暂无需求</div>}
        </div>
      </section>
    </main>
  );
}
