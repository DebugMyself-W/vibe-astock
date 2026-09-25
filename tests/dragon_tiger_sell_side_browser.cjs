const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://127.0.0.1:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  let chatBody = null;
  try {
    const page = await browser.newPage({ viewport: { width: 1369, height: 1000 } });
    await page.addInitScript(() => localStorage.setItem("vr-llm", JSON.stringify({
      provider: "openai", baseURL: "https://example.invalid/v1", apiKey: "browser-test", model: "test",
    })));
    await page.route("**/api/dragon-tiger?**", (route) => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ data: {
        records: [{ date: "2026-09-18", reason: "测试上榜原因", net_buy: 1234.5, turnover: 18.2 }],
        seats: {
          buy: [{ name: "买方测试营业部", buy_amt: 2000, sell_amt: 100, net: 1900 }],
          sell: [{ name: "卖方测试营业部", buy_amt: 50, sell_amt: 1800, net: -1750 }],
        },
        institution: { buy_amt: 2000, sell_amt: 1800, net_amt: 200 },
      } }),
    }));
    await page.route("**/api/chat", async (route) => {
      chatBody = JSON.parse(route.request().postData() || "{}");
      await route.fulfill({
        status: 200,
        contentType: "application/x-ndjson",
        body: '{"type":"delta","text":"测试完成"}\n{"type":"done","rounds":1}\n',
      });
    });
    await page.goto(`${base}/agent/review`, { waitUntil: "networkidle" });
    const analyze = page.getByRole("button", { name: "分析", exact: true }).first();
    await analyze.waitFor();
    await analyze.click();
    await page.getByText("测试完成", { exact: true }).waitFor();
    assert(chatBody, "analysis must call /api/chat");
    assert.match(chatBody.context, /买方席位 TOP5[\s\S]*买方测试营业部/);
    assert.match(chatBody.context, /卖方席位 TOP5[\s\S]*卖方测试营业部/);
    assert.match(chatBody.messages[0].content, /卖方席位分析/);
    assert.match(chatBody.messages[0].content, /买卖双方对照/);
    console.log("dragon-tiger sell-side browser regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
