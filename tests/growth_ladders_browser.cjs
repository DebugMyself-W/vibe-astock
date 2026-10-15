const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:4173";
const outputDir = process.argv[3] || path.join(process.cwd(), "artifacts", "growth-ladders");
fs.mkdirSync(outputDir, { recursive: true });

const stock = (code, name, industry, level, pct_chg, options = {}) => ({
  code, name, industry, level, pct_chg, eliminated: false, stage_high: false, ...options,
});

const response = {
  available: true, date: "2026-10-09", live: false,
  updated_at: "2026-10-09T15:05:12+08:00",
  rule: "收盘连涨或连续收阳满 3 天进入天梯，按两者中较长的分层；开盘等于收盘且不跌也算收阳。收盘创近 120 日新高的单独标出；昨天不少于 5 天、今天断掉的标成断阳。",
  source: "local_full_market_daily", coverage: "full", note: "",
  available_dates: ["2026-09-30", "2026-10-08", "2026-10-09"],
  quality: { universe_count: 5362, covered_count: 5362, missing_count: 0, suspended_count: 41, source_hits: { baostock_qfq: 5218, eastmoney_qfq: 144 }, algorithm_version: "growth-ladder-v2" },
  sync: { state: "ready", phase: "complete", completed: 5362, total: 5362, percent: 100, available_dates: ["2026-09-30", "2026-10-08", "2026-10-09"] },
  levels: [
    { level: 12, overflow: 0, items: [stock("300239", "东宝生物", "生物制品", 12, 0.18)] },
    { level: 8, overflow: 0, items: [stock("600519", "贵州茅台", "白酒", 8, 1.8, { stage_high: true }), stock("601318", "中国平安", "保险", 8, 0.9)] },
    { level: 6, overflow: 3, items: [stock("688256", "寒武纪", "半导体", 6, 14.1, { stage_high: true }), stock("300476", "胜宏科技", "元件", 6, -2.3, { eliminated: true })] },
    { level: 5, overflow: 0, items: [stock("002463", "沪电股份", "元件", 5, 8.5), stock("601138", "工业富联", "计算机设备", 5, 6.3), stock("600183", "生益科技", "元件", 5, 3.7), stock("603986", "兆易创新", "半导体", 5, 2.9)] },
    { level: 4, overflow: 0, items: [stock("688012", "中微公司", "半导体", 4, 4.6), stock("002371", "北方华创", "半导体", 4, 3.2), stock("300274", "阳光电源", "电源设备", 4, 2.7), stock("000333", "美的集团", "白色家电", 4, 1.4), stock("601888", "中国中免", "旅游零售", 4, -1.2, { eliminated: true })] },
    { level: 3, overflow: 0, items: [stock("300750", "宁德时代", "电池", 3, 5.4), stock("002594", "比亚迪", "汽车整车", 3, 3.8), stock("600036", "招商银行", "银行", 3, 1.2), stock("601012", "隆基绿能", "光伏设备", 3, 0.7), stock("600276", "恒瑞医药", "化学制药", 3, -0.8, { eliminated: true })] },
  ],
};

const kline = Array.from({ length: 60 }, (_, index) => ({
  date: `2026-${index < 21 ? "07" : index < 42 ? "08" : "09"}-${String((index % 20) + 1).padStart(2, "0")}`,
  open: 100 + index * 0.7, high: 103 + index * 0.7, low: 98 + index * 0.7,
  close: 101 + index * 0.72 + Math.sin(index / 3) * 3, volume: 1000000 + index * 10000,
}));
const intraday = {
  requested_date: "2026-10-09", days: 1, source: "fixture",
  series: { "300239": { code: "300239", date: "2026-10-09", previous_close: 5.4, points: [
    { time: "09:35", label: "10/09 09:35", price: 5.45, volume: 1000, pct: .93 },
    { time: "10:30", label: "10/09 10:30", price: 5.6, volume: 1800, pct: 3.7 },
    { time: "14:55", label: "10/09 14:55", price: 5.72, volume: 2600, pct: 5.93 },
  ] } },
};

