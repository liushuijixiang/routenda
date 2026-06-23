"use client";

import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";

const baseSchema = {
  title: "VisitRequirementDraft",
  type: "object",
  required: [
    "supplier_id",
    "site_id",
    "date_start",
    "date_end",
    "duration_minutes",
    "origin",
  ],
  properties: {
    supplier_id: { type: "string", title: "供应商", default: "demo-supplier" },
    site_id: { type: "string", title: "厂区", default: "demo-site" },
    purpose_category: {
      type: "string",
      title: "拜访目的",
      default: "质量沟通",
    },
    date_start: {
      type: "string",
      title: "开始时间",
      default: "2026-06-25T09:00:00Z",
    },
    date_end: {
      type: "string",
      title: "结束时间",
      default: "2026-06-26T18:00:00Z",
    },
    duration_minutes: {
      type: "integer",
      title: "时长分钟",
      minimum: 1,
      default: 90,
    },
    priority: {
      type: "integer",
      title: "优先级",
      minimum: 1,
      maximum: 5,
      default: 5,
    },
    required_people: {
      type: "array",
      title: "必需参与人",
      items: { type: "string" },
      default: ["王经理"],
    },
    origin: { type: "string", title: "起点", default: "上海虹桥酒店" },
    destination: { type: "string", title: "终点", default: "上海虹桥机场" },
    return_deadline: {
      type: "string",
      title: "返程截止",
      default: "2026-06-26T18:00:00Z",
    },
    can_move_existing: {
      type: "boolean",
      title: "允许改动既有预约",
      default: false,
    },
  },
};

const uiSchema = {
  required_people: { "ui:options": { orderable: false } },
  can_move_existing: { "ui:widget": "checkbox" },
};

export const initialRequirementData = {
  supplier_id: "demo-supplier",
  site_id: "demo-site",
  purpose_category: "质量沟通",
  date_start: "2026-06-25T09:00:00Z",
  date_end: "2026-06-26T18:00:00Z",
  duration_minutes: 90,
  priority: 5,
  required_people: ["王经理"],
  origin: "上海虹桥酒店",
  destination: "上海虹桥机场",
  return_deadline: "2026-06-26T18:00:00Z",
  can_move_existing: false,
};

export default function RequirementJsonSchemaForm({
  formData = initialRequirementData,
  onChange = () => undefined,
  onSubmit = () => undefined,
  suppliers = [],
  sites = [],
  busy = false,
}) {
  const schema = {
    ...baseSchema,
    properties: {
      ...baseSchema.properties,
      supplier_id: {
        ...baseSchema.properties.supplier_id,
        ...(suppliers.length
          ? {
              oneOf: suppliers.map((item) => ({
                const: item.id,
                title: item.display_name,
              })),
            }
          : {}),
      },
      site_id: {
        ...baseSchema.properties.site_id,
        ...(sites.length
          ? {
              oneOf: sites.map((item) => ({
                const: item.id,
                title: item.name,
              })),
            }
          : {}),
      },
    },
  };
  return (
    <Form
      schema={schema}
      uiSchema={uiSchema}
      validator={validator}
      formData={formData}
      onChange={({ formData: nextData }) => onChange(nextData)}
      onSubmit={({ formData: submitted }) => onSubmit(submitted)}
    >
      <button type="submit" disabled={busy}>
        保存草稿
      </button>
    </Form>
  );
}
