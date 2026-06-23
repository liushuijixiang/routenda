"use client";

import { useEffect, useMemo, useState } from "react";
import { Model } from "survey-core";
import { Survey } from "survey-react-ui";

const surveyJson = {
  showQuestionNumbers: "off",
  completeText: "提交更新",
  pages: [
    {
      name: "reschedule",
      title: "改期",
      elements: [
        { type: "comment", name: "reason", title: "原因" },
        { type: "text", name: "acceptable_time", title: "可接受新时间" },
        {
          type: "boolean",
          name: "allow_affect_later",
          title: "允许影响后续行程",
        },
      ],
    },
    {
      name: "site",
      title: "更换地点",
      elements: [
        { type: "text", name: "site_id", title: "现有厂区" },
        { type: "text", name: "temporary_address", title: "临时地址" },
        {
          type: "boolean",
          name: "requires_master_data_approval",
          title: "地址是否需要主数据审批",
        },
      ],
    },
    {
      name: "participants",
      title: "变更参与人",
      elements: [
        {
          type: "tagbox",
          name: "required_roles",
          title: "必须角色",
          choices: ["采购", "质量", "工程"],
        },
        {
          type: "tagbox",
          name: "candidate_people",
          title: "候选人员",
          choices: ["王经理", "李工程师", "赵采购"],
        },
        { type: "boolean", name: "allow_substitute", title: "允许替代" },
      ],
    },
  ],
};

const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

export default function RequirementUpdateSurvey({ requirementId = "demo" }) {
  const model = useMemo(() => new Model(surveyJson), []);
  const [result, setResult] = useState("");

  useEffect(() => {
    const complete = async (sender) => {
      const data = sender.data;
      const patch = {};
      if (data.acceptable_time) patch.date_start = data.acceptable_time;
      if (data.site_id) patch.site_id = data.site_id;
      if (data.candidate_people?.length)
        patch.required_people = data.candidate_people;
      if (data.allow_affect_later !== undefined)
        patch.can_move_existing = data.allow_affect_later;
      const response = await fetch(
        `${API_BASE}/api/v1/requirements/${requirementId}`,
        {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            "X-Role": "requester",
            "Idempotency-Key": `survey-${requirementId}-${crypto.randomUUID()}`,
          },
          body: JSON.stringify({ patch, source: "survey_update_wizard" }),
        },
      );
      if (response.ok) {
        const updated = await response.json();
        setResult(
          updated.action ? "更新已进入审批" : `已更新至版本 ${updated.version}`,
        );
      } else {
        const error = await response.json();
        setResult(error.detail?.message || "更新失败");
      }
    };
    model.onComplete.add(complete);
    return () => model.onComplete.remove(complete);
  }, [model, requirementId]);

  return (
    <>
      <Survey model={model} />
      {result && (
        <div className="inline-notice" role="status">
          {result}
        </div>
      )}
    </>
  );
}
