// Live-service smoke test. It never mocks API responses or mutates server data.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.argv[2] || "http://127.0.0.1:8910";
const output = path.resolve(process.argv[3] || "frontend/dist/today-validation-live-qa");
fs.mkdirSync(output, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  const pageErrors = [], failed = [], serverErrors = [];
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("requestfailed", (request) => {
      if (request.url().startsWith(base)) failed.push(`${request.method()} ${request.url()} ${request.failure()?.errorText || ""}`);
    });
    page.on("response", (response) => { if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`); });
    await page.goto(base + "/today-validation", { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "今日验证", exact: true }).waitFor();
    await page.getByText("昨晚判断与今日验证", { exact: true }).waitFor();
    assert.equal(await page.getByText("交易日待确认", { exact: true }).count(), 0, "page must not remain unbound during live session");
    const cards = page.locator("button").filter({ has: page.locator("text=基准") });
    assert(await cards.count() > 0, "validation cards should be interactive");
    await cards.first().click();
    await page.getByText("状态历史", { exact: true }).waitFor();
    await page.getByLabel("关闭详情").click();
    const theme = page.getByText("统一题材抽屉", { exact: true });
    const themeSection = page.getByRole("heading", { name: "主线题材", exact: true });
    await themeSection.scrollIntoViewIfNeeded();
    const themeButton = themeSection.locator("xpath=../following-sibling::div//button").first();
    if (await themeButton.count()) {
      await themeButton.click();
      await theme.waitFor();
      await page.getByLabel("关闭题材详情").click();
    }
    assert.equal(await page.locator("body").evaluate((el) => el.scrollWidth <= window.innerWidth), true, "desktop body overflow");
    await page.screenshot({ path: path.join(output, "desktop.png"), fullPage: true });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.reload({ waitUntil: "networkidle" });
    assert.equal(await page.locator("body").evaluate((el) => el.scrollWidth <= window.innerWidth), true, "mobile body overflow");
    await page.screenshot({ path: path.join(output, "mobile.png"), fullPage: true });
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(failed, []);
    assert.deepEqual(serverErrors, []);
    console.log(JSON.stringify({ result: "passed", output }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
