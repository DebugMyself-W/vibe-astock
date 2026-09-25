import type { FirstBoardStock } from "./api";
import type { DeepDiveState } from "../components/ui/DeepDive";

export function buildFirstBoardExport(
  date: string,
  state: Pick<DeepDiveState, "analysis" | "completed"> & { date?: string; names?: Record<string, string> },
  stocks: FirstBoardStock[],
) {
  const day = date.split("-").join("");
  if (!/^\d{8}$/.test(day) || (state.date && state.date !== day)) return null;
  const codes = Object.keys(state.completed).filter((code) =>
    /^\d{6}$/.test(code) && state.completed[code] && state.analysis[code]?.trim(),
  ).sort();
  if (!codes.length) return null;
  const label = `${day.slice(0, 4)}-${day.slice(4, 6)}-${day.slice(6)}`;
  const names = { ...(state.names || {}), ...Object.fromEntries(stocks.map((s) => [s.code, s.name])) };
  const sections = codes.map((code) => {
    const name = names[code]?.replace(/[\r\n]/g, " ") || "名称未记录";
    return `## ${name}（${code}）\n\n${state.analysis[code].trim()}`;
  });
  return {
    filename: `首板分析_${label}.md`,
    count: codes.length,
    content: `# 首板分析 · ${label}\n\n交易日：${label}\n\n已完成分析：${codes.length} 只\n\n` +
      "AI 分析仅供研究参考，不构成投资建议。\n\n" + sections.join("\n\n---\n\n") + "\n",
  };
}
