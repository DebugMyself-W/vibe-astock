const { chromium } = require("playwright");
const assert = require("node:assert/strict");

const base = process.argv[2] || "http://127.0.0.1:8910";

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  let saved = null;
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    await page.addInitScript(() => localStorage.setItem("astock-agent-connection", JSON.stringify({
      provider: "codex-private", model: "gpt-test", baseURL: "", apiKey: "", verifiedAt: Date.now(),
    })));
    await page.route("**/api/**", async (route) => {
      const url = new URL(route.request().url());
      let body = {};
      if (url.pathname === "/api/settings") {
        if (route.request().method() === "PUT") {
          saved = JSON.parse(route.request().postData() || "{}");
          body = { ...saved, review_schedule: { ...saved.review_schedule, last_date: null,
            last_triggered_at: null, last_status: null, last_finished_at: null, last_error: null, last_elapsed: null } };
        } else body = { iwencai_intraday_refresh_minutes: 60, review_schedule: {
          enabled: false, time: "15:30", force: false, source: null, last_date: "2026-09-24",
          last_triggered_at: "2026-09-24 18:30:00 CST", last_status: "failed",
          last_finished_at: "2026-09-24 18:35:00 CST", last_error: "测试失败", last_elapsed: 300,
        }, iwencai: {} };
      } else if (url.pathname === "/api/review-agent/status") body = {
        installed: true, subscription_ready: true, engine: "test", default_model: "gpt-test", models: ["gpt-test"],
      };
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    });

    await page.goto(`${base}/settings`, { waitUntil: "domcontentloaded" });
    await page.getByText("上次自动复盘失败", { exact: true }).waitFor();
    await page.getByText("测试失败", { exact: true }).waitFor();
    await page.getByLabel("启用定时复盘").check();
    await page.getByLabel("执行时间").fill("18:45");
    await page.getByLabel("已有复盘也重新生成").check();
    await page.getByRole("button", { name: "保存定时设置" }).click();
    await page.getByText("定时复盘已启用", { exact: true }).waitFor();

    assert(saved, "settings PUT was not sent");
    assert.deepEqual(saved.review_schedule.source, { provider: "codex-private", model: "gpt-test" });
    assert.equal(saved.review_schedule.enabled, true);
    assert.equal(saved.review_schedule.time, "18:45");
    assert.equal(saved.review_schedule.force, true);
    assert.equal("apiKey" in saved.review_schedule.source, false, "subscription credentials must not be persisted");
    console.log("scheduled review settings browser regression passed");
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
