const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const Module = require("node:module");
const ts = require("../frontend/node_modules/typescript");

const filename = path.resolve(__dirname, "../frontend/src/lib/daily-review-emotion-export.ts");
assert(fs.existsSync(filename), "daily emotion Markdown export builder must exist");
const mod = new Module(filename, module);
mod._compile(ts.transpileModule(fs.readFileSync(filename, "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS },
}).outputText, filename);
const { buildDailyEmotionExport } = mod.exports;

const emotion = {
  date: "2026-08-26", zt_count: 65, dt_count: 2, zb_count: 22, max_boards: 5, lianban_count: 2,
  seal_rate: 0.747, break_rate: 0.253, promotion_rate: 0.191, yzt_count: 56,
  lianban_stocks: [
    { code: "600001", name: "连板甲", boards: 5, price: 13.09, amount: 12.36e8, float_cap: 61.38e8, reason: "中药|集采中选", industry: "中药II" },
    { code: "600002", name: "连板乙", boards: 2, price: 10.78, amount: null, float_cap: null, reason: "", industry: "种植业" },
  ],
};
const state = {
  date: "20260826", names: { "600001": "连板甲（存档名）", "600009": "不在名单" },
  analysis: { "600001": "连板甲完整分析\n\n保留 Markdown", "600002": "中断内容", "600009": "不应导出" },
  completed: { "600001": true, "600002": false, "600009": true },
};

const traffic = {
  mode: "live", date: "2026-08-29", stock_mode: "settled", stocks: {
    "600001": {
      code: "600001", name: "连板甲", date: "2026-08-26", stage: "sealed", light: "green", label: "晋级候选", summary: "综合通过",
      signals: [], blockers: [], missing: [], uncovered: [],
      quality: { light: "green", label: "晋级候选", score: 4, max_score: 4, summary: "四项通过", signals: [{ key: "early_seal", label: "首次封板", state: "healthy", detail: "09:35" }], risks: [], missing: [] },
      action: { light: "green", label: "可筛选", summary: "" },
    },
  },
};

const report = buildDailyEmotionExport(emotion, state, traffic);
assert.equal(report.filename, "短线情绪复盘_2026-08-26.md");
assert.equal(report.analysisCount, 1);
assert(report.content.includes("封板率：74.7%"));
assert(report.content.includes("中药\\|集采中选"));
assert(report.content.includes("个股灯（收盘）"));
assert(report.content.includes("晋级候选 4/4；可筛选"));
assert(report.content.includes("## 连板个股灯明细（收盘）"));
assert(report.content.includes("首次封板：09:35（通过）"));
assert(report.content.includes("| 连板乙 | 600002 | 2 板 | 待计算 |"));
assert(report.content.includes("连板甲（存档名）（600001）"));
assert(!report.content.includes("中断内容"));
assert(!report.content.includes("不应导出"));
assert.equal(buildDailyEmotionExport(emotion, { ...state, date: "20260825" }), null, "stale analyses must not export");
const noAnalysis = buildDailyEmotionExport(emotion, { ...state, analysis: {}, completed: {} });
assert.equal(noAnalysis.analysisCount, 0);
assert(noAnalysis.content.includes("暂无已完成深入分析。"));
const liveTraffic = buildDailyEmotionExport(emotion, state, { ...traffic, stock_mode: "live" });
assert(liveTraffic.content.includes("| 连板甲 | 600001 | 5 板 | 待计算 |"), "live stock lights must not export into a settled review");
const selected = buildDailyEmotionExport(emotion, state, traffic, { selectedCodes: ["600002"] });
assert.equal(selected.filename, "短线情绪复盘_2026-08-26_已选.md");
assert.equal(selected.count, 1);
assert(selected.content.includes("| 连板乙 | 600002 | 2 板 | 待计算 |"));
assert(!selected.content.includes("| 连板甲 | 600001 |"));
const liveCard = buildDailyEmotionExport(emotion, state, { ...traffic, stock_mode: "live" }, {
  title: "今日实时二板情绪", filenamePrefix: "今日实时二板情绪", dataLabel: "盘中实时", useTrafficLights: true,
});
assert.equal(liveCard.filename, "今日实时二板情绪_2026-08-26.md");
assert(liveCard.content.includes("个股灯（盘中实时）"));
assert(liveCard.content.includes("| 连板甲 | 600001 | 5 板 | 晋级候选 4/4；可筛选 |"));
console.log("Daily emotion export unit checks passed");
