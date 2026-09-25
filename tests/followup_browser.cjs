// UI integration checks with isolated, explicitly synthetic API fixtures.
// NODE_PATH=<playwright installation> node tests/followup_browser.cjs [base URL] [output dir]
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const base = process.argv[2] || "http://localhost:8911";
const output = path.resolve(process.argv[3] || "frontend/dist/followup-qa");
fs.mkdirSync(output, { recursive: true });

const member = { code: "600000", name: "界面测试成员", boards: 1, pct: 1.2, price: 10,
  quote_time: "2026-07-27T10:00:00+08:00", limit_up: false, limit_down: false };
const stats = { members: [member], sample: 1, valid: 1, coverage: 1, median: 1.2, up_count: 1, limit_up_count: 0, limit_down_count: 0 };
const direction = { id: "0", direction: "界面测试题材", logic: "固定测试数据，不代表实际行情。", risk: "测试风险原文。",
  tags: ["界面测试题材"], complete: true, binding_reason: "", members: [member], explicit_members: [], unresolved: [], stats };
const item = { metric: "money_effect_median", label: "昨日涨停股中位涨幅", unit: "%", direction: "上升", reason: "修复条件测试",
  base_value: -.8, eps: .5, current_value: 1.2, delta: 2, state: "暂时支持", sample: 1, valid: 1, verified: null };
const focus = { emotion_phase: "修复", market_oneliner: "界面交互测试样本，不是实际行情。", focus_directions: [direction],
  risk_alerts: ["测试数据"], verification_items: [item] };
const review = { target_date: "2026-07-24", trade_date: "2026-07-24", generated_at: "2026-07-24 16:00 CST", focus,
  emotion_metrics: {}, market_facts: {} };
const snapshot = { prediction_date: "2026-07-24", eval_date: "2026-07-27", observed_at: "2026-07-27T10:00:00+08:00",
  stage: "intraday", eligible: true, items: [item], directions: [direction], summary: { total: 1, decided: 0, hit: 0, undecidable: 1, rate: null } };
const final = { ...snapshot, stage: "final", observed_at: "2026-07-27T15:05:00+08:00",
  items: [{ ...item, state: "不成立", verified: false, current_value: -2, delta: -1.2 }],
  summary: { total: 1, decided: 1, hit: 0, undecidable: 0, rate: 0 } };
