const API_BASE =
  process.env.API_INTERNAL_BASE_URL ||
  process.env.NEXT_PUBLIC_API_BASE_URL ||
  "http://127.0.0.1:8000";

export async function apiGet<T>(path: string, fallback: T): Promise<T> {
  try {
    const response = await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      headers: { "X-Role": "admin" },
    });
    if (!response.ok) return fallback;
    return (await response.json()) as T;
  } catch {
    return fallback;
  }
}

export function publicApiUrl(path: string): string {
  const base = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
  return `${base}${path}`;
}

export type Requirement = {
  id: string;
  status: string;
  version: number;
  paused_at: string | null;
  draft: {
    supplier_name: string | null;
    supplier_id: string | null;
    site_id: string | null;
    purpose_category: string | null;
    date_start: string | null;
    date_end: string | null;
    duration_minutes: number | null;
    priority: number;
    required_people: string[];
    origin: string | null;
    return_deadline: string | null;
  };
};

export type Supplier = {
  id: string;
  erp_id: string;
  legal_name: string;
  display_name: string;
  aliases: string[];
  status: string;
  source_system: string;
  version: number;
};

export type Approval = {
  id: string;
  action: string;
  risk: string;
  impact_preview: Record<string, unknown>;
  approver: string;
  status: string;
};

export type Appointment = {
  id: string;
  requirement_id: string;
  start: string;
  end: string;
  participants: string[];
  status: string;
};

export type IntegrationHealth = {
  erp: string;
  calendar: string;
  communication: string;
  geocoding: string;
  routing: string;
  llm: string;
  search: string;
  database: string;
};

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "未设置";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export function statusLabel(status: string): string {
  const labels: Record<string, string> = {
    DRAFT: "草稿",
    NEED_MORE_INFORMATION: "待补充",
    READY_TO_CONTACT: "待联络",
    CONTACTED: "已联络",
    WAITING_REPLY: "待回复",
    CANDIDATES_RECEIVED: "已收时间",
    INTERNAL_APPROVAL: "待内部审批",
    TENTATIVE_HOLD: "临时占位",
    CONFIRMED: "已确认",
    RESCHEDULE_REQUESTED: "改期中",
    CANCELLATION_REQUESTED: "取消中",
    CANCELLED: "已取消",
    COMPLETED: "已完成",
    FAILED: "失败",
  };
  return labels[status] || status;
}
