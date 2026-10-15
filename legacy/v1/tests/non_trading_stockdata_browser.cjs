const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const failures = [];
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/") && response.status() >= 400) failures.push({ path: url.pathname, status: response.status() });
    });
    await page.goto(`${base}/stock-data?symbol=600000`, { waitUntil: "domcontentloaded" });
    await page.getByText("个股研究", { exact: true }).last().waitFor();
    await page.waitForTimeout(25000);
    const text = (await page.locator("body").innerText()).replace(/\s+/g, " ");
    console.log(JSON.stringify({
      failures,
      loading: /查询中/.test(text),
      gaps: (text.match(/以下数据暂不可用[^。]*。/g) || []),
      hasQuote: /现价/.test(text),
      hasResearch: /研报|新闻|公告/.test(text),
      excerpt: text.slice(0, 1800),
    }, null, 2));
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
