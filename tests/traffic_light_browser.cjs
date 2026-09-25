const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.argv[2] || "http://localhost:8914";
const output = path.resolve("frontend/dist/traffic-light-qa");
fs.mkdirSync(output, { recursive: true });

const regime = {
  version: "emotion-regime-v1", mode: "live", date: "2026-08-27", as_of: "10:10:20",
  phase: "亢奋", phase_label: "亢奋", temperature: .763, momentum: .08,
  confidence: "high", data_coverage: 1, issues: [], summary: "高位观察。",
  retreat: { level: "watch", label: "高位观察", risk_count: 0, core_risk_count: 0, signals: [] },
};
const dimension = (key, label, light, summary) => ({
  key, label, light, light_label: light === "green" ? "绿灯" : "黄灯", summary, missing: [],
  signals: [{ key: `${key}-signal`, label: "关键读数", state: "healthy", detail: summary }],
});
const stockLight = {
  version: "board-traffic-light-v2", mode: "live_entry", code: "600001", name: "测试股份", date: "2026-08-27", as_of: "10:10:20",
  stage: "observe", light: "green", label: "可进入人工筛选", summary: "环境与个股质量均通过。",
  quality: { light: "green", label: "临板质量通过", score: 4, max_score: 4, summary: "临板量价因子通过 4/4。",
    signals: [
      { key: "near_limit", label: "临板距离", state: "healthy", detail: "距涨停不超过2%" },
      { key: "vol_ratio", label: "量比", state: "healthy", detail: "2.10" },
      { key: "turnover", label: "换手率", state: "healthy", detail: "8.0%" },
    ], risks: [], missing: [] },
  action: { light: "green", label: "可进入人工筛选", summary: "环境与个股质量均通过。" },
  environment: { light: "green", label: "绿灯 · 高位观察", source: "live", as_of: "10:10:20", missing: [] },
  signals: [
    { key: "environment", label: "环境总灯", state: "healthy", detail: "绿灯 · 高位观察" },
    { key: "near_limit", label: "临板距离", state: "healthy", detail: "距涨停不超过2%" },
    { key: "vol_ratio", label: "量比", state: "healthy", detail: "2.10" },
    { key: "turnover", label: "换手率", state: "healthy", detail: "8.0%" },
  ], blockers: [], missing: [], uncovered: ["Level-2买一封单"],
};
const settledStockLight = {
  ...stockLight, mode: "settled_promotion", stage: "sealed", light: "yellow",
  label: "个股质量通过 · 环境黄 · 暂缓新仓",
  quality: { ...stockLight.quality, label: "晋级候选", score: 4, max_score: 4,
    summary: "四项质量因子通过 4/4。" },
  action: { light: "yellow", label: "个股质量通过 · 环境黄 · 暂缓新仓", summary: "环境黄灯，暂缓新开仓。" },
  environment: { light: "yellow", label: "黄灯 · 高位观察", source: "live", as_of: "15:02:00", missing: [] },
};
const traffic = {
  version: "board-traffic-light-v1", mode: "live", date: "2026-08-27", as_of: "10:10:20",
  light: "green", label: "绿灯 · 高位观察", summary: "至少两个独立维度通过，且没有红灯。",
  dimensions: {
    market: dimension("market", "大盘环境", "green", "宽度与指数同步支持"),
    emotion: dimension("emotion", "情绪周期", "yellow", "亢奋 · 高位观察"),
    board: dimension("board", "打板生态", "green", "收盘基线与盘中生态通过"),
  }, hard_vetoes: [], missing: [], reasons: [],
  validation: "启发式阈值，尚未回测；绿灯仅表示规则准入。", uncovered: ["Level-2买一封单"],
  stocks: { "600001": stockLight },
};

