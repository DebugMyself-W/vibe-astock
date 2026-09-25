// Visual interaction smoke test with synthetic, clearly-labelled fixtures.
const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const base = process.argv[2] || "http://127.0.0.1:8912";
const output = path.resolve(process.argv[3] || "frontend/dist/today-validation-qa");
fs.mkdirSync(output, { recursive: true });

const fixture = {
  available: true, schema_version: 1, prediction_date: "2026-09-18", eval_date: "2026-09-19",
  observed_at: "2026-09-19T10:12:00+08:00", stage: "盘中", delayed: false,
  checkpoints: [
    { key: "auction_0925", time: "09:25", label: "竞价", state: "completed", observed_at: "2026-09-19T09:25:00+08:00", history_key: "20260919T0900" },
    { key: "morning_1000", time: "10:00", label: "上午", state: "completed", observed_at: "2026-09-19T10:00:00+08:00", history_key: "20260919T1000" },
    { key: "midday_1130", time: "11:30", label: "午间", state: "upcoming", observed_at: null, history_key: null },
    { key: "late_1430", time: "14:30", label: "尾盘", state: "upcoming", observed_at: null, history_key: null },
    { key: "close_1500", time: "15:00", label: "收盘", state: "upcoming", observed_at: null, history_key: null },
  ],
  summary: { total: 4, pending: 0, watching: 2, satisfied: 1, invalidated: 1, insufficient_data: 0, expired: 0 },
  validations: [
    { id: "1", metric: "limit_up_count", label: "涨停家数", judgment: "机器人板块延续强势，有望成为本周主线", expected_direction: "上升", baseline: 42, threshold: 5, unit: "家", current_value: 48, actual_direction: "上升", status: "satisfied", status_label: "暂时支持", evidence: "超过有意义变化阈值", updated_at: "2026-09-19T10:05:00+08:00", history: [{ status: "satisfied", at: "2026-09-19T10:05:00+08:00", snapshot: "morning_1000", value: 48, evidence: "超过阈值" }] },
    { id: "2", metric: "promotion_1to2", label: "1进2晋级率", judgment: "算力概念早盘分歧，关注10点后修复", expected_direction: "上升", baseline: .28, threshold: .05, unit: "", current_value: .25, actual_direction: "持平", status: "watching", status_label: "尚待观察", evidence: "变化尚未超过阈值", updated_at: "2026-09-19T10:12:00+08:00", history: [] },
    { id: "3", metric: "theme_concentration", label: "头部题材集中度", judgment: "创新药存在反弹预期", expected_direction: "上升", baseline: .31, threshold: .05, unit: "", current_value: null, actual_direction: null, status: "watching", status_label: "尚待收盘", evidence: "盘中暂不支持", updated_at: null, history: [] },
    { id: "4", metric: "deep_loss_count", label: "跌超5%家数", judgment: "消费板块短线反弹或有超跌修复", expected_direction: "下降", baseline: 18, threshold: 5, unit: "家", current_value: 27, actual_direction: "上升", status: "invalidated", status_label: "暂时相反", evidence: "亏钱效应扩大", updated_at: "2026-09-19T09:45:00+08:00", history: [] },
  ],
  regime: { phase: "修复", phase_label: "修复观察", confidence: "medium", coverage: .9, summary: "赚钱反馈回暖，但接力与亏钱效应仍需观察。", dimensions: { "赚钱反馈": 6.2, "接力晋级": 4.1, "封板质量": 5.8, "亏钱效应": 3.6, "梯队结构": 5.1, "市场宽度": 4.3 }, issues: [] },
  themes: [
    { id: "0", name: "机器人", lifecycle: "扩散", strength: 4.8, coverage: 1, members: [{ code: "600000", name: "测试核心", pct: 4.8, boards: 3 }], logic: "板块联动增强", risk: "核心股断板" },
    { id: "1", name: "算力", lifecycle: "震荡", strength: -.3, coverage: .9, members: [], logic: "早盘分歧", risk: "承接继续走弱" },
  ],
  feedback: { total: 20, buckets: [{ label: "晋级涨停", count: 4, ratio: .2 }, { label: "收红", count: 6, ratio: .3 }, { label: "小跌", count: 5, ratio: .25 }, { label: "跌超5%", count: 4, ratio: .2 }, { label: "跌停", count: 1, ratio: .05 }], members: [{ code: "600000", name: "测试核心", pct: 4.8, boards: 3 }] },
  events: [{ id: "e1", kind: "validation_changed", title: "炸板率升至31%", occurred_at: "2026-09-19T10:12:00+08:00", severity: "risk", before: .21, after: .31, threshold: .05, scope: ["promotion_1to2"], evidence: "较昨晚基准上升12个百分点" }],
  risks: ["高位股分歧加大", "市场成交量不足"], sources: {}, available_times: ["20260919T0900", "20260919T1000"],
};

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  const errors = [];
  try {
    for (const [name, width, height] of [["desktop", 1536, 1024], ["mobile", 390, 844]]) {
      const context = await browser.newContext({ viewport: { width, height } });
      const page = await context.newPage();
      page.on("pageerror", (e) => errors.push(e.message));
      await page.route("**/api/today-validation**", async (route) => {
        const url = new URL(route.request().url());
        const body = structuredClone(fixture);
        if (url.searchParams.has("at")) body.observed_at = "2026-09-19T10:00:00+08:00";
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
      });
      await page.goto(base + "/today-validation");
      await page.getByText("昨晚判断与今日验证").waitFor();
      assert.equal(await page.locator("body").evaluate((el) => el.scrollWidth <= window.innerWidth), true, `${name} body overflow`);
      await page.getByText("机器人板块延续强势", { exact: false }).click();
      await page.getByText("状态历史").waitFor();
      await page.getByLabel("关闭详情").click();
      await page.getByText("机器人", { exact: true }).click();
      await page.getByText("统一题材抽屉").waitFor();
      await page.getByLabel("关闭题材详情").click();
      await page.getByRole("button", { name: "回看 09:25 竞价" }).click();
      await page.getByText("正在回看历史截面", { exact: false }).waitFor();
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: true });
      await context.close();
    }
    assert.deepEqual(errors, []);
    console.log(JSON.stringify({ result: "passed", output }));
  } finally { await browser.close(); }
})().catch((error) => { console.error(error); process.exitCode = 1; });
