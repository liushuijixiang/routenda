import SupplierDirectory from "../../components/SupplierDirectory";
import ReconciliationPanel from "../../components/ReconciliationPanel";
import { apiGet, publicApiUrl, type Supplier } from "../../lib/server-api";

export const dynamic = "force-dynamic";

export default async function Suppliers() {
  const [suppliers, qualityIssues] = await Promise.all([
    apiGet<Supplier[]>("/api/v1/suppliers", []),
    apiGet<Array<{ entity_id: string }>>("/api/v1/data-quality", []),
  ]);
  return (
    <main>
      <header className="page-header">
        <div>
          <p className="eyebrow">主数据</p>
          <h1>供应商</h1>
        </div>
        <a
          className="secondary-link"
          href={publicApiUrl("/api/v1/reconciliation/suppliers/export")}
        >
          导出对账 CSV
        </a>
      </header>
      <SupplierDirectory suppliers={suppliers} qualityIssues={qualityIssues} />
      <ReconciliationPanel />
    </main>
  );
}
