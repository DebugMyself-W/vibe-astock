const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

const baseUrl = process.argv[2] || "http://127.0.0.1:4173";
const outputDir = process.argv[3] || path.join(process.cwd(), "artifacts", "dragon-tiger-board");
fs.mkdirSync(outputDir, { recursive: true });

const branchNames = {
  buy: ["沪股通专用", "东方证券股份有限公司无锡梁清路证券营业部", "国泰海通证券股份有限公司总部", "金融街证券上海浦东新区成山路证券营业部", "国泰海通证券股份有限公司北京知春路证券营业部"],
  sell: ["沪股通专用", "中信证券股份有限公司上海分公司", "华鑫证券有限责任公司上海浦东新区瑞平路证券营业部", "国盛证券股份有限公司宁波桑田路证券营业部", "国信证券股份有限公司浙江互联网分公司"],
};
const branches = (_prefix, buy) => Array.from({ length: 5 }, (_, index) => {
  const name = branchNames[buy ? "buy" : "sell"][index];
  const profile = name.includes("沪股通") ? ["northbound", ["沪股通专用"], "交易所披露的沪股通专用席位。"]
    : name.includes("总部") || name.includes("上海分公司") || name.includes("浙江互联网") ? ["quant", ["量化基金"], "公开市场常见量化席位标签。"]
    : name.includes("桑田路") ? ["hot_money", ["游资", "宁波桑田路"], "公开市场常见游资席位别名。"]
    : name.includes("知春路") ? ["hot_money", ["游资", "北京知春路"], "公开市场常见游资席位别名。"]
    : ["brokerage", [], "普通营业部"];
  return {
  seat_id: `${buy ? "buy" : "sell"}-${index}`, category: profile[0], tags: profile[1], description: profile[2], name,
  buy_wan: buy ? 3600 - index * 320 : 380 + index * 50,
  sell_wan: buy ? 260 + index * 40 : 3100 - index * 270,
  net_wan: buy ? 3340 - index * 360 : -2720 + index * 320,
  ratio: 3.0 - index * 0.3,
}; });

const rows = [
  ["688256", "寒武纪", 14.12, 21860, "算力", "首日上涨无价格涨跌幅限制的证券", "AI芯片+算力", "2连板"],
  ["300476", "胜宏科技", 10.37, 15620, "PCB", "日涨幅达到15%的前5只证券", "PCB+服务器", null],
  ["002463", "沪电股份", 8.52, 9340, "服务器", "日涨幅偏离值达7%", "服务器+PCB", "首板"],
  ["601138", "工业富联", 6.31, 7280, "算力", "日涨幅偏离值达7%", "算力+液冷", null],
  ["600183", "生益科技", -7.21, -11260, "PCB", "日跌幅偏离值达7%", "PCB+覆铜板", null],
  ["002916", "深南电路", -5.18, -8060, "服务器", "日跌幅偏离值达7%", "服务器+先进封装", null],
  ["603986", "兆易创新", -3.86, -5920, "存储芯片", "日振幅值达15%", "存储芯片+半导体", null],
  ["688012", "中微公司", -2.74, -4180, "半导体", "有价格涨跌幅限制的日换手率达到30%", "半导体设备", null],
].map(([code, name, change_pct, net_wan, concept, reason, theme_reason, board_label], index) => ({
  code, name, close: 32.5 + index * 6.7, change_pct, turnover_pct: 8.3 + index * 1.7,
  deal_wan: 42800 - index * 2600,
  buy_wan: net_wan > 0 ? 23000 + index * 900 : 11200 - index * 300,
  sell_wan: net_wan > 0 ? 23000 + index * 900 - net_wan : 11200 - index * 300 - net_wan,
  net_wan, reasons: [reason], reason, concept, theme_reason, board_label,
  buy_branches: branches("世纪大道", true), sell_branches: branches("浦东南", false),
}));

