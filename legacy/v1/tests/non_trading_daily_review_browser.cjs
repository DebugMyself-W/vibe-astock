const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://localhost:8910";

const settledLight = {
  version: "board-traffic-light-v2",
  mode: "settled_promotion",
  code: "600001",
  name: "周末测试股",
  date: "2026-09-24",
  as_of: "15:00:00",
  stage: "sealed",
  light: "yellow",
  label: "环境黄 · 暂缓新仓",
  summary: "使用复盘日收盘定稿。",
  quality: {
    light: "green",
    label: "晋级候选",
    score: 4,
    max_score: 4,
    summary: "四项质量因子通过 4/4。",
    signals: [],
    risks: [],
    missing: [],
  },
  action: { light: "yellow", label: "环境黄 · 暂缓新仓", summary: "使用复盘日收盘定稿。" },
  environment: { light: "yellow", label: "黄灯", source: "settled", as_of: "15:00:00", missing: [] },
  signals: [],
  blockers: [],
  missing: [],
  uncovered: [],
};

function response(pathname) {
  if (pathname === "/api/market/session") return {
    now: "2026-09-26 22:30", today: "2026-09-26", quotes_of: "2026-09-24",
    phase_key: "closed", phase: "尚无今日行情", label: "尚无今日行情 · 显示 2026-09-24 数据", is_today: false, poll: false,
  };
  if (pathname === "/api/market/emotion") return {
    available: true, date: "2026-09-24", source_date: "2026-09-24", updated: "2026-09-26 22:30",
    zt_count: 52, dt_count: 13, zb_count: 10, max_boards: 2, lianban_count: 1,
    ladder: [{ boards: 2, count: 1, plus: false }], seal_rate: .839, break_rate: .161,
    promotion_rate: .255, yzt_count: 39,
    lianban_stocks: [{
      code: "600001", name: "周末测试股", boards: 2, price: 10, pct: 10,
      amount: 1e8, float_cap: 8e9, industry: "测试行业", reason: "测试题材",
      close_status: "收盘封板", break_count: 1, traffic_light: settledLight,
    }],
  };
  if (pathname === "/api/quote") return { "600001": { name: "周末测试股", price: 10, change_pct: 10 } };
  if (pathname === "/api/market/live-emotion") return { available: false, reason: "尚无今日行情", lianban_stocks: [] };
  if (pathname === "/api/market/overview") return { sentiment: { up: 1, down: 1, flat: 0, zt: 0, zt_real: 0, dt: 0, dt_real: 0, active: "—", breadth: "—", speculation: "—", date: "2026-09-24" }, sectors: [], updated: "—" };
  if (pathname === "/api/market/turnover-top") return { stocks: [] };
  if (pathname === "/api/indices" || pathname === "/api/global/indices") return [];
  if (pathname === "/api/market/overseas") return { available: false };
  return {};
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
    await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
    await page.route("**/api/**", (route) => {
      const url = new URL(route.request().url());
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response(url.pathname)) });
    });
    await page.goto(`${base}/daily-review`, { waitUntil: "domcontentloaded" });

    const row = page.getByRole("row").filter({ hasText: "周末测试股" });
    await row.waitFor();
    await row.getByLabel("周末测试股 晋级候选，查看个股因子状况").waitFor();
    await row.getByText("N 日收盘灯", { exact: true }).waitFor();
    await row.getByText("封板（炸 1 次）", { exact: true }).waitFor();
    assert.equal(await row.getByText("待计算", { exact: true }).count(), 0);
    assert.equal(await row.getByText("状态待确认", { exact: true }).count(), 0);
    console.log("Non-trading DailyReview uses the archived close light and close status.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
