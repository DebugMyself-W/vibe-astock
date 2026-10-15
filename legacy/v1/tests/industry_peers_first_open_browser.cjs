const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://localhost:8910";

(async () => {
  const browser = await chromium.launch({
    headless: true,
    channel: process.env.PLAYWRIGHT_CHANNEL || undefined,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.route("**/api/market/industry-peers?**", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 4_000));
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          available: true,
          degraded: false,
          board: { code: "BK0001", name: "出版", basis: "industry" },
          requested_date: "2026-09-24",
          quote_date: "2026-09-24",
          focus_code: "601811",
          total: 2,
          members: [
            { code: "601811", name: "新华文轩", pct: 1.2, industry: "出版", is_focus: true },
            { code: "600551", name: "时代出版", pct: 0.8, industry: "出版", is_focus: false },
          ],
          source: "test_delayed_source",
        }),
      });
    });

    await page.goto(`${base}/agent/review?date=2026-09-24`, {
      waitUntil: "domcontentloaded",
      timeout: 20_000,
    });
    const stock = page.getByRole("button").filter({ hasText: "新华文轩" }).first();
    await stock.waitFor({ timeout: 15_000 });

    const started = Date.now();
    await stock.click();
    const drawer = page.locator(".ladder-drawer");
    await drawer.getByText(/主票 新华文轩/).waitFor({ timeout: 1_000 });
    await drawer.getByText("正在后台补全同行业标的…", { exact: true }).waitFor({ timeout: 1_000 });
    assert(Date.now() - started < 1_500, "first-open preview should render before the slow request finishes");

    await drawer.getByText("时代出版", { exact: true }).waitFor({ timeout: 6_000 });
    await drawer.getByText("正在后台补全同行业标的…", { exact: true }).waitFor({ state: "hidden" });
    assert.deepEqual(pageErrors, []);
    console.log("industry peers first-open preview regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
