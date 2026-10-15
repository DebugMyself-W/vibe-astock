import { ShieldCheck, ShieldAlert } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { finite, safeArray, type EntryDecision, type EntrySignal } from "@/lib/agent";
import { cn } from "@/lib/utils";

const labels = { bullish: "偏多", bearish: "偏空", neutral: "中性", missing: "缺失" };

function reading(value: number | null, unit: EntrySignal["unit"]): string {
  const n = finite(value);
  return n == null ? "—" : `${(unit === "rate" ? n * 100 : n).toFixed(2)}%`;
}

export function EntryDecisionPanel({ decision }: { decision?: EntryDecision }) {
  const signals = safeArray<EntrySignal>(decision?.signals);
  const vetoes = safeArray<string>(decision?.vetoes);
  const eligible = decision?.status === "candidate";
  const Icon = eligible ? ShieldCheck : ShieldAlert;
  return (
    <section aria-label="出手环境判定" className="min-w-0 space-y-3 border-y border-border py-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="flex items-center gap-2 text-sm font-bold"><Icon className="h-4 w-4" />出手环境判定</h2>
        <strong className={cn("text-base", eligible ? "text-primary" : "text-warning")}>
          {decision?.label || "判定不可用"}
        </strong>
        <Caliber mobileCentered text={"收盘复盘规则，不是盘中买点。所有核心读数可用且无降级警告才允许进入备选。\n" +
          "优先级：风险否决 > 数据校验 > 两项偏空 > 备选 > 观察 > 等待。\n" +
          "备选需至少4项偏多、无偏空，且赚钱效应、晋级率、炸板率三项偏多；至少2项偏多但未达备选为观察。\n" +
          "收益与亏损样本至少10只、连板至少3只、首板至少10只；有覆盖率时需至少90%。\n" +
          "这些是待回测的初始阈值。相关指标不是独立投票，也不构成胜率或仓位建议。"} />
        <span className="text-xs text-muted-foreground">{decision?.date || "日期未知"} 收盘依据 · 非实时买入信号</span>
      </div>
      <p className="text-sm text-muted-foreground">{decision?.reason || "未收到判定结果，不能据此授予备选资格。"}</p>
      {decision && <>
        <p className="text-xs text-muted-foreground">
          偏多 {decision.bullish_count} 项 · 偏空 {decision.bearish_count} 项 · 缺失 {safeArray<string>(decision.missing).length} 项
        </p>
        <div className="grid gap-x-6 md:grid-cols-2">
          {signals.map((signal) => (
            <div key={signal.key} className="min-w-0 border-t border-border py-2.5">
              <div className="flex flex-wrap items-baseline justify-between gap-2 text-[13px]">
                <span>{signal.label}</span>
                <span className="shrink-0 tabular-nums">
                  <b>{reading(signal.value, signal.unit)}</b>
                  <span className={cn("ml-2", signal.state === "bullish" ? "text-primary" :
                    signal.state === "bearish" ? "text-warning" : "text-muted-foreground")}>
                    {labels[signal.state]}{vetoes.includes(signal.key) ? " · 否决" : ""}
                  </span>
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-muted-foreground">
                偏多 {signal.lower_is_better ? "≤" : "≥"}{reading(signal.bull_threshold, signal.unit)}
                {" · "}偏空 {signal.lower_is_better ? "≥" : "≤"}{reading(signal.bear_threshold, signal.unit)}
                {signal.veto_threshold != null && <> · 否决 {signal.lower_is_better ? "≥" : "≤"}{reading(signal.veto_threshold, signal.unit)}</>}
                {signal.sample != null && <> · 样本 {signal.sample} 只</>}
              </p>
              {signal.reason && <p className="mt-1 text-xs text-warning">{signal.reason}</p>}
            </div>
          ))}
        </div>
        {safeArray<string>(decision.issues).map((issue) => <p key={issue} className="text-xs text-warning">{issue}</p>)}
        <div className="border-t border-border pt-2 text-xs leading-relaxed text-muted-foreground">
          <p>待确认：{safeArray<string>(decision.pending_checks).join("；")}。</p>
          <p className="mt-1">{decision.validation}</p>
        </div>
      </>}
    </section>
  );
}
