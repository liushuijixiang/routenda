"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export default function MasterDataQueue({ initialChanges }) {
  const [changes, setChanges] = useState(initialChanges);

  async function decide(id, decision) {
    const response = await fetch(
      `${API_BASE}/api/v1/master-data-change-requests/${id}/${decision}`,
      {
        method: "POST",
        headers: {
          "X-Role": "approver",
          "Idempotency-Key": `master-${decision}-${id}`,
        },
      },
    );
    if (response.ok) {
      const updated = await response.json();
      setChanges((items) =>
        items.map((item) => (item.id === id ? updated : item)),
      );
    }
  }

  if (!changes.length)
    return <div className="empty-state">没有主数据变更请求</div>;
  return (
    <div className="record-list">
      {changes.map((change) => (
        <article className="record-row" key={change.id}>
          <div className="record-main">
            <div className="record-title">
              {change.entity_type} · {change.entity_id}
            </div>
            <div className="meta-line">
              <span className="status status-high">{change.risk}</span>
              <span>{change.approval_status}</span>
            </div>
            <pre className="diff-preview">
              {JSON.stringify(
                { before: change.original_value, after: change.proposed_value },
                null,
                2,
              )}
            </pre>
          </div>
          {change.approval_status === "pending" && (
            <div className="row-actions">
              <button
                className="icon-button approve"
                aria-label="批准主数据变更"
                title="批准"
                onClick={() => decide(change.id, "approve")}
              >
                <Check size={17} />
              </button>
              <button
                className="icon-button reject"
                aria-label="拒绝主数据变更"
                title="拒绝"
                onClick={() => decide(change.id, "reject")}
              >
                <X size={17} />
              </button>
            </div>
          )}
        </article>
      ))}
    </div>
  );
}
