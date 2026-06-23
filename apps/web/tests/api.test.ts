import { readFileSync } from "node:fs";

import { describe, expect, test } from "vitest";

import { demoCards, operationalEndpoints, pages } from "../lib/api.js";

const requiredRoutes = [
  "/dashboard",
  "/requirements/new",
  "/requirements/[id]",
  "/requirements/[id]/update",
  "/suppliers",
  "/suppliers/[id]",
  "/itineraries/[id]",
  "/approvals",
  "/public/availability/[token]",
  "/settings/integrations",
];

describe("web application inventory", () => {
  test("declares every required page", () => {
    expect(pages).toEqual(expect.arrayContaining(requiredRoutes));
  });

  test("declares every dashboard work queue", () => {
    expect(demoCards).toEqual(
      expect.arrayContaining([
        "待补充",
        "待回复",
        "待审批",
        "即将拜访",
        "同步异常",
      ]),
    );
  });

  test("declares live operational API dependencies", () => {
    expect(operationalEndpoints).toEqual(
      expect.arrayContaining([
        "/api/v1/requirements",
        "/api/v1/suppliers",
        "/api/v1/approvals",
        "/api/v1/appointments",
      ]),
    );
  });

  test.each([
    ["components/RequirementJsonSchemaForm.jsx", "@rjsf/core"],
    ["components/RequirementUpdateSurvey.jsx", "survey-core"],
    ["components/ItineraryMap.jsx", "maplibre-gl"],
  ])("%s uses %s", (file, marker) => {
    const source = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
    expect(source).toContain(marker);
  });
});
