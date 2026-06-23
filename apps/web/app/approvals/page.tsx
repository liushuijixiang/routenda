import ApprovalQueue from "../../components/ApprovalQueue";
import MasterDataQueue from "../../components/MasterDataQueue";
import { apiGet, type Approval } from "../../lib/server-api";

export const dynamic = "force-dynamic";

export default async function Approvals() {
  const [approvals, masterChanges] = await Promise.all([
    apiGet<Approval[]>("/api/v1/approvals", []),
    apiGet<Array<Record<string, unknown>>>(
      "/api/v1/master-data-change-requests",
      [],
    ),
  ]);
  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">人工决策</p>
          <h1>审批</h1>
        </div>
        <span className="header-count">
          {approvals.filter((item) => item.status === "pending").length}{" "}
          项待处理
        </span>
      </header>
      <section className="section-band">
        <div className="section-heading">
          <h2>业务动作</h2>
          <span>{approvals.length}</span>
        </div>
        <ApprovalQueue initialApprovals={approvals} />
      </section>
      <section className="section-band">
        <div className="section-heading">
          <h2>主数据变更</h2>
          <span>{masterChanges.length}</span>
        </div>
        <MasterDataQueue initialChanges={masterChanges} />
      </section>
    </main>
  );
}
