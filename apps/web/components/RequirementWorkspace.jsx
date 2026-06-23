"use client";

import { Bot, FileCheck2, LoaderCircle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import RequirementJsonSchemaForm, {
  initialRequirementData,
} from "./RequirementJsonSchemaForm";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export default function RequirementWorkspace() {
  const [text, setText] = useState(
    "下周去苏州看 A、B 两家供应商，A 优先，王经理最好参加，周四 18 点前回上海。",
  );
  const [formData, setFormData] = useState(initialRequirementData);
  const [suppliers, setSuppliers] = useState([]);
  const [sites, setSites] = useState([]);
  const [missing, setMissing] = useState([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");

  useEffect(() => {
    fetch(`${API_BASE}/api/v1/suppliers`)
      .then((response) => response.json())
      .then((items) => {
        setSuppliers(items);
        if (items[0]) {
          setFormData((current) => ({ ...current, supplier_id: items[0].id }));
        }
      })
      .catch(() => setNotice("供应商数据暂不可用"));
  }, []);

  useEffect(() => {
    if (!formData.supplier_id || formData.supplier_id === "demo-supplier")
      return;
    fetch(`${API_BASE}/api/v1/suppliers/${formData.supplier_id}/sites`)
      .then((response) => response.json())
      .then((items) => {
        setSites(items);
        if (items[0] && !items.some((item) => item.id === formData.site_id)) {
          setFormData((current) => ({ ...current, site_id: items[0].id }));
        }
      });
  }, [formData.site_id, formData.supplier_id]);

  const selectedSupplier = useMemo(
    () => suppliers.find((item) => item.id === formData.supplier_id),
    [formData.supplier_id, suppliers],
  );

  async function parseText() {
    setBusy(true);
    setNotice("");
    const response = await fetch(`${API_BASE}/api/v1/agent/intake-sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    if (response.ok) {
      const result = await response.json();
      const extracted = Object.fromEntries(
        Object.entries(result.draft).filter(([, value]) => value !== null),
      );
      setFormData((current) => ({ ...current, ...extracted }));
      setMissing(result.missing_slots || []);
      setNotice("解析结果已同步");
    } else {
      setNotice("解析失败，请检查输入");
    }
    setBusy(false);
  }

  async function saveRequirement(submitted) {
    setBusy(true);
    setNotice("");
    const response = await fetch(`${API_BASE}/api/v1/requirements`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Role": "requester",
        "Idempotency-Key": `web-requirement-${crypto.randomUUID()}`,
      },
      body: JSON.stringify({
        ...submitted,
        supplier_name: selectedSupplier?.display_name || null,
      }),
    });
    if (response.ok) {
      const requirement = await response.json();
      window.location.assign(`/requirements/${requirement.id}`);
      return;
    }
    const error = await response.json();
    setNotice(error.detail?.message || "保存失败");
    setBusy(false);
  }

  return (
    <section className="workspace-split">
      <div className="agent-panel">
        <div className="panel-heading">
          <Bot size={18} />
          <h2>Agent 对话</h2>
        </div>
        <textarea
          aria-label="需求描述"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        <button type="button" onClick={parseText} disabled={busy}>
          {busy ? (
            <LoaderCircle className="spin" size={16} />
          ) : (
            <FileCheck2 size={16} />
          )}
          解析需求
        </button>
        <dl className="agent-summary">
          <div>
            <dt>供应商</dt>
            <dd>{selectedSupplier?.display_name || "未匹配"}</dd>
          </div>
          <div>
            <dt>时间</dt>
            <dd>{formData.date_start || "未设置"}</dd>
          </div>
          <div>
            <dt>参与人</dt>
            <dd>{formData.required_people?.join("、") || "未设置"}</dd>
          </div>
          <div>
            <dt>缺失字段</dt>
            <dd>{missing.join("、") || "无"}</dd>
          </div>
        </dl>
        {notice && (
          <div className="inline-notice" role="status">
            {notice}
          </div>
        )}
      </div>
      <div className="form-panel">
        <div className="panel-heading">
          <FileCheck2 size={18} />
          <h2>结构化表单</h2>
        </div>
        <RequirementJsonSchemaForm
          formData={formData}
          onChange={setFormData}
          onSubmit={saveRequirement}
          suppliers={suppliers}
          sites={sites}
          busy={busy}
        />
      </div>
    </section>
  );
}
