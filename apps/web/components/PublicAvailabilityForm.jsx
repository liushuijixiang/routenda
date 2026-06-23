"use client";

import { useEffect, useMemo, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

function localLabel(value) {
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value));
}

export default function PublicAvailabilityForm({ token }) {
  const [poll, setPoll] = useState(null);
  const [selected, setSelected] = useState([]);
  const [contactName, setContactName] = useState("");
  const [note, setNote] = useState("");
  const [noneWork, setNoneWork] = useState(false);
  const [alternativeStart, setAlternativeStart] = useState("");
  const [alternativeEnd, setAlternativeEnd] = useState("");
  const [status, setStatus] = useState("loading");

  useEffect(() => {
    let active = true;
    fetch(`${API_BASE}/api/v1/public/availability/${token}`)
      .then(async (response) => {
        if (!response.ok) throw new Error("链接无效或已过期");
        return response.json();
      })
      .then((data) => {
        if (!active) return;
        setPoll(data);
        setStatus("ready");
      })
      .catch((error) => {
        if (!active) return;
        setStatus(error.message);
      });
    return () => {
      active = false;
    };
  }, [token]);

  const selectedWindows = useMemo(
    () =>
      (poll?.candidate_windows || []).filter((_, index) =>
        selected.includes(index),
      ),
    [poll, selected],
  );

  async function submit(event) {
    event.preventDefault();
    setStatus("submitting");
    const alternativeWindows =
      alternativeStart && alternativeEnd
        ? [
            {
              start: new Date(alternativeStart).toISOString(),
              end: new Date(alternativeEnd).toISOString(),
              timezone_name: "Asia/Shanghai",
              preference: 4,
            },
          ]
        : [];
    const response = await fetch(
      `${API_BASE}/api/v1/public/availability/${token}/submit`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          contact_name: contactName,
          note,
          selected_windows: noneWork ? [] : selectedWindows,
          none_work: noneWork,
          alternative_windows: alternativeWindows,
        }),
      },
    );
    setStatus(response.ok ? "submitted" : "提交失败，请稍后重试");
  }

  if (status === "loading") {
    return <p>正在加载候选时间...</p>;
  }
  if (!poll) {
    return <p role="alert">{status}</p>;
  }
  if (status === "submitted") {
    return <p className="success-message">已提交，协调人将据此更新行程。</p>;
  }

  return (
    <form className="public-form" onSubmit={submit}>
      <header>
        <strong>{poll.supplier_name}</strong>
        <span>{poll.purpose_category}</span>
      </header>

      <fieldset disabled={noneWork}>
        <legend>候选时间</legend>
        <div className="choice-list">
          {poll.candidate_windows.map((window, index) => (
            <label className="choice-row" key={window.start}>
              <input
                type="checkbox"
                checked={selected.includes(index)}
                onChange={(event) =>
                  setSelected((current) =>
                    event.target.checked
                      ? [...current, index]
                      : current.filter((item) => item !== index),
                  )
                }
              />
              <span>
                {localLabel(window.start)} - {localLabel(window.end)}
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <label className="choice-row">
        <input
          type="checkbox"
          checked={noneWork}
          onChange={(event) => setNoneWork(event.target.checked)}
        />
        <span>这些时间都不行</span>
      </label>

      <div className="form-grid">
        <label>
          联系人姓名
          <input
            required
            value={contactName}
            onChange={(event) => setContactName(event.target.value)}
          />
        </label>
        <label>
          其他建议开始
          <input
            type="datetime-local"
            value={alternativeStart}
            onChange={(event) => setAlternativeStart(event.target.value)}
          />
        </label>
        <label>
          其他建议结束
          <input
            type="datetime-local"
            value={alternativeEnd}
            onChange={(event) => setAlternativeEnd(event.target.value)}
          />
        </label>
      </div>

      <label>
        备注
        <textarea
          value={note}
          onChange={(event) => setNote(event.target.value)}
        />
      </label>

      <button
        type="submit"
        disabled={!contactName || (!noneWork && selectedWindows.length === 0)}
      >
        提交时间
      </button>
      {status !== "ready" && status !== "submitting" ? (
        <p role="alert">{status}</p>
      ) : null}
    </form>
  );
}