(async () => {
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE, "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe", "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"].find((candidate) => candidate && fs.existsSync(candidate));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const mock = async (page) => {
      await page.route("**/api/market/growth-ladders/yang**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) }));
      await page.route("**/api/kline**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(kline) }));
      await page.route("**/api/quote**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ "300239": { code: "300239", name: "东宝生物", price: 5.54, change_pct: 0.18, amount_wan: 24741, float_mcap_yi: 32.67, turnover_pct: 4.01 } }) }));
      await page.route("**/api/blocks**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
      await page.route("**/api/hot-concepts**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
      await page.route("**/api/market/intraday**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(intraday) }));
    };
    const desktop = await browser.newPage({ viewport: { width: 1536, height: 1180 }, deviceScaleFactor: 1 });
    await mock(desktop);
    await desktop.goto(`${baseUrl}/growth-ladders`, { waitUntil: "networkidle" });
    await desktop.getByRole("heading", { name: "连阳天梯" }).waitFor();
    await desktop.getByText("12天", { exact: true }).waitFor();
    await desktop.getByText(/覆盖率：5362\/5362/).waitFor();
    await desktop.getByText(/baostock_qfq 5218/).waitFor();
    if (await desktop.getByText("更多天梯", { exact: true }).count() < 2) throw new Error("复盘子导航未显示更多天梯");
    if (await desktop.getByRole("button", { name: /^查看.+行情$/ }).count() !== 19) throw new Error("股票卡片数量不正确");
    await desktop.screenshot({ path: path.join(outputDir, "vibe-growth-ladders-desktop.png"), fullPage: true });

    await desktop.getByRole("button", { name: "查看东宝生物行情" }).click();
    await desktop.getByRole("button", { name: "日K线" }).waitFor();
    await desktop.locator("canvas").last().waitFor();
    const quoteReadout = desktop.getByTestId("stock-chart-readout");
    const initialReadout = (await quoteReadout.textContent()).replace(/\s+/g, " ").trim();
    assert.match(initialReadout, /5\.54.*\+0\.18%/);
    const priceChart = desktop.getByTestId("stock-price-chart");
    const chartBox = await priceChart.boundingBox();
    assert.ok(chartBox, "stock chart should have a visible interaction area");
    await desktop.mouse.move(chartBox.x + chartBox.width * .72, chartBox.y + chartBox.height * .35);
    await desktop.waitForFunction((initial) => {
      const value = document.querySelector('[data-testid="stock-chart-readout"]')?.textContent?.replace(/\s+/g, " ").trim();
      return value && value !== initial;
    }, initialReadout);
    const hoveredReadout = (await quoteReadout.textContent()).replace(/\s+/g, " ").trim();
    assert.notEqual(hoveredReadout, initialReadout, "hovering a candle should update price and change");
    await desktop.mouse.move(chartBox.x - 20, chartBox.y - 20);
    await desktop.waitForFunction((initial) => document.querySelector('[data-testid="stock-chart-readout"]')?.textContent?.replace(/\s+/g, " ").trim() === initial, initialReadout);
    await desktop.getByRole("button", { name: "分时图" }).click();
    const minuteInitial = (await quoteReadout.textContent()).replace(/\s+/g, " ").trim();
    const minuteChartBox = await priceChart.boundingBox();
    await desktop.mouse.move(minuteChartBox.x + minuteChartBox.width * .7, minuteChartBox.y + minuteChartBox.height * .35);
    await desktop.waitForFunction((initial) => {
      const value = document.querySelector('[data-testid="stock-chart-readout"]')?.textContent?.replace(/\s+/g, " ").trim();
      return value && value !== initial && /10\/09/.test(value);
    }, minuteInitial);
    const minuteHovered = (await quoteReadout.textContent()).replace(/\s+/g, " ").trim();
    assert.ok([
      /5\.45.*\+0\.93%.*10\/09 09:35/,
      /5\.60.*\+3\.70%.*10\/09 10:30/,
      /5\.72.*\+5\.93%.*10\/09 14:55/,
    ].some((pattern) => pattern.test(minuteHovered)), `minute hover readout does not match a source point: ${minuteHovered}`);
    await desktop.screenshot({ path: path.join(outputDir, "vibe-growth-ladders-stock-detail.png"), fullPage: true });
    await desktop.getByRole("button", { name: "关闭行情卡片" }).click();
    await desktop.getByRole("button", { name: "元件 3" }).click();
    if (await desktop.getByRole("button", { name: /^查看.+行情$/ }).count() !== 3) throw new Error("行业筛选未生效");
    await desktop.getByLabel("显示断阳").uncheck();
    if (await desktop.getByRole("button", { name: /^查看.+行情$/ }).count() !== 2) throw new Error("断阳开关未生效");

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
    await mock(mobile);
    await mobile.goto(`${baseUrl}/growth-ladders`, { waitUntil: "networkidle" });
    await mobile.getByRole("heading", { name: "连阳天梯" }).waitFor();
    await mobile.screenshot({ path: path.join(outputDir, "vibe-growth-ladders-mobile.png"), fullPage: true });
    await mobile.getByRole("button", { name: /更多/ }).first().click();
    await mobile.getByText("分享", { exact: true }).waitFor();
    await mobile.screenshot({ path: path.join(outputDir, "vibe-growth-ladders-mobile-more.png"), fullPage: true });
    await mobile.getByRole("button", { name: /10\.09/ }).click();
    await mobile.getByRole("heading", { name: "选择交易日" }).waitFor();
    if (await mobile.getByText("09.30 周三").count() !== 1) throw new Error("交易日底部面板未显示完整日期");

    const failedKline = await browser.newPage({ viewport: { width: 900, height: 900 }, deviceScaleFactor: 1 });
    await failedKline.route("**/api/market/growth-ladders/yang**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) }));
    await failedKline.route("**/api/kline**", (route) => route.fulfill({ status: 502, contentType: "application/json", body: JSON.stringify({ detail: "K线源异常" }) }));
    await failedKline.route("**/api/quote**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ "300239": { code: "300239", name: "东宝生物", price: 5.54, change_pct: 0.18, amount_wan: 24741, float_mcap_yi: 32.67, turnover_pct: 4.01 } }) }));
    await failedKline.route("**/api/blocks**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "{}" }));
    await failedKline.route("**/api/hot-concepts**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: "[]" }));
    await failedKline.route("**/api/market/intraday**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ requested_date: "2026-10-09", series: {} }) }));
    await failedKline.goto(`${baseUrl}/growth-ladders`, { waitUntil: "networkidle" });
    await failedKline.getByRole("button", { name: "查看东宝生物行情" }).click();
    await failedKline.getByText("日 K 暂时不可用", { exact: true }).waitFor();
    await failedKline.getByRole("button", { name: "重新加载" }).waitFor();

    const buildingPage = await browser.newPage({ viewport: { width: 900, height: 900 }, deviceScaleFactor: 1 });
    let buildFinished = false;
    const buildingResponse = {
      available: false, date: "2026-10-09", live: false, updated_at: "2026-10-09T15:10:00+08:00",
      rule: response.rule, source: "local_full_market_daily", coverage: "building",
      note: "正在构建全市场日线，完整前不展示近似榜单。", available_dates: [], levels: [],
      quality: { universe_count: 5362, covered_count: 2681, missing_count: 0, suspended_count: 0, source_hits: { baostock_qfq: 2681 }, algorithm_version: "growth-ladder-v2" },
      sync: { state: "building", phase: "history", completed: 2681, total: 5362, percent: 50, available_dates: [] },
    };
    await buildingPage.route("**/api/market/growth-ladders/yang**", (route) => {
      const url = route.request().url();
      if (url.endsWith("/status")) {
        buildFinished = true;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ...response.sync, state: "ready" }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(buildFinished ? response : buildingResponse) });
    });
    await buildingPage.goto(`${baseUrl}/growth-ladders`, { waitUntil: "networkidle" });
    await buildingPage.getByText("正在构建完整全市场天梯").waitFor();
    await buildingPage.getByText("12天", { exact: true }).waitFor({ timeout: 7000 });
    console.log(JSON.stringify({ ok: true, cards: 19, screenshots: fs.readdirSync(outputDir).sort() }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exit(1); });
