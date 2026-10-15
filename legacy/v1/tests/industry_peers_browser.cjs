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

    await page.goto(`${base}/agent/review?date=2026-09-24`, {
      waitUntil: "domcontentloaded",
      timeout: 20_000,
    });
    await page.getByText("Vibe-AStock", { exact: true }).first().waitFor();

    const stock = page.getByRole("button").filter({ hasText: "新华文轩" }).first();
    await stock.waitFor({ timeout: 15_000 });
    const [response] = await Promise.all([
      page.waitForResponse((item) => item.url().includes("/api/market/industry-peers")),
      stock.click(),
    ]);
    assert.equal(response.status(), 200);
    const payload = await response.json();
    assert.equal(payload.available, true);
    assert(payload.total > 1);
    assert(payload.members.some((item) => item.code === "601811" && item.is_focus));

    if (payload.source === "baostock_csrc_industry") {
      assert.equal(payload.degraded, false);
      assert.equal(payload.requested_date, "2026-09-24");
      await page.getByText(/Baostock 的证监会行业分类/).waitFor();
      await page.getByText(/这是完整分类成分/).waitFor();
    } else {
      assert.equal(payload.source, "eastmoney_board_constituents");
      assert.equal(payload.board?.basis, "industry");
      await page.getByText("时代出版", { exact: true }).waitFor();
    }
    assert.deepEqual(pageErrors, []);
    console.log("historical industry full-classification browser regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
