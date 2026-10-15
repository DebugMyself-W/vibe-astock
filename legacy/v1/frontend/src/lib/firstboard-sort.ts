import type { FirstBoardStock } from "./api";

export const SORT_LABELS = {
  seal_time: "首封", break_count: "炸板", price: "现价", pct: "当日涨幅", industry: "行业",
} as const;
export type SortKey = keyof typeof SORT_LABELS;
export interface SortRule { key: SortKey; direction: "asc" | "desc" }
export const DEFAULT_SORT: SortRule[] = [{ key: "seal_time", direction: "asc" }];

export function toggleSort(rules: SortRule[], key: SortKey): SortRule[] {
  const rule = rules.find((item) => item.key === key);
  if (!rule) return [...rules, { key, direction: "asc" }];
  if (rule.direction === "desc") return rules.filter((item) => item.key !== key);
  return rules.map((item) => item.key === key ? { ...item, direction: "desc" } : item);
}

function value(stock: FirstBoardStock, key: SortKey): number | string | null {
  const raw = stock[key];
  if (key === "seal_time") {
    if (typeof raw !== "string" || !/^\d{2}:\d{2}(?::\d{2})?$/.test(raw)) return null;
    const [hours, minutes, seconds = 0] = raw.split(":").map(Number);
    return hours < 24 && minutes < 60 && seconds < 60 ? hours * 3600 + minutes * 60 + seconds : null;
  }
  if (key === "industry") return typeof raw === "string" && raw.trim() && raw !== "—" ? raw.trim() : null;
  return typeof raw === "number" && Number.isFinite(raw) ? raw : null;
}

export function sortFirstBoard(stocks: FirstBoardStock[], rules: SortRule[]): FirstBoardStock[] {
  return [...stocks].sort((a, b) => {
    for (const { key, direction } of rules) {
      const left = value(a, key), right = value(b, key);
      // Missing data stays at the bottom in either direction.
      if (left == null || right == null) {
        if (left !== right) return left == null ? 1 : -1;
        continue;
      }
      const compared = typeof left === "number" && typeof right === "number"
        ? left - right : String(left).localeCompare(String(right), "zh-CN", { numeric: true });
      if (compared) return direction === "asc" ? compared : -compared;
    }
    return 0;
  });
}
