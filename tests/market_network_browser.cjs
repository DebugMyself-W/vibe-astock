const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://127.0.0.1:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 1000 } });
    await page.goto(`${base}/settings`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "市场数据网络" }).waitFor();
    await page.getByText("AI 网络隔离已启用", { exact: false }).waitFor();
    const proxyText = await page.getByText("检测代理：", { exact: false }).textContent();
    assert(proxyText && proxyText.includes("http://"), `proxy detection missing: ${proxyText}`);

    await page.getByRole("button", { name: "一键诊断直连与 Clash" }).click();
    await page.getByText("诊断时间", { exact: false }).waitFor({ timeout: 30000 });
    await page.getByText("WSL 直连：", { exact: false }).waitFor();
    await page.getByText("Windows Clash / HTTP 代理：", { exact: false }).waitFor();
    for (const label of ["腾讯行情", "新浪行情", "东财延时行情"]) {
      assert.equal(await page.getByText(new RegExp(label)).count(), 2, `${label} should be tested on both routes`);
    }
    console.log("market network settings and diagnostics browser regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
