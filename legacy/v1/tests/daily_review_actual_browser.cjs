const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://localhost:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const failures = [];
    page.on("response", (response) => {
      const pathname = new URL(response.url()).pathname;
      if (["/api/market/session", "/api/market/emotion", "/api/quote", "/api/market/board-traffic-light"].includes(pathname)
          && response.status() >= 400) failures.push(`${response.status()} ${pathname}`);
    });
    await page.goto(`${base}/daily-review`, { waitUntil: "domcontentloaded", timeout: 20_000 });
    const table = page.locator("table").filter({ has: page.getByRole("columnheader", { name: "N 日收盘个股灯" }) });
    await table.waitFor({ timeout: 20_000 });
    const text = await table.innerText();
    assert(text.includes("晋级候选"), `archived stock lights are not rendered:\n${text.slice(0, 1200)}`);
    assert(!text.includes("待计算"), `archived stock lights still contain 待计算:\n${text.slice(0, 1200)}`);
    assert(!text.includes("状态待确认"), `archived close states still contain 状态待确认:\n${text.slice(0, 1200)}`);
    assert(text.includes("封板"), "the settled table does not show confirmed close status");
    assert.deepEqual(failures, []);
    console.log("Actual DailyReview archived lights and close states passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
