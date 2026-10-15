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
  if (pathname === "/api/market/limit-gene") return {
    available: true, window_days: 259, from: "2025-08-13", to: "2026-08-27",
    limit_up_count: 7, touched_limit_count: 10, seal_success_rate: .7,
    recent_60_limit_count: 2, stock_character_score: 63,
    source: "腾讯日K（未复权）", note: "涨停次数仅统计收盘封住涨停；盘中触板但收盘未封不计入。",
  };
  if (pathname === "/api/market/board-traffic-light") return params.get("stock_mode") === "settled"
    ? { ...traffic, light: "yellow", label: "黄灯 · 高位观察", stocks: { "600001": settledStockLight } }
    : traffic;
  if (pathname === "/api/market/live-emotion") return { available: true, date: "2026-08-27", as_of: "10:10:20", phase: "盘中",
    zt_count: 77, dt_count: 3, zb_count: 17, max_boards: 6, lianban_count: 13,
    seal_rate: .82, break_rate: .18, promotion_rate: .25, promotion_base: 52, emotion_regime: regime };
  if (pathname === "/api/market/session") return { now: "2026-08-27 10:10", today: "2026-08-27", quotes_of: "2026-08-27", phase: "盘中", label: "盘中", is_today: true };
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
    warnings: [], focus: null, emotion_metrics: { ladder_gap: { available: true, highest: 2,
    tiers: { "2": 1, "1": 3 }, gaps: [], continuous: true, note: "梯队完整" } }, market_facts: { feedback_matrix: { available: true, details: [
      { code: "600001", name: "测试股份", board: "10cm", prev_tier: "首板", ret: 10.01,
        result: "晋级涨停", sector: "测试行业", traffic_light: settledStockLight,
        next_session: { date: "2026-08-28", ret: 2.31, result: "收红", source: "next_review_feedback" } },
      { code: "600002", name: "旧档股票", board: "10cm", prev_tier: "首板", ret: -1.2,
        result: "小跌", sector: "测试行业" },
    ] }, theme_tree: { available: true, source_note: "题材诊断测试", tag_count: 1, covered: 1,
      total_limit_up: 1, coverage_rate: 1, concentration: 1,
      reason_meta: { source: "iwencai", fetched_at: "2026-08-27T15:30:00", cache_hit: true, settled: true },
      themes: [{ tag: "测试题材", kind: "theme", state: "扩散中", limit_up: 1, first_boards: 0,
        consec_boards: 1, highest: 2, broken: 0, limit_down: 0, broken_rate: 0,
        first_seal: "0945", last_seal: "0945", prev_members: 1, prev_still_up: 1,
        prev_broken_or_down: 0, continuation_rate: 1,
        members: [{ code: "600001", name: "测试股份", boards: 2, first_seal: "0945", broken_times: 0 }] }] } },
    iwencai_status: { configured: true, source_priority: ["iwencai"], intraday_refresh_minutes: 60,
      day: "2026-08-27", requests_today: 3, successes_today: 2, failures_today: 1,
      last_attempt_at: "2026-08-27T15:30:00", circuit_state: "rate_limited",
      blocked_until: "2026-08-27T15:40:00", circuit_reason: "测试熔断" },
    emotion_regime: { ...regime, mode: "settled" },
    board_traffic_light: { ...traffic, mode: "settled", stocks: undefined }, analysts: [] };
  if (pathname === "/api/review/dates") return { dates: ["2026-08-27"] };
  if (pathname === "/api/review/followup") return { available: false, eligible: false, eval_date: null, times: [], snapshot: null, final: null, prediction: null, notes: [], status: { phase: "等待", enabled: false, delayed: false } };
  if (pathname === "/api/review/ladder") return { available: true, date: "2026-08-27", count: 1, highest: 2,
    tiers: [{ boards: 2, count: 1, stocks: [{ code: "600001", name: "测试股份", boards: 2,
      pct: 10.01, industry: "测试行业", reason: "测试题材", seal_time: "09:45" }] }] };
  if (pathname === "/api/review-agent/status") return { installed: true, subscription_ready: true, engine: "test", default_model: "test" };
  if (pathname === "/api/review-agent/conversations") return [];
  if (pathname === "/api/review-agent/catalog") return { targets: [] };
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
      await factorDialog.getByText("7 次", { exact: true }).waitFor();
      await factorDialog.getByText("63 / 100", { exact: true }).waitFor();
      await factorDialog.getByText("70.0%", { exact: true }).waitFor();
      await factorDialog.getByRole("button", { name: "关闭" }).click();
      assert.equal(await page.getByText("打板环境", { exact: true }).count(), 0, `${name} duplicate sticky summary remains`);
      await page.getByText("市场宽度与涨跌停统计", { exact: true }).waitFor();
      assert.equal(await page.getByText("题材投机", { exact: true }).count(), 0, `${name} legacy speculation label remains`);
      await page.getByText("真实涨停", { exact: true }).waitFor();
      assert(await page.locator("body").evaluate((body) => body.scrollWidth <= body.clientWidth + 1), `${name} daily overflow`);
      await page.screenshot({ path: path.join(output, `${name}-daily.png`), fullPage: true });

      await page.goto(`${base}/agent/review`, { waitUntil: "domcontentloaded" });
      await page.getByLabel("收盘打板红绿灯").waitFor();
      await page.getByText("具体标的 · 1 只", { exact: true }).waitFor();
      await page.getByText("问财今日 3 次 / 失败 1", { exact: false }).waitFor();
      await page.getByText("熔断：测试熔断", { exact: false }).waitFor();
      const stockLink = page.locator('a[title="打开同花顺 600001"]:visible').first();
      await stockLink.waitFor();
      assert.equal(await stockLink.getAttribute("href"), "https://stockpage.10jqka.com.cn/600001", `${name} stock link target is incorrect`);
      await page.getByText("展开明细（2 只，按今日表现从差到好）", { exact: true }).click();
      await page.getByText("次日涨幅", { exact: true }).waitFor();
      await page.getByText("晋级结果", { exact: true }).waitFor();
      await page.getByText("+2.31%", { exact: true }).waitFor();
      await page.getByText("未记录", { exact: true }).waitFor();
      await page.getByTitle("按结果排序：跌停、跌超5%、小跌、收红、晋级涨停").first().click();
      await page.getByLabel("选择测试股份").check();
      assert.equal(await page.getByLabel("选中一键分析反馈股票").isEnabled(), true, `${name} batch deep dive remains disabled`);
      assert.equal(await page.getByLabel("导出已勾选反馈股票").isEnabled(), true, `${name} feedback export remains disabled`);
      await page.getByLabel("昨日强势股反馈跟踪备注").fill("回归测试备注");
      assert.equal(await page.evaluate(() => localStorage.getItem("vibe-astock-feedback-tracking-note:2026-08-27")), "回归测试备注", `${name} feedback note was not persisted`);
      await page.getByLabel("测试股份 晋级候选，查看个股因子状况").click();
      await page.getByRole("dialog", { name: "个股因子状况" }).getByText("四项质量因子通过 4/4。", { exact: false }).waitFor();
      await page.getByRole("dialog", { name: "个股因子状况" }).getByRole("button", { name: "关闭" }).click();
      assert(await page.locator("body").evaluate((body) => body.scrollWidth <= body.clientWidth + 1), `${name} review overflow`);
      await page.screenshot({ path: path.join(output, `${name}-review.png`), fullPage: true });

      await page.goto(`${base}/first-board`, { waitUntil: "domcontentloaded" });
      try {
        await page.getByLabel("测试股份 临板质量通过，查看个股因子状况").waitFor();
      } catch (error) {
        await page.screenshot({ path: path.join(output, `${name}-first-board-failure.png`), fullPage: true });
        console.error(`${name} first-board body:\n${(await page.locator("body").innerText()).slice(0, 4000)}`);
        throw error;
      }
      await page.getByText("可进入个股筛选", { exact: true }).first().waitFor();
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