function fixture(editable) {
  return { available: true, prediction_date: "2026-07-24", eval_date: "2026-07-27", cutoff_at: "2026-07-27T09:15:00+08:00",
    editable, eligible: true, times: ["20260727T1000", "20260727T1400"], snapshot, final,
    prediction: { version: "test", recorded_at: "2026-07-24T16:00:00+08:00", items: [item], directions: [direction], focus,
      baseline_sample_date: "2026-07-23", universe: { themes: [{ tag: "界面测试题材", complete: true, members: [member] }] } },
    status: { phase: "盘中", enabled: true, delayed: false, last_success_at: snapshot.observed_at }, notes: [] };
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    const errors = [];
    for (const [name, width, height] of [["desktop", 1440, 1100], ["mobile", 390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      const data = fixture(name === "desktop");
      const calls = [];
      await page.route("**/api/**", async (route) => {
        const url = new URL(route.request().url());
        calls.push(url.pathname + url.search);
        let body = {};
        if (url.pathname === "/api/review/latest") body = { ...review, target_date: url.searchParams.get("date") || review.target_date };
        if (url.pathname === "/api/review/dates") body = { dates: ["2026-07-24", "2026-07-23"] };
        if (url.pathname === "/api/review/followup") {
          body = structuredClone(data);
          if (url.searchParams.get("date") === "2026-07-23") { body.snapshot = null; body.final = null; body.times = []; body.eligible = false; body.eval_date = "2026-07-24"; }
          if (url.searchParams.get("at") === "final") body.snapshot = data.final || data.snapshot;
          if (url.searchParams.get("at") === "20260727T1400") {
            body.snapshot = { ...snapshot, observed_at: "2026-07-27T14:00:00+08:00", items: [{ ...item, state: "暂时相反", current_value: -2, delta: -1.2 }] };
          }
        }
        if (url.pathname === "/api/review/followup/notes") {
          const value = route.request().postDataJSON();
          data.notes.push({ id: "1", text: value.text, created_at: snapshot.observed_at }); body = { ok: true };
        }
        if (url.pathname === "/api/review/followup/bindings") body = { ok: true };
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
      });
      await page.goto(base + "/agent/review");
      const panel = page.getByRole("region", { name: "次日跟踪" });
      await panel.getByText("暂时支持", { exact: true }).filter({ visible: true }).waitFor();
      await panel.scrollIntoViewIfNeeded();
      await panel.getByText("固定成员明细（1）", { exact: true }).click();
      const link = panel.getByRole("link", { name: "600000", exact: true });
      assert.equal(await link.getAttribute("href"), "https://stockpage.10jqka.com.cn/600000");
      assert.equal(await link.getAttribute("target"), "_blank");
      await page.screenshot({ path: path.join(output, name + "-latest.png") });
      await panel.screenshot({ path: path.join(output, name + "-panel.png") });
      const bounds = await panel.boundingBox();
      assert(bounds.width <= width && bounds.x + bounds.width <= width + 1, name + " panel overflow");
      await panel.getByRole("button", { name: "盘中回看", exact: true }).click();
      await panel.getByLabel("盘中记录时间").selectOption("20260727T1400");
      await panel.getByText("暂时相反", { exact: true }).filter({ visible: true }).waitFor();
      const requestCount = calls.length;
      await page.clock.install();
      await page.clock.fastForward(31_000);
      assert.equal(calls.length, requestCount, "history must not poll");
      await panel.getByRole("button", { name: "收盘结果", exact: true }).click();
      await panel.getByText("条件命中率 0%", { exact: true }).waitFor();
      data.final = null;
      data.snapshot = { ...snapshot, stage: "closing", items: [{ ...item, state: "待定稿" }] };
      await panel.getByRole("button", { name: "刷新跟踪记录" }).click();
      await panel.getByText("收盘数据待补齐，当前读数尚未定稿：", { exact: false }).waitFor();
      assert.equal(await panel.getByText("条件命中率", { exact: false }).count(), 0);
      await panel.screenshot({ path: path.join(output, name + "-closing.png") });
      data.final = final;
      await page.clock.fastForward(31_000);
      await panel.getByText("条件命中率 0%", { exact: true }).waitFor();
      const settledCalls = calls.length;
      await page.clock.fastForward(31_000);
      assert.equal(calls.length, settledCalls, "settled final must not poll");
      await panel.getByLabel("追加跟踪备注").fill("界面测试备注");
      await panel.getByRole("button", { name: "保存跟踪备注" }).click();
      await panel.getByText("界面测试备注", { exact: true }).waitFor();
      if (name === "desktop") {
        await panel.getByRole("button", { name: "确认 界面测试题材 题材关联" }).click();
        await panel.getByRole("checkbox").uncheck();
        await panel.getByRole("button", { name: "确认关联", exact: true }).click();
        assert(calls.includes("/api/review/followup/bindings?date=2026-07-24"));
      } else {
        assert(await panel.getByRole("button", { name: "确认 界面测试题材 题材关联" }).isDisabled());
      }
      await page.locator('input[type="date"]').fill("2026-07-23");
      await page.getByRole("region", { name: "次日跟踪" }).getByRole("button", { name: "盘中回看", exact: true }).click();
      await page.getByText("没有保存过盘中快照，未补造历史。", { exact: true }).waitFor();
      await context.close();
    }
    assert.deepEqual(errors, []);
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    await page.goto(base + "/agent/review");
    const panel = page.getByRole("region", { name: "次日跟踪" });
    await panel.waitFor({ timeout: 30_000 });
    await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: path.join(output, "real-service.png") });
    console.log(JSON.stringify({ result: "passed", fixtures: "desktop/mobile", actualService: base, output }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
