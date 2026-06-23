import { CheckCircle2, CircleDashed } from "lucide-react";
import { apiGet, type IntegrationHealth } from "../../../lib/server-api";

export const dynamic = "force-dynamic";

export default async function Integrations() {
  const health = await apiGet<IntegrationHealth>(
    "/api/v1/integrations/health",
    {
      erp: "unavailable",
      calendar: "unavailable",
      communication: "unavailable",
      geocoding: "unavailable",
      routing: "unavailable",
      llm: "unavailable",
      search: "unavailable",
      database: "unavailable",
    },
  );
  const rows = [
    ["ERP", health.erp],
    ["企业日历", health.calendar],
    ["邮件", health.communication],
    ["地理编码", health.geocoding],
    ["路线", health.routing],
    ["LLM", health.llm],
    ["搜索", health.search],
    ["数据库", health.database],
  ];
  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">运行状态</p>
          <h1>集成设置</h1>
        </div>
      </header>
      <section className="section-band">
        <div className="integration-list">
          {rows.map(([name, value]) => {
            const configured = !["mock", "unavailable"].includes(value);
            const Icon = configured ? CheckCircle2 : CircleDashed;
            return (
              <div className="integration-row" key={name}>
                <Icon size={18} />
                <strong>{name}</strong>
                <span>{value}</span>
              </div>
            );
          })}
        </div>
        <p className="muted-note">
          页面仅显示 Adapter 状态，不返回凭据或敏感配置值。
        </p>
      </section>
    </main>
  );
}
