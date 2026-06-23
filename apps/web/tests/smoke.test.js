import { pages, demoCards } from "../lib/api.js";
import { readFileSync } from "node:fs";

const required = [
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

for (const route of required) {
  if (!pages.includes(route)) {
    throw new Error(`missing route ${route}`);
  }
}

if (!demoCards.includes("待审批")) {
  throw new Error("dashboard cards missing approval state");
}

const integrations = [
  ["components/RequirementJsonSchemaForm.jsx", "@rjsf/core"],
  ["components/RequirementUpdateSurvey.jsx", "survey-core"],
  ["components/ItineraryMap.jsx", "maplibre-gl"],
];

for (const [file, marker] of integrations) {
  const source = readFileSync(new URL(`../${file}`, import.meta.url), "utf8");
  if (!source.includes(marker)) {
    throw new Error(`${file} is missing ${marker}`);
  }
}

console.log("web smoke ok");
