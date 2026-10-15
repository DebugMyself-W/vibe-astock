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
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      let body = {};
      let contentType = "application/json";
      if (url.pathname === "/api/dragon-tiger") body = { data: {
        records: [{ date: "2026-09-18", reason: "测试上榜原因", net_buy: 1234.5, turnover: 18.2 }],
        seats: {
          buy: [{ name: "买方测试营业部", buy_amt: 2000, sell_amt: 100, net: 1900 }],
          sell: [{ name: "卖方测试营业部", buy_amt: 50, sell_amt: 1800, net: -1750 }],
        },
        institution: { buy_amt: 2000, sell_amt: 1800, net_amt: 200 },
      } };
      else if (url.pathname === "/api/chat") {
        chatBody = JSON.parse(route.request().postData() || "{}");
        body = '{"type":"delta","text":"测试完成"}\n{"type":"done","rounds":1}\n';
        contentType = "application/x-ndjson";
      } else if (url.pathname === "/api/review/latest") body = {
        target_date: "2026-09-18", generated_at: "2026-09-18 18:30 CST", warnings: [], focus: null,
        emotion_metrics: {}, analysts: [], market_facts: { feedback_matrix: { available: true, details: [
          { code: "600001", name: "测试股份", prev_tier: "首板", ret: 10.01, result: "晋级涨停",
            sector: "测试行业", reason: "测试题材" },
        ] } },
      };
      else if (url.pathname === "/api/review/dates") body = { dates: ["2026-09-18"] };
      else if (url.pathname === "/api/review/followup") body = { available: false, eligible: false, notes: [], status: { phase: "等待", enabled: false } };
      else if (url.pathname === "/api/review-agent/status") body = { installed: true, subscription_ready: true, engine: "test", default_model: "test" };
      else if (url.pathname === "/api/review-agent/conversations") body = [];
      else if (url.pathname === "/api/review-agent/catalog") body = { targets: [] };
      await route.fulfill({ status: 200, contentType, body: typeof body === "string" ? body : JSON.stringify(body) });
    });
    await page.goto(`${base}/agent/review`, { waitUntil: "networkidle" });
    await page.getByText("展开明细（1 只，按今日表现从差到好）", { exact: true }).click();
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
