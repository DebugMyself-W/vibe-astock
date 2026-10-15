const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.argv[2] || "http://localhost:8912";
const output = path.resolve("frontend/dist/emotion-regime-qa");
fs.mkdirSync(output, { recursive: true });

const signals = [
  { key: "seal", label: "封板质量", state: "risk", severe: false, detail: "炸板率 42% · 较日内最佳 +10%" },
  { key: "continuation", label: "盘中晋级", state: "risk", severe: false, detail: "晋级率 9% · 较日内峰值 -9%" },
  { key: "loss", label: "跌停压力", state: "healthy", severe: false, detail: "跌停 3 家 · 较日内低点 +1 家" },
  { key: "ladder", label: "盘中梯队", state: "healthy", severe: false, detail: "涨停 49 家 · 连板 10 家 · 最高 5 板" },
];
const regime = {
  version: "emotion-regime-v1", mode: "live", date: "2026-08-27", as_of: "10:01:20",
  phase: "退潮", phase_label: "退潮", temperature: .54, momentum: -.08,
  confidence: "high", data_coverage: 1, issues: [],
  summary: "2组独立信号同步转弱，进入退潮预警。",
  retreat: { level: "warning", label: "退潮预警", risk_count: 2, core_risk_count: 2, signals },
};

function response(pathname) {
  if (pathname === "/api/market/live-emotion") return {
    available: true, date: "2026-08-27", as_of: "10:01:20", phase: "盘中",
    zt_count: 49, dt_count: 3, zb_count: 36, max_boards: 5, lianban_count: 10,
    seal_rate: .58, break_rate: .42, promotion_rate: .09, promotion_base: 52,
    promotion_base_date: "2026-08-26", emotion_regime: regime,
  };
  if (pathname === "/api/market/session") return { phase: "盘中", label: "盘中", is_today: true };
  if (pathname === "/api/market/overview") return { sentiment: {
    up: 1600, down: 3000, flat: 200, zt: 49, zt_real: 49, dt: 3, dt_real: 3,
    active: "35%", breadth: "偏弱", speculation: "普通", date: "2026-08-27",
  }, sectors: [], updated: "10:01" };
  if (pathname === "/api/market/emotion") return {
    date: "2026-08-26", zt_count: 52, dt_count: 2, zb_count: 20, max_boards: 5,
    lianban_count: 3, ladder: [], lianban_stocks: [], seal_rate: .72,
    break_rate: .28, promotion_rate: .2, yzt_count: 40,
  };
  if (pathname === "/api/market/overseas") return { available: false, reason: "fixture" };
  if (pathname === "/api/indices" || pathname === "/api/global/indices") return [];
  if (pathname === "/api/market/turnover-top") return { stocks: [] };
  return {};
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on("pageerror", (error) => errors.push(error.message));
      await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
      await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
      await page.route("**/api/**", (route) => {
        const pathname = new URL(route.request().url()).pathname;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response(pathname)) });
      });
      await page.goto(`${base}/daily-review`, { waitUntil: "domcontentloaded" });
      await page.getByText("退潮预警", { exact: true }).waitFor();
      const cycle = page.getByLabel("情绪周期五阶段");
      await cycle.waitFor();
      const card = cycle.locator("xpath=ancestor::div[contains(@class,'glass')][1]");
      await card.screenshot({ path: path.join(output, `${name}.png`) });
      assert(await card.evaluate((el) => el.scrollWidth <= el.clientWidth + 1), `${name} live regime overflow`);
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("Live emotion regime desktop/mobile checks passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
