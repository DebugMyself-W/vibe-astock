const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://127.0.0.1:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.goto(`${base}/agent/review?date=2026-09-24`, { waitUntil: "domcontentloaded", timeout: 20_000 });

    await page.getByRole("button").filter({ hasText: "新华文轩" }).first().click();
    const drawer = page.locator(".ladder-drawer");
    await drawer.waitFor({ timeout: 20_000 });
    await drawer.getByRole("button", { name: "行情", exact: true }).first().click();
    await page.getByLabel("关闭行情卡片").waitFor();
    await page.getByRole("button", { name: "概念", exact: true }).click();

    await page.getByRole("heading", { name: "板块归属 · 概念" }).waitFor({ timeout: 20_000 });
    await page.getByText("当下热门概念命中", { exact: true }).waitFor({ timeout: 20_000 });
    assert((await page.getByText("电商概念", { exact: true }).count()) >= 2, "regular concept and hot hit should both be rendered");
    await page.getByText("国企改革", { exact: true }).waitFor();
    await page.getByRole("button", { name: "富时罗素", exact: true }).click();
    const conceptPeers = page.locator("section").filter({ hasText: "富时罗素 · 热门标的" });
    await conceptPeers.getByText("海峡创新", { exact: true }).waitFor({ timeout: 20_000 });
    assert.equal(await conceptPeers.getByText("概念热门标的加载失败", { exact: true }).count(), 0);
    assert.deepEqual(pageErrors, []);
    console.log("review stock modal concepts, hot hits, and concept peers regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
