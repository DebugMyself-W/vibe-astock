const { chromium } = require("playwright");

const base = process.argv[2] || "http://127.0.0.1:8910";
const routes = [
  "/", "/daily-review", "/agent/review", "/first-board", "/heat", "/today-validation",
  "/watch", "/yesterday-ladder", "/agent/intraday", "/agent/deepdive", "/stock-data",
  "/intel", "/backtest", "/backtest-agent", "/my-stocks", "/portfolio", "/watchlist",
  "/journal", "/notes", "/settings",
];

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    const pageErrors = [];
    const apiFailures = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    page.on("response", (response) => {
      const url = new URL(response.url());
      if (url.pathname.startsWith("/api/") && response.status() >= 500) {
        apiFailures.push({ path: url.pathname, status: response.status() });
      }
    });
    const results = [];
    for (const route of routes) {
      const beforeErrors = pageErrors.length;
      const beforeFailures = apiFailures.length;
      let error = null;
      try {
        await page.goto(base + route, { waitUntil: "domcontentloaded", timeout: 15000 });
        await page.getByText("Vibe-AStock", { exact: true }).first().waitFor({ timeout: 8000 });
        await page.waitForTimeout(1600);
      } catch (cause) {
        error = cause instanceof Error ? cause.message : String(cause);
      }
      results.push({
        route,
        error,
        pageErrors: pageErrors.slice(beforeErrors),
        apiFailures: apiFailures.slice(beforeFailures),
        text: (await page.locator("body").innerText()).replace(/\s+/g, " ").slice(0, 260),
      });
    }
    console.log(JSON.stringify(results, null, 2));
    if (results.some((result) => result.error || result.pageErrors.length)) process.exitCode = 1;
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });
