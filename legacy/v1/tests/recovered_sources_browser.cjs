const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://localhost:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));

    await page.goto(`${base}/agent/review?date=2026-09-24`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.getByText("短线复盘看板", { exact: true }).waitFor({ timeout: 20_000 });
    await page.getByText("258 家", { exact: true }).waitFor({ timeout: 20_000 });
    await page.getByText("113 家", { exact: true }).waitFor({ timeout: 20_000 });
    console.log("breadth visible");
    const warnings = page.getByText(/涨跌分布取数失败/);
    for (let index = 0; index < await warnings.count(); index += 1) {
      assert.equal(await warnings.nth(index).isVisible(), false);
    }

    const stock = page.getByRole("button").filter({ hasText: "上工申贝" }).first();
    await stock.waitFor();
    await stock.click();
    await page.getByText(/Baostock 的证监会行业分类/).waitFor({ timeout: 40_000 });
    await page.getByText("361 只", { exact: true }).waitFor();
    console.log("industry visible");
    const sampleWarnings = page.getByText(/不代表完整行业成分/);
    for (let index = 0; index < await sampleWarnings.count(); index += 1) {
      assert.equal(await sampleWarnings.nth(index).isVisible(), false);
    }

    await page.goto(`${base}/agent/deepdive`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    const latest = page.getByRole("button", { name: "查看最近完成报告" });
    await latest.waitFor();
    console.log("deepdive ready");
    const responsePromise = page.waitForResponse((response) => response.url().includes("/api/deepdive/latest"));
    await latest.click();
    assert.equal((await responsePromise).status(), 200);
    await page.getByText(/已加载最近完成报告/).waitFor();
    console.log("latest feedback visible");

    assert.deepEqual(pageErrors, []);
    console.log("Recovered breadth, industry membership and latest deep-dive interactions passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
