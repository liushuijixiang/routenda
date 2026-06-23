"use client";

import { FileSearch, Upload } from "lucide-react";
import { useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export default function ReconciliationPanel() {
  const [content, setContent] = useState("");
  const [preview, setPreview] = useState(null);
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);

  async function runPreview() {
    setBusy(true);
    const response = await fetch(
      `${API_BASE}/api/v1/reconciliation/suppliers/import-preview`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Role": "coordinator",
        },
        body: JSON.stringify({ content }),
      },
    );
    const data = await response.json();
    setPreview(data);
    setNotice(data.valid ? "预览完成" : "请先修正 CSV 错误");
    setBusy(false);
  }

  async function applyChanges() {
    setBusy(true);
    const response = await fetch(
      `${API_BASE}/api/v1/reconciliation/suppliers/import-apply`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Role": "coordinator",
          "Idempotency-Key": `csv-${crypto.randomUUID()}`,
        },
        body: JSON.stringify({ content }),
      },
    );
    if (response.ok) {
      const data = await response.json();
      setNotice(`已创建 ${data.change_requests.length} 条主数据变更审批`);
    } else {
      setNotice("应用失败，请重新预览");
    }
    setBusy(false);
  }

  return (
    <section className="section-band">
      <div className="section-heading">
        <h2>
          <FileSearch size={17} />
          CSV 对账
        </h2>
      </div>
      <div className="reconciliation-toolbar">
        <label className="file-picker">
          <Upload size={17} />
          选择 CSV
          <input
            type="file"
            accept=".csv,text/csv"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (file) {
                setContent(await file.text());
                setPreview(null);
                setNotice(file.name);
              }
            }}
          />
        </label>
        <button type="button" disabled={!content || busy} onClick={runPreview}>
          生成预览
        </button>
        <button
          type="button"
          disabled={!preview?.valid || !preview?.changes?.length || busy}
          onClick={applyChanges}
        >
          创建变更审批
        </button>
      </div>
      {notice && (
        <div className="inline-notice" role="status">
          {notice}
        </div>
      )}
      {preview && (
        <div className="reconciliation-result">
          <div className="meta-line">
            <strong>{preview.row_count} 行</strong>
            <span>{preview.changes.length} 项变更</span>
            <span>{preview.errors.length} 项错误</span>
          </div>
          {preview.errors.map((error) => (
            <p className="error-line" key={`${error.row}-${error.field}`}>
              第 {error.row} 行 · {error.field} · {error.message}
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