function response(pathname, params = new URLSearchParams()) {
  if (pathname === "/api/market/board-traffic-light") return params.get("stock_mode") === "settled"
    ? { ...traffic, light: "yellow", label: "黄灯 · 高位观察", stocks: { "600001": settledStockLight } }
    : traffic;
  if (pathname === "/api/market/live-emotion") return { available: true, date: "2026-08-27", as_of: "10:10:20", phase: "盘中",
    zt_count: 77, dt_count: 3, zb_count: 17, max_boards: 6, lianban_count: 13,
    seal_rate: .82, break_rate: .18, promotion_rate: .25, promotion_base: 52, emotion_regime: regime };
  if (pathname === "/api/market/session") return { now: "2026-08-27 15:02", today: "2026-08-27", quotes_of: "2026-08-27", phase: "已收盘", label: "已收盘", is_today: true };
  if (pathname === "/api/market/overview") return { sentiment: { up: 3224, down: 1853, flat: 209,
    zt: 77, zt_real: 77, dt: 3, dt_real: 3, active: "61%", breadth: "偏强", speculation: "活跃", date: "2026-08-27" }, sectors: [], updated: "10:10" };
  if (pathname === "/api/market/emotion") return { date: "2026-08-26", zt_count: 52, dt_count: 2, zb_count: 20,
    max_boards: 5, lianban_count: 0, ladder: [], lianban_stocks: [], seal_rate: .72, break_rate: .28,
    promotion_rate: .2, yzt_count: 40 };
  if (pathname === "/api/quote") return { "600001": { name: "测试股份", price: 9.85, last_close: 9,
    change_pct: 9.44, pe_ttm: 10, pb: 2, mcap_yi: 80, turnover_pct: 8, limit_up: 10, limit_down: 8 } };
  if (pathname === "/api/market/first-board") return { date: "20260827", total_zt: 77, first_count: 1,
    reason_note: null, stocks: [{ code: "600001", name: "测试股份", price: 10, pct: 10, amount: 2e8,
      float_cap: 8e9, industry: "测试行业", seal_time: "09:45", last_seal_time: "09:45", break_count: 0,
      turnover_pct: 8, reason: "测试题材", traffic_light: settledStockLight }] };
  if (pathname === "/api/review/latest") return { target_date: "2026-08-27", generated_at: "2026-08-27 17:37 CST",
    warnings: [], focus: null, emotion_metrics: {}, market_facts: { feedback_matrix: { available: true, details: [
      { code: "600001", name: "测试股份", board: "10cm", prev_tier: "首板", ret: 10.01,
        result: "晋级涨停", sector: "测试行业", traffic_light: settledStockLight,
        next_session: { date: "2026-08-28", ret: 2.31, result: "收红", source: "next_review_feedback" } },
      { code: "600002", name: "旧档股票", board: "10cm", prev_tier: "首板", ret: -1.2,
        result: "小跌", sector: "测试行业" },
    ] } }, emotion_regime: { ...regime, mode: "settled" },
    board_traffic_light: { ...traffic, mode: "settled", stocks: undefined }, analysts: [] };
  if (pathname === "/api/review/dates") return { dates: ["2026-08-27"] };
  if (pathname === "/api/market/overseas") return { available: false };
  if (pathname === "/api/indices") return [
    { name: "上证指数", price: 3500, change_pct: .3, change_amt: 10 },
    { name: "深证成指", price: 11000, change_pct: .8, change_amt: 20 },
    { name: "创业板指", price: 2300, change_pct: 1.1, change_amt: 12 },
    { name: "沪深300", price: 4100, change_pct: -.1, change_amt: -4 },
  ];
  if (pathname === "/api/global/indices") return [];
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
      await page.addInitScript(() => localStorage.setItem("vr-watchlist", JSON.stringify(["600001"])));
      await page.route("https://fonts.googleapis.com/**", (route) => route.abort());
      await page.route("https://fonts.gstatic.com/**", (route) => route.abort());
      await page.route("**/api/**", (route) => { const url = new URL(route.request().url()); return route.fulfill({ status: 200, contentType: "application/json",
        body: JSON.stringify(response(url.pathname, url.searchParams)) }); });

      await page.goto(`${base}/daily-review`, { waitUntil: "domcontentloaded" });
      await page.getByLabel("盘中打板红绿灯").waitFor();
      await page.getByLabel("测试股份 临板质量通过，查看个股因子状况").click();
      const factorDialog = page.getByRole("dialog", { name: "个股因子状况" });
      await factorDialog.getByText("环境状态", { exact: true }).waitFor();
      await factorDialog.getByText("临板距离", { exact: true }).waitFor();
      await factorDialog.getByText("量比", { exact: true }).waitFor();
      await factorDialog.getByText("换手率", { exact: true }).waitFor();
      await factorDialog.getByRole("button", { name: "关闭" }).click();
      assert.equal(await page.getByText("打板环境", { exact: true }).count(), 0, `${name} duplicate sticky summary remains`);
      await page.getByText("市场宽度与涨跌停统计", { exact: true }).waitFor();
      assert.equal(await page.getByText("题材投机", { exact: true }).count(), 0, `${name} legacy speculation label remains`);
      await page.getByText("真实涨停", { exact: true }).waitFor();
      assert(await page.locator("body").evaluate((body) => body.scrollWidth <= body.clientWidth + 1), `${name} daily overflow`);
      await page.screenshot({ path: path.join(output, `${name}-daily.png`), fullPage: true });

      await page.goto(`${base}/agent/review`, { waitUntil: "domcontentloaded" });
      await page.getByLabel("收盘打板红绿灯").waitFor();
      await page.getByText("展开明细（2 只，按今日表现从差到好）", { exact: true }).click();
      await page.getByText("次日涨幅", { exact: true }).waitFor();
      await page.getByText("晋级结果", { exact: true }).waitFor();
      await page.getByText("+2.31%", { exact: true }).waitFor();
      await page.getByText("未记录", { exact: true }).waitFor();
      await page.getByLabel("测试股份 晋级候选，查看个股因子状况").click();
      await page.getByRole("dialog", { name: "个股因子状况" }).getByText("四项质量因子通过 4/4。", { exact: true }).waitFor();
      await page.getByRole("dialog", { name: "个股因子状况" }).getByRole("button", { name: "关闭" }).click();
      assert(await page.locator("body").evaluate((body) => body.scrollWidth <= body.clientWidth + 1), `${name} review overflow`);
      await page.screenshot({ path: path.join(output, `${name}-review.png`), fullPage: true });

      await page.goto(`${base}/first-board`, { waitUntil: "domcontentloaded" });
      await page.getByLabel("测试股份 晋级候选，查看个股因子状况").waitFor();
      await page.getByText("暂缓新仓", { exact: true }).first().waitFor();
      assert(await page.locator("body").evaluate((body) => body.scrollWidth <= body.clientWidth + 1), `${name} first-board overflow`);
      await page.screenshot({ path: path.join(output, `${name}-first-board.png`), fullPage: true });
      assert.deepEqual(errors, []);
      await page.close();
    }
    console.log("Traffic-light daily/review/stock-row desktop/mobile checks passed.");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