const response = {
  data: { available: true, date: "2026-10-09", total_records: 11, total_stocks: rows.length,
    source: "eastmoney_datacenter", reason_note: null, branch_note: null, stocks: rows },
};

const announcements = [
  { date: "2026-10-08", type: "业绩预告", title: "前三季度业绩预告：净利润同比大幅增长", url: "https://example.test/notice/1" },
  { date: "2026-09-30", type: "公司公告", title: "关于核心产品产能建设进展的公告", url: "https://example.test/notice/2" },
];
const kline = Array.from({ length: 35 }, (_, index) => {
  const open = 25 + index * 0.2;
  return { date: `2026-09-${String(index + 1).padStart(2, "0")}`, open, close: open + 0.32, high: open + 0.55, low: open - 0.18, volume: 8000 + index * 300 };
});
const intraday = {
  requested_date: "2026-10-13", days: 1, source: "fixture",
  series: { "688256": { code: "688256", date: "2026-10-13", previous_close: 39, points: [
    { date: "2026-10-13", time: "09:30", label: "10/13 09:30", price: 39.2, pct: 0.51, volume: 1200 },
    { date: "2026-10-13", time: "10:30", label: "10/13 10:30", price: 40.1, pct: 2.82, volume: 800 },
    { date: "2026-10-13", time: "11:30", label: "10/13 11:30", price: 41.3, pct: 5.9, volume: 600 },
    { date: "2026-10-13", time: "14:00", label: "10/13 14:00", price: 41.9, pct: 7.44, volume: 900 },
    { date: "2026-10-13", time: "15:00", label: "10/13 15:00", price: 42.5, pct: 8.97, volume: 1100 },
  ] } },
};

