const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://127.0.0.1:8910";

const previousReport = {
  job_id: "0".repeat(32),
  code: "600127",
  name: "旧报告标的",
  generated_at: "2026-10-08 20:24 CST",
  ai_source: { provider: "codex-private", model: "gpt-test" },
  verdict: {
    one_liner: "这是上一份已完成报告。",
    theme: "旧题材",
    capital: "旧资金",
    technical: "旧技术",
    risks: ["旧风险"],
    debate_takeaway: "旧结论",
  },
};

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    const pageErrors = [];
    page.on("pageerror", (error) => pageErrors.push(error.message));
    await page.addInitScript(() => {
      localStorage.setItem("astock-agent-connection", JSON.stringify({
        provider: "codex-private", model: "gpt-test", baseURL: "", apiKey: "",
      }));
      sessionStorage.removeItem("astock-deepdive-accepted");
      sessionStorage.removeItem("astock-deepdive-pending");
    });

    let submitted = false;
    const jobId = "1".repeat(32);
    await page.route("**/api/deepdive/latest", (route) => route.fulfill({
      status: 200, contentType: "application/json", body: JSON.stringify(previousReport),
    }));
    await page.route("**/api/review-agent/deepdive*", async (route) => {
      if (route.request().method() === "POST") {
        submitted = true;
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
          job_id: jobId, stock: "605577", running: true, status: "running", stage: "核对标的与行情", elapsed: 0,
        }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(submitted ? {
        job_id: jobId, stock: "605577", running: false, status: "failed", stage: "解析标的",
        elapsed: 3, error: "解析标的失败：公开资料取数未完整完成；请检查网络或稍后重试",
      } : { running: false, status: "idle", elapsed: 0 }) });
    });

    await page.goto(`${base}/agent/deepdive?symbol=605577`, { waitUntil: "domcontentloaded", timeout: 30_000 });
    await page.getByText("这是上一份已完成报告。", { exact: true }).waitFor();
    await page.getByRole("button", { name: "开始多空辩论" }).click();

    const alert = page.getByRole("alert");
    await alert.getByText("605577 多空辩论失败", { exact: true }).waitFor();
    await alert.getByText(/解析标的失败：公开资料取数未完整完成/).waitFor();
    await alert.getByText(/停止阶段：解析标的/).waitFor();
    await page.getByText(/下方仍是上一份已完成报告.*600127/).waitFor();
    assert.equal(await page.getByText("这是上一份已完成报告。", { exact: true }).isVisible(), true);
    assert.deepEqual(pageErrors, []);
    console.log("deep-dive terminal failure feedback regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
