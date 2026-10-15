const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:4173";
const outputDir = process.argv[3] || path.join(process.cwd(), "artifacts", "board-pattern-research");
fs.mkdirSync(outputDir, { recursive: true });

const dates = Array.from({ length: 250 }, (_, i) => `2026-${String(Math.floor(i / 28) + 1).padStart(2, "0")}-${String(i % 28 + 1).padStart(2, "0")}`);
const bars = Array.from({ length: 32 }, (_, i) => ({
  date: `2026-${i < 16 ? "09" : "10"}-${String(i % 16 + 1).padStart(2, "0")}`,
  open: 8 + i * .08, high: 8.35 + i * .09, low: 7.85 + i * .075,
  close: 8.1 + i * .085 + Math.sin(i / 2) * .1, volume: 800000 + i * 40000,
}));

const event = (code, name, status = "sealed") => ({
  event_date: status === "temporary_break" ? "2026-10-13" : "2026-10-09", code, name,
  market: "SH", industry: "服装家纺", theme: "消费", status, board_height: 2,
  board_type: status === "temporary_break" ? "T字" : "换手", path: ["换手", status === "temporary_break" ? "T字" : "换手"],
  open: 10, high: 11, low: 9.8, close: 10.8, prev_close: 9.8, pct_chg: status === "temporary_break" ? 4.99 : 10.02,
  volume: 18000000, amount: 626000000, turnover_pct: 17.22, float_mcap: 5800000000,
});

const closeEvent = event("600400", "红豆股份");
const liveEvent = event("605577", "龙版传媒", "temporary_break");
const directory = (mode) => ({
  available: true, coverage: "full", matching_available: true,
  date: mode === "intraday" ? "2026-10-13" : "2026-10-09", mode,
  anchor_time: mode === "intraday" ? "2026-10-13T10:09:00+08:00" : "2026-10-09T15:10:00+08:00",
  algorithm_version: "board-pattern-v1", events: mode === "intraday" ? [liveEvent] : [closeEvent],
  groups: [{ key: mode === "intraday" ? "temporary_break" : "sealed", label: mode === "intraday" ? "临时断板" : "连板进行中", count: 1, items: mode === "intraday" ? [liveEvent] : [closeEvent] }],
  available_dates: dates, quality: { event_count: 1, source_hits: { eastmoney_limit_pool: 76 }, history_sessions: 250, required_sessions: 250 },
  intraday_window: { available: true, date: "2026-10-13", opens_at: "09:25", closes_at: "15:00", quote_day: "2026-10-13", reason: "" },
  sync: { state: "ready", phase: "complete", completed: 280, total: 280, progress: 100, available_dates: dates },
});

const cases = ["中央商场", "宏昌电子", "华康股份", "先导基电", "晋拓股份", "湖南天雁", "同力天启", "华锋股份"].map((name, index) => ({
  code: `60${String(280 + index).padStart(4, "0")}`, name, event_date: `2026-0${index + 1}-15`, industry: "消费", theme: "消费电子",
  status: "sealed", board_height: 2, board_type: "换手", path: ["换手", "换手"],
  scores: { identity: 95, path: 100, volume_price: 78 + index, liquidity: 62, environment: 70, total: 80 + index / 2, path_suffix: 2, exact_path: true },
  result: { t1: index % 2 ? -4.99 : 10.01, t3: index % 3 ? 8.91 : -5.88, t1_high: 10.1, t1_pct: 10.01, label: index % 2 ? "弱势延续" : "强修复/晋级" },
  chart: bars,
}));

function result(mode, algorithm) {
  const target = mode === "intraday" ? liveEvent : closeEvent;
  return {
    available: true, coverage: "full", matching_available: true, date: target.event_date, mode,
    anchor_time: mode === "intraday" ? "2026-10-13T10:09:00+08:00" : "2026-10-09T15:10:00+08:00",
    algorithm, algorithm_version: "board-pattern-v1", target: { ...target, chart: bars },
    stats: { sample_count: 20, complete_path_count: 31, next_red_rate: mode === "intraday" ? 40 : 55, next_avg_high: mode === "intraday" ? 3.03 : 7.05, next_close_median: mode === "intraday" ? -1.71 : 1.61, next_close_mean: mode === "intraday" ? -1.42 : 1.58, next_limit_up: 2, next_limit_down: 3 },
    cases, candidate_count: 591,
    scripts: (mode === "intraday" ? ["强修复", "独立反抽", "继续退潮"] : ["继续晋级", "分歧承接", "晋级失败"]).map(title => ({ title, detail: `${title}的客观验证条件，下一交易日按价格、封板和题材扩散确认。` })),
    warnings: mode === "intraday" ? ["盘中预演按当前价格假设收盘，当日高低点和封板状态仍可能继续变化。", "当日成交量与换手率尚未完成，相关量价因子已自动降权 50%。"] : [],
    quality: directory(mode).quality, disclaimer: "历史相似度与历史结果不代表未来成功率，不构成买卖建议。",
  };
}

(async () => {
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].find(value => value && fs.existsSync(value));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1536, height: 1100 }, deviceScaleFactor: 1 });
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(30000);
    await page.route("**/api/market/board-pattern-research/events**", route => {
      const mode = new URL(route.request().url()).searchParams.get("mode") || "close";
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(directory(mode)) });
    });
    await page.route("**/api/market/board-pattern-research/calculate", async route => {
      const body = route.request().postDataJSON();
      await new Promise(resolve => setTimeout(resolve, 250));
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(result(body.mode, body.algorithm)) });
    });
    await page.route("**/api/market/board-pattern-research/status", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(directory("close").sync) }));
    await page.goto(`${baseUrl}/board-pattern-research`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "连板剧本研究" }).waitFor();
    await page.getByText("红豆股份", { exact: true }).first().waitFor();
    await page.screenshot({ path: path.join(outputDir, "01-close-event-pool.png"), fullPage: true });
    await page.getByRole("button", { name: /开始计算/ }).click();
    await page.getByRole("status").getByText(/正在计算 红豆股份/).waitFor();
    await page.getByText("历史对照", { exact: false }).waitFor();
    await page.getByRole("status").getByText(/计算完成：红豆股份/).waitFor();
    await page.locator("canvas").first().waitFor();
    await page.screenshot({ path: path.join(outputDir, "02-close-v1-result.png"), fullPage: true });
    await page.getByRole("button", { name: /V2 · 全景匹配/ }).click();
    await page.getByRole("button", { name: /开始计算/ }).click();
    await page.getByText("V2 · 全景匹配 · 历史对照").waitFor();
    await page.screenshot({ path: path.join(outputDir, "03-close-v2-result.png"), fullPage: true });
    await page.getByRole("button", { name: /盘中预演/ }).click();
    await page.getByText("龙版传媒", { exact: true }).first().waitFor();
    await page.getByRole("button", { name: /开始计算/ }).click();
    await page.getByText(/量价因子已自动降权 50%/).waitFor();
    await page.screenshot({ path: path.join(outputDir, "04-intraday-preview.png"), fullPage: true });
    console.log(JSON.stringify({ ok: true, screenshots: fs.readdirSync(outputDir).filter(name => name.endsWith(".png")).sort() }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
