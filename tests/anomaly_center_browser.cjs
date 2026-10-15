const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:8910";
const outputDir = process.argv[3] || path.join(process.cwd(), "artifacts", "anomaly-center");
fs.mkdirSync(outputDir, { recursive: true });

const prediction = (code, name, market, current_pct, deviation, required_pct, status, rule, industry = "文化传媒") => ({
  code, name, market, board: market === "BJ" ? "北交所" : code.startsWith("68") ? "科创板" : code.startsWith("30") ? "创业板" : "主板",
  industry, price: 12.53, current_pct, window_days: rule.startsWith("30") ? 30 : 10,
  deviation, required_pct, status, rule, reset_date: null, quote_time: "2026-10-09 14:56:00", benchmark: market === "SH" ? "SH000002" : market === "BJ" ? "BJ899050" : "SZ399107",
});

const predictions = {
  available: true, coverage: "partial", trade_date: "2026-10-09", updated_at: "2026-10-09T15:10:21+08:00", algorithm_version: "anomaly-center-v1",
  note: "当前按已覆盖的 5288/5362 只股票展示，缺失标的不会解释为空结果。",
  quality: { universe_count: 5362, history_covered: 5288, announcement_covered: 87, realtime_covered: 126, missing_count: 74, source_hits: { baostock_qfq: 5144, eastmoney_qfq: 144 } },
  groups: {
    next_day: [prediction("688137", "近岸蛋白", "SH", 8.32, 83.24, 8.46, "次日预测", "10日累计上涨100%", "生物制品"), prediction("920002", "万达轴承", "BJ", 12.2, 88.4, 17.9, "次日预测", "10日累计上涨100%", "通用设备")],
    same_day: [prediction("688137", "近岸蛋白", "SH", 8.32, 83.24, 8.46, "盘中预测", "10日累计上涨100%", "生物制品"), prediction("300476", "胜宏科技", "SZ", 6.81, 92.02, 7.41, "盘中预测", "10日累计上涨100%", "元件")],
    triggered: [prediction("600825", "新华传媒", "SH", 10.01, 103.18, -3.18, "已触发", "10日累计上涨100%"), prediction("002050", "三花智控", "SZ", 9.98, 205.11, -5.11, "已触发", "30日累计上涨200%", "家电零部件")],
  },
};

const monitorRows = [
  { code: "600825", name: "新华传媒", market: "SH", type: "severe_abnormal", type_label: "严重异动", start: "2026-10-09", end: "", remaining_days: null, reason: "10个交易日累计上涨100%", announcement_date: "2026-10-09", link: "https://www.sse.com.cn/" },
  { code: "000420", name: "吉林化纤", market: "SZ", type: "risk_warning", type_label: "风险提示", start: "2026-10-08", end: "2026-10-22", remaining_days: 11, reason: "交易所重点监控，期间交易行为将持续核查", announcement_date: "2026-10-08", link: "https://www.szse.cn/" },
  { code: "920002", name: "万达轴承", market: "BJ", type: "risk_warning", type_label: "风险提示", start: "2026-10-06", end: "2026-10-20", remaining_days: 9, reason: "北交所重点监控", announcement_date: "2026-10-06", link: "https://www.bse.cn/" },
];

const kline = Array.from({ length: 60 }, (_, index) => ({ date: `2026-${index < 30 ? "08" : "09"}-${String((index % 28) + 1).padStart(2, "0")}`, open: 8 + index * .07, high: 8.3 + index * .07, low: 7.8 + index * .07, close: 8.1 + index * .075, volume: 1_000_000 + index * 8_000 }));

