"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

const actionLabels = {
  send_external_message: "发送首次供应商联络",
  modify_confirmed_requirement: "修改已确认需求",
  final_confirm_appointment: "最终确认预约",
  cancel_confirmed_appointment: "取消已确认预约",
  move_confirmed_appointment: "移动已确认预约",
  resolve_calendar_conflict: "处理日历冲突",
};

export default function ApprovalQueue({ initialApprovals }) {
  const [approvals, setApprovals] = useState(initialApprovals);
  const [busy, setBusy] = useState("");

  async function decide(id, decision) {
    setBusy(`${id}:${decision}`);
    const response = await fetch(
      `${API_BASE}/api/v1/approvals/${id}/${decision}`,
      {
        method: "POST",
        headers: {
          "X-Role": "approver",
          "Idempotency-Key": `web-${decision}-${id}`,
        },
      },
    );
    if (response.ok) {
      const updated = await response.json();
      setApprovals((items) =>
        items.map((item) => (item.id === id ? updated : item)),
      );
    }
    setBusy("");
  }

  if (!approvals.length) {
    return <div className="empty-state">当前没有审批记录</div>;
  }

  return (
    <div className="record-list">
      {approvals.map((approval) => (
        <article className="record-row" key={approval.id}>
          <div className="record-main">
            <div className="record-title">
              {actionLabels[approval.action] || approval.action}
            </div>
            <div className="meta-line">
              <span className={`status status-${approval.risk}`}>
                {approval.risk}
              </span>
              <span>审批人：{approval.approver}</span>
              <span>状态：{approval.status}</span>
            </div>
            <pre className="diff-preview">
              {JSON.stringify(approval.impact_preview, null, 2)}
            </pre>
          </div>
          {approval.status === "pending" && (
            <div className="row-actions">
              <button
                className="icon-button approve"
                aria-label="批准"
                title="批准"
                disabled={Boolean(busy)}
                onClick={() => decide(approval.id, "approve")}
              >
                <Check size={17} />
              </button>
              <button
                className="icon-button reject"
                aria-label="拒绝"
                title="拒绝"
                disabled={Boolean(busy)}
                onClick={() => decide(approval.id, "reject")}
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
