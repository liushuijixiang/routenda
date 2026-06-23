"use client";

import { MapPin, Search } from "lucide-react";
import { useMemo, useState } from "react";

export default function SupplierDirectory({ suppliers, qualityIssues }) {
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const issueIds = new Set(qualityIssues.map((item) => item.entity_id));
  const visible = useMemo(() => {
    const term = query.trim().toLowerCase();
    return suppliers.filter((supplier) => {
      const matchesStatus = status === "all" || supplier.status === status;
      const haystack = [
        supplier.erp_id,
        supplier.legal_name,
        supplier.display_name,
        ...supplier.aliases,
      ]
        .join(" ")
        .toLowerCase();
      return matchesStatus && (!term || haystack.includes(term));
    });
  }, [query, status, suppliers]);

  return (
    <>
      <div className="toolbar">
        <label className="search-box">
          <Search size={17} />
          <input
            aria-label="搜索供应商"
            placeholder="搜索名称、编码或别名"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        </label>
        <select
          aria-label="供应商状态"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="active">启用</option>
          <option value="inactive">停用</option>
        </select>
      </div>
      <div className="supplier-grid">
        {visible.map((supplier) => (
          <a
            className="supplier-card"
            href={`/suppliers/${supplier.id}`}
            key={supplier.id}
          >
            <div>
              <h2>{supplier.display_name}</h2>
              <p>{supplier.legal_name}</p>
            </div>
            <div className="meta-line">
              <span>{supplier.erp_id}</span>
              <span className={`status status-${supplier.status}`}>
                {supplier.status === "active" ? "启用" : supplier.status}
              </span>
              {issueIds.has(supplier.id) && (
                <span className="quality-flag">
                  <MapPin size={14} /> 数据待核对
                </span>
              )}
            </div>
          </a>
        ))}
      </div>
      {!visible.length && <div className="empty-state">没有匹配的供应商</div>}
    </>
  );
}