async function installMocks(page) {
  await page.route("**/api/market/anomaly-center/predictions**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(predictions) }));
  await page.route("**/api/market/anomaly-center/status**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "complete", phase: "完成", completed: 7, total: 7, progress: 100 }) }));
  await page.route("**/api/market/anomaly-center/sync**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ state: "queued", phase: "等待同步", completed: 0, total: 7, progress: 0 }) }));
  await page.route("**/api/market/anomaly-center/monitors**", route => {
    const type = new URL(route.request().url()).searchParams.get("type") || "all";
    const items = type === "all" ? monitorRows : monitorRows.filter(row => row.type === type);
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: true, coverage: "full", counts: { all: 3, risk_warning: 2, severe_abnormal: 1 }, items, pagination: { page: 1, page_size: 20, total: items.length, pages: 1 }, errors: [], updated_at: "2026-10-09T15:10:21+08:00" }) });
  });
  await page.route("**/api/market/anomaly-center/search**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify([{ code: "600825", name: "新华传媒", market: "SH", industry: "文化传媒", is_st: 0 }]) }));
  await page.route("**/api/market/anomaly-center/calculate**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ available: true, trade_date: "2026-10-09", stock: { code: "600825", name: "新华传媒", market: "SH", board: "沪市主板", industry: "文化传媒", price: 12.53, current_pct: 10.01 }, benchmark: { code: "SH000002", name: "上证A股指数" }, general_rule: { threshold: 20, same_direction_count: 4 }, severe_rules: [{ window_days: 10, threshold: 100, direction: "up", rule: "10日累计上涨100%", deviation: 103.18, required_today: -3.18, required_next: -2.9, triggered: true }], events: [], disclaimer: "本结果为本地规则测算，不代表交易所正式认定；请以交易所公告为准。", algorithm_version: "anomaly-center-v1" }) }));
  await page.route("**/api/kline**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(kline) }));
  await page.route("**/api/quote**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ "600825": { name: "新华传媒", price: 12.53, last_close: 11.39, change_pct: 10.01, amount_wan: 62600, float_mcap_yi: 130.92, turnover_pct: 4.78 } }) }));
  await page.route("**/api/blocks**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 2, boards: [], concept_tags: ["文化传媒", "国企改革"] }) }));
  await page.route("**/api/hot-concepts**", route => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
  await page.route("**/api/market/intraday**", route => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ requested_date: "2026-10-09", series: { "600825": { code: "600825", previous_close: 11.39, points: [{ time: "09:30", price: 11.6, volume: 10000 }, { time: "10:30", price: 12.1, volume: 18000 }, { time: "14:55", price: 12.53, volume: 25000 }] } } }) }));
}

(async () => {
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].find(candidate => candidate && fs.existsSync(candidate));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1536, height: 1100 }, deviceScaleFactor: 1 });
    await installMocks(page);
    await page.goto(`${baseUrl}/anomaly-center?tab=prediction`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "监管异动" }).waitFor();
    await page.getByRole("heading", { name: "次日预测" }).waitFor();
    await page.getByRole("button", { name: "刷新监管异动" }).click();
    await page.getByText(/已刷新 \d{2}:\d{2}:\d{2}/).waitFor();
    await page.screenshot({ path: path.join(outputDir, "01-predictions.png"), fullPage: true });

    await page.getByRole("button", { name: "监控池" }).click();
    await page.getByText("交易所重点监控，期间交易行为将持续核查").waitFor();
    await page.screenshot({ path: path.join(outputDir, "02-monitor-all.png"), fullPage: true });
    await page.getByRole("button", { name: /风险提示 2/ }).click();
    await page.getByText("北交所重点监控").waitFor();
    await page.screenshot({ path: path.join(outputDir, "03-monitor-risk-warning.png"), fullPage: true });
    await page.getByRole("button", { name: /严重异动 1/ }).click();
    await page.getByText("10个交易日累计上涨100%").waitFor();
    await page.screenshot({ path: path.join(outputDir, "04-monitor-severe-abnormal.png"), fullPage: true });

    await page.getByRole("button", { name: "单股计算" }).click();
    await page.getByRole("heading", { name: "A股异动监控规则" }).waitFor();
    await page.screenshot({ path: path.join(outputDir, "05-calculator-rules.png"), fullPage: true });
    await page.getByRole("button", { name: "我知道了" }).click();

    await page.getByRole("button", { name: "异动预测" }).click();
    await page.getByText("新华传媒", { exact: true }).click();
    await page.getByText("严重异动条件已触发", { exact: true }).waitFor();
    await page.getByRole("button", { name: "日K线" }).click();
    await page.locator("canvas").last().waitFor();
    await page.screenshot({ path: path.join(outputDir, "06-stock-detail.png"), fullPage: true });

    const files = fs.readdirSync(outputDir).filter(name => name.endsWith(".png")).sort();
    if (files.length !== 6) throw new Error(`expected 6 screenshots, got ${files.length}`);
    console.log(JSON.stringify({ ok: true, screenshots: files }));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