async function mockStockDetails(page, intradayRequests = []) {
  await page.route("**/api/market/session", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
    now: "2026-10-13 15:35", today: "2026-10-13", quotes_of: "2026-10-13", latest_closed: "2026-10-13",
    is_today: true, phase_key: "closed", phase: "已收盘", label: "已收盘", poll: false,
  }) }));
  await page.route("**/api/quote?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { "688256": { name: "寒武纪", price: 42.5, last_close: 39, change_pct: 8.97, pe_ttm: 0, pb: 8.2, mcap_yi: 1400, float_mcap_yi: 1320, turnover_pct: 9.86, limit_up: 46.8, limit_down: 31.2, amount_wan: 42800 } } }) }));
  await page.route("**/api/kline?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: kline }) }));
  await page.route("**/api/market/intraday?**", (route) => {
    intradayRequests.push(route.request().url());
    return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: intraday }) });
  });
  await page.route("**/api/blocks?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: { total: 1, boards: [], concept_tags: ["算力"] } }) }));
  await page.route("**/api/hot-concepts?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: [] }) }));
  await page.route("**/api/announcements?**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ data: announcements }) }));
}

(async () => {
  const executablePath = [
    process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ].find((candidate) => candidate && fs.existsSync(candidate));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const desktop = await browser.newPage({ viewport: { width: 1536, height: 1240 }, deviceScaleFactor: 1 });
    await desktop.route("**/api/market/dragon-tiger-board**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) }));
    const intradayRequests = [];
    await mockStockDetails(desktop, intradayRequests);
    await desktop.goto(`${baseUrl}/dragon-tiger-board`, { waitUntil: "networkidle" });
    await desktop.getByText("主力在买什么").waitFor();
    if (await desktop.getByText("龙虎榜", { exact: true }).count() < 2) throw new Error("复盘子导航未显示龙虎榜");
    if (await desktop.locator("article").count() !== rows.length) throw new Error("桌面卡片数量不正确");
    await desktop.screenshot({ path: path.join(outputDir, "vibe-dragon-tiger-desktop.png"), fullPage: true });

    await desktop.locator("article").first().click();
    await desktop.getByText("营业部详情").first().waitFor();
    const thsLink = desktop.getByRole("link", { name: /同花顺个股讨论/ });
    if (await thsLink.getAttribute("href") !== `https://t.10jqka.com.cn/pc/newgroup/stock/${rows[0].code}/`) throw new Error("同花顺个股讨论链接不正确");
    if (await thsLink.getAttribute("target") !== "_blank") throw new Error("同花顺个股讨论应在新标签页打开");
    const detailTables = desktop.locator("dialog table");
    if (await detailTables.nth(0).locator("tbody tr").count() !== 5 || await detailTables.nth(1).locator("tbody tr").count() !== 5) throw new Error("买卖席位未各显示五席");
    await desktop.getByText("量化基金").first().waitFor();
    await desktop.getByText("游资").first().waitFor();
    await desktop.getByText("3.00%").first().waitFor();
    await desktop.screenshot({ path: path.join(outputDir, "vibe-dragon-tiger-detail.png"), fullPage: true });
    await desktop.getByRole("button", { name: "关闭" }).click();

    const firstCard = desktop.locator("article").first();
    await firstCard.getByRole("button", { name: "行情" }).click();
    await desktop.getByRole("button", { name: "分时图" }).waitFor();
    await desktop.getByTestId("stock-price-chart").waitFor();
    if (!intradayRequests.length || new URL(intradayRequests.at(-1)).searchParams.has("date")) throw new Error(`龙虎榜行情卡片没有请求最近分时：${intradayRequests.at(-1)}`);
    await desktop.getByText("行情日 2026-10-13 · 最新收盘", { exact: true }).waitFor();
    const chartBox = await desktop.getByTestId("stock-price-chart").boundingBox();
    if (!chartBox) throw new Error("分时图未渲染");
    await desktop.mouse.move(chartBox.x + chartBox.width * 0.03, chartBox.y + chartBox.height * 0.4);
    await desktop.getByTestId("stock-chart-readout").getByText("39.20", { exact: true }).waitFor();
    await desktop.getByTestId("stock-chart-readout").getByText("10/13 09:30", { exact: true }).waitFor();
    await desktop.mouse.move(chartBox.x + chartBox.width * 0.97, chartBox.y + chartBox.height * 0.4);
    await desktop.getByTestId("stock-chart-readout").getByText("42.50", { exact: true }).waitFor();
    await desktop.getByTestId("stock-chart-readout").getByText("10/13 15:00", { exact: true }).waitFor();
    await desktop.getByRole("button", { name: "日K线" }).click();
    await desktop.getByText("━ MA20", { exact: true }).waitFor();
    await desktop.screenshot({ path: path.join(outputDir, "vibe-dragon-tiger-market.png"), fullPage: true });
    await desktop.getByRole("button", { name: "关闭行情卡片" }).click();

    await firstCard.getByRole("button", { name: "异动" }).click();
    await desktop.getByText("龙虎榜资金", { exact: true }).waitFor();
    await desktop.getByText("前三季度业绩预告：净利润同比大幅增长", { exact: true }).waitFor();
    await desktop.screenshot({ path: path.join(outputDir, "vibe-dragon-tiger-reason.png"), fullPage: true });
    await desktop.getByRole("button", { name: "确定" }).click();

    await desktop.getByRole("button", { name: /净流出/ }).click();
    if (await desktop.locator("article").count() !== 4) throw new Error("净流出筛选未生效");
    await desktop.getByPlaceholder("股票名称或代码").fill("中微");
    if (await desktop.locator("article").count() !== 1) throw new Error("股票搜索未生效");

    const mobile = await browser.newPage({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 1 });
    await mobile.route("**/api/market/dragon-tiger-board**", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(response) }));
    await mobile.goto(`${baseUrl}/dragon-tiger-board`, { waitUntil: "networkidle" });
    await mobile.getByText("搜索筛选").waitFor();
    await mobile.screenshot({ path: path.join(outputDir, "vibe-dragon-tiger-mobile.png"), fullPage: true });
    console.log(JSON.stringify({ ok: true, desktopCards: rows.length, screenshots: fs.readdirSync(outputDir).sort() }));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
