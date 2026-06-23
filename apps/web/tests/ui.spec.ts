import { expect, test } from "@playwright/test";

test("requirement creation renders agent input and RJSF fields", async ({
  page,
}) => {
  await page.goto("/requirements/new");
  await expect(
    page.getByRole("heading", { name: "新建拜访需求" }),
  ).toBeVisible();
  await expect(page.getByRole("textbox").first()).toContainText("下周去苏州看");
  await expect(page.locator("form.rjsf")).toBeVisible();
  await expect(page.locator("#root_duration_minutes")).toHaveValue("90");
  await expect(page.getByRole("button", { name: "保存草稿" })).toBeVisible();
  await expect(page.locator("#root_supplier_id option").first()).toHaveText(
    "苏州安科",
  );
  await page.locator("#root_required_people_0").fill("赵采购");
  await expect(page.locator(".agent-summary")).toContainText("赵采购");
  await page.getByRole("button", { name: "解析需求" }).click();
  await expect(page.getByRole("status")).toHaveText("解析结果已同步");
});

test("dashboard and supplier directory render live API records", async ({
  page,
}) => {
  await page.goto("/dashboard");
  await expect(page.getByText("苏州安科").first()).toBeVisible();
  await page.goto("/suppliers");
  await expect(page.getByRole("heading", { name: "苏州安科" })).toBeVisible();
  await page.getByLabel("搜索供应商").fill("SUP-008");
  await expect(page.getByRole("heading", { name: "太仓恒曜" })).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "苏州安科" }),
  ).not.toBeVisible();
});

test("requirement detail starts supplier contact and opens approval queue", async ({
  page,
  request,
}) => {
  const suppliers = await (
    await request.get("http://127.0.0.1:8000/api/v1/suppliers")
  ).json();
  const sites = await (
    await request.get(
      `http://127.0.0.1:8000/api/v1/suppliers/${suppliers[0].id}/sites`,
    )
  ).json();
  const created = await request.post(
    "http://127.0.0.1:8000/api/v1/requirements",
    {
      headers: { "Idempotency-Key": `playwright-${Date.now()}` },
      data: {
        supplier_name: suppliers[0].display_name,
        supplier_id: suppliers[0].id,
        site_id: sites[0].id,
        purpose_category: "质量沟通",
        date_start: "2026-06-25T01:00:00Z",
        date_end: "2026-06-26T10:00:00Z",
        duration_minutes: 90,
        priority: 4,
        required_people: ["王经理"],
        origin: "上海虹桥酒店",
        destination: "上海虹桥机场",
        return_deadline: "2026-06-26T10:00:00Z",
        can_move_existing: false,
      },
    },
  );
  const requirement = await created.json();

  await page.goto(`/requirements/${requirement.id}`);
  await page.getByRole("button", { name: "联系供应商" }).click();
  await page.waitForURL("**/approvals");
  await expect(page.getByText("发送首次供应商联络").first()).toBeVisible();
});

test("supplier CSV reconciliation previews exported stable IDs", async ({
  page,
  request,
}) => {
  const exported = await request.get(
    "http://127.0.0.1:8000/api/v1/reconciliation/suppliers/export",
  );
  await page.goto("/suppliers");
  await page.locator('input[type="file"]').setInputFiles({
    name: "suppliers.csv",
    mimeType: "text/csv",
    buffer: Buffer.from(await exported.text()),
  });
  await page.getByRole("button", { name: "生成预览" }).click();
  await expect(page.getByRole("status")).toHaveText("预览完成");
  await expect(page.getByText("8 行")).toBeVisible();
});

test("requirement update renders SurveyJS wizard", async ({ page }) => {
  await page.goto("/requirements/demo/update");
  await expect(page.getByRole("heading", { name: "快速更新" })).toBeVisible();
  await expect(page.locator(".sd-root-modern")).toBeVisible();
  await expect(page.getByText("改期")).toBeVisible();
  await expect(page.getByText("可接受新时间")).toBeVisible();
});

test("itinerary renders MapLibre container and route list", async ({
  page,
}) => {
  await page.goto("/itineraries/demo");
  await expect(page.getByRole("heading", { name: "行程方案" })).toBeVisible();
  await expect(page.getByText("上海虹桥酒店")).toBeVisible();
  await expect(page.locator(".maplibre-canvas")).toBeVisible();
  await expect(page.locator("canvas").first()).toBeVisible({ timeout: 10_000 });
});

test("supplier submits a public availability window", async ({ page }) => {
  await page.route(
    "**/api/v1/public/availability/demo-token",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          requirement_id: "demo-requirement",
          supplier_name: "苏州安科",
          purpose_category: "质量沟通",
          candidate_windows: [
            {
              start: "2026-06-25T02:00:00Z",
              end: "2026-06-25T04:00:00Z",
              timezone_name: "Asia/Shanghai",
              preference: 3,
            },
          ],
          expires_at: "2026-06-28T00:00:00Z",
        }),
      });
    },
  );
  await page.route(
    "**/api/v1/public/availability/demo-token/submit",
    async (route) => {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ ok: true, data: { windows: [{}] } }),
      });
    },
  );

  await page.goto("/public/availability/demo-token");
  await expect(page.getByText("苏州安科")).toBeVisible();
  await page.getByRole("checkbox").first().check();
  await page.getByLabel("联系人姓名").fill("张经理");
  await page.getByRole("button", { name: "提交时间" }).click();
  await expect(page.getByText("已提交，协调人将据此更新行程。")).toBeVisible();
});
