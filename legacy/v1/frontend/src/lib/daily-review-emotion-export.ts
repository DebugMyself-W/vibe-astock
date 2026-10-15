import type { ShortTermEmotion } from "./api";
import type { DeepDiveState } from "../components/ui/DeepDive";
import type { BoardTrafficLight, StockTrafficLight } from "./agent";

export interface DailyEmotionExportOptions {
  /** Only export the chosen codes; omit for the complete card dataset. */
  selectedCodes?: Iterable<string>;
  title?: string;
  filenamePrefix?: string;
  dataLabel?: string;
  /** Live-card exports opt in; settled-review exports only accept settled stock lights. */
  useTrafficLights?: boolean;
}

const normalizeDate = (date: string) => date.replace(/-/g, "");

const dateLabel = (date: string) => {
  const day = normalizeDate(date);
  return /^\d{8}$/.test(day) ? `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}` : date;
};

const formatAmount = (value: number | null) => {
  if (value == null) return "—";
  return `${(value / 1e8).toLocaleString("zh-CN", { maximumFractionDigits: 2 })} 亿`;
};

const formatRate = (value: number | null) => value == null ? "—" : `${(value * 100).toFixed(1)}%`;

const cell = (value: string | number | null | undefined) => String(value ?? "—")
  .replace(/[\r\n]+/g, " ")
  .replace(/\|/g, "\\|");

const stockLightLabel = (light: StockTrafficLight | undefined) => {
  if (!light) return "待计算";
  const quality = light.quality;
  const score = quality?.max_score ? ` ${quality.score}/${quality.max_score}` : "";
  const action = light.action?.label ? `；${light.action.label}` : "";
  return `${quality?.label || light.label}${score}${action}`;
};

const signalState: Record<string, string> = {
  healthy: "通过", risk: "风险", neutral: "未通过", missing: "缺失",
  observed: "已观察", informational: "仅展示",
};

const stockLightDetails = (stock: { code: string; name: string }, light: StockTrafficLight | undefined) => {
  if (!light) return `### ${cell(stock.name)}（${cell(stock.code)}）\n\n- 状态：待计算`;
  const quality = light.quality;
  const signals = quality?.signals || light.signals || [];
  const risks = quality?.risks || light.blockers || [];
  const missing = quality?.missing || light.missing || [];
  const score = quality?.max_score ? `（${quality.score}/${quality.max_score}）` : "";
  const signalLines = signals.length
    ? signals.map((signal) => `  - ${cell(signal.label)}：${cell(signal.detail)}（${signalState[signal.state] || signal.state}）`).join("\n")
    : "  - —";
  return `### ${cell(stock.name)}（${cell(stock.code)}）\n\n` +
    `- 综合结论：${cell(light.label)}${light.summary ? `；${cell(light.summary)}` : ""}\n` +
    `- 质量结论：${cell(quality?.label || light.label)}${score}${quality?.summary ? `；${cell(quality.summary)}` : ""}\n` +
    `- 操作提示：${cell(light.action?.label || "—")}${light.action?.summary ? `；${cell(light.action.summary)}` : ""}\n` +
    "- 因子明细：\n" + signalLines + "\n" +
    `- 风险项：${risks.length ? risks.map(cell).join("；") : "无"}\n` +
    `- 缺失项：${missing.length ? missing.map(cell).join("；") : "无"}`;
};

export function buildDailyEmotionExport(
  emotion: ShortTermEmotion | null,
  state: Pick<DeepDiveState, "analysis" | "completed"> & { date?: string; names?: Record<string, string> },
  traffic?: BoardTrafficLight | null,
  options: DailyEmotionExportOptions = {},
) {
  if (!emotion || !Array.isArray(emotion.lianban_stocks)) return null;
  const day = normalizeDate(emotion.date);
  if (!/^\d{8}$/.test(day) || (state.date && state.date !== day)) return null;

  const label = dateLabel(emotion.date);
  const selected = options.selectedCodes ? new Set(options.selectedCodes) : null;
  const stocks = selected ? emotion.lianban_stocks.filter((stock) => selected.has(stock.code)) : emotion.lianban_stocks;
  const completed = stocks.filter((stock) =>
    state.completed[stock.code] && state.analysis[stock.code]?.trim(),
  );
  const title = options.title || "短线情绪复盘";
  const filenamePrefix = options.filenamePrefix || "短线情绪复盘";
  const dataLabel = options.dataLabel || "收盘";
  const sessionLabel = options.dataLabel || "收盘定稿";
  // Default remains conservative for the N-day review: only a settled light may
  // be exported. The upper live card explicitly opts into its own current light.
  const trafficStocks = options.useTrafficLights || traffic?.stock_mode === "settled" ? traffic?.stocks : undefined;
  const lightFor = (stock: { code: string }) => {
    const light = trafficStocks?.[stock.code];
    return light || emotion.lianban_stocks.find((row) => row.code === stock.code)?.traffic_light;
  };
  const rows = stocks.map((stock) =>
    `| ${cell(stock.name)} | ${cell(stock.code)} | ${stock.boards} 板 | ${cell(stockLightLabel(lightFor(stock)))} | ${cell(stock.price)} | ${formatAmount(stock.amount)} | ${formatAmount(stock.float_cap)} | ${cell(stock.reason)} | ${cell(stock.industry)} |`,
  );
  const lightDetails = stocks.map((stock) => stockLightDetails(stock, lightFor(stock))).join("\n\n");
  const analyses = completed.length
    ? completed.map((stock) => {
      const name = cell(state.names?.[stock.code] || stock.name);
      return `## ${name}（${stock.code}）\n\n${state.analysis[stock.code].trim()}`;
    }).join("\n\n---\n\n")
    : "暂无已完成深入分析。";

  return {
    filename: `${filenamePrefix}_${label}${selected ? "_已选" : ""}.md`,
    analysisCount: completed.length,
    count: stocks.length,
    content: `# ${title} · ${label}\n\n` +
      `交易日：${label}（${sessionLabel}）\n\n` +
      "## 情绪指标\n\n" +
      `- 涨停：${emotion.zt_count} 家\n` +
      `- 跌停：${emotion.dt_count ?? "—"} 家\n` +
      `- 炸板：${emotion.zb_count ?? "—"} 家\n` +
      `- 最高连板：${emotion.max_boards} 板\n` +
      `- 连板（2 板+）：${emotion.lianban_count} 家\n` +
      `- 封板率：${formatRate(emotion.seal_rate)}\n` +
      `- 炸板率：${formatRate(emotion.break_rate)}\n` +
      `- 晋级率：${formatRate(emotion.promotion_rate)}\n` +
      `- 前一交易日涨停家数：${emotion.yzt_count ?? "—"}\n\n` +
      `${options.title ? "## 标的名单" : "## 连板名单"}\n\n` +
      `| 名称 | 代码 | 连板 | 个股灯（${dataLabel}） | 当前价格 | 成交额 | 流通市值 | 涨停原因 | 概念 |\n` +
      "| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n" +
      `${rows.length ? rows.join("\n") : "| — | — | — | 待计算 | — | — | — | — | — |"}\n\n` +
      `${options.title ? "## 个股灯明细" : "## 连板个股灯明细"}（${dataLabel}）\n\n${lightDetails || "暂无标的。"}\n\n` +
      `${options.title ? "## 已完成深入分析" : "## 已完成连板深入分析"}（${completed.length} 只）\n\n${analyses}\n\n` +
      "AI 分析仅供研究参考，不构成投资建议。\n",
  };
}
