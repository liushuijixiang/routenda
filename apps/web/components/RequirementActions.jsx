"use client";

import { CalendarCheck2, Check, LoaderCircle, Route, Send } from "lucide-react";
import { useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

const actions = {
  READY_TO_CONTACT: { label: "联系供应商", icon: Send, kind: "contact" },
  CANDIDATES_RECEIVED: { label: "生成方案", icon: Route, kind: "plan" },
  TENTATIVE_HOLD: {
    label: "申请最终确认",
    icon: CalendarCheck2,
    kind: "confirm",
  },
};

export default function RequirementActions({
  requirementId,
  status,
  plans,
  appointments,
}) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const draftPlan = plans.find((item) => item.status !== "accepted");
  const tentative = appointments.find((item) => item.status !== "cancelled");
  const action =
    status === "CANDIDATES_RECEIVED" && draftPlan
      ? { label: "接受推荐方案", icon: Check, kind: "accept" }
      : actions[status];

  if (!action) return null;
  const Icon = action.icon;

  async function execute() {
    setBusy(true);
    setNotice("");
    let path = `/api/v1/requirements/${requirementId}/contact`;
    let role = "coordinator";
    let body;
    if (action.kind === "plan") {
      path = "/api/v1/planning/run";
      role = "requester";
      body = { requirement_ids: [requirementId] };
    } else if (action.kind === "accept") {
      path = `/api/v1/planning/${draftPlan.id}/accept`;
    } else if (action.kind === "confirm") {
      if (!tentative) {
        setNotice("未找到可确认的预约");
        setBusy(false);
        return;
      }
      path = `/api/v1/appointments/${tentative.id}/confirm`;
    }
    const response = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Role": role,
        "Idempotency-Key": `web-${action.kind}-${crypto.randomUUID()}`,
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (response.ok) {
      const result = await response.json();
      const approvalRequired =
        result.message === "approval_required" || result.action;
      if (approvalRequired) {
        window.location.assign("/approvals");
      } else {
        window.location.reload();
      }
      return;
    }
    const error = await response.json().catch(() => ({}));
    setNotice(error.detail?.message || "操作失败");
    setBusy(false);
  }

  return (
    <div className="requirement-action">
      <button type="button" onClick={execute} disabled={busy}>
        {busy ? (
          <LoaderCircle className="spin" size={16} />
        ) : (
          <Icon size={16} />
        )}
        {action.label}
      </button>
      {notice && <span role="alert">{notice}</span>}
    </div>
  );
}
