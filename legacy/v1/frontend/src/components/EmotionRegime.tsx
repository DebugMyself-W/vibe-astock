import { Activity, AlertTriangle, CheckCircle2, CircleHelp } from "lucide-react";
import { cn } from "@/lib/utils";
import { finite, phaseTone, safeArray } from "@/lib/agent";
import type { EmotionPhase, EmotionRegime, EmotionRegimeSignal, RetreatLevel } from "@/lib/agent";

const PHASES: EmotionPhase[] = ["冰点", "修复", "发酵", "亢奋", "退潮"];

const levelTone: Record<RetreatLevel, string> = {
  normal: "border-success/40 bg-success/10 text-success",
  watch: "border-warning/40 bg-warning/10 text-warning",
  warning: "border-danger/50 bg-danger/10 text-danger",
  confirmed: "border-danger bg-danger/20 text-danger",
  insufficient: "border-border bg-muted/50 text-muted-foreground",
};

function confidenceText(value: EmotionRegime["confidence"]): string {
  return { high: "高", medium: "中", low: "低" }[value];
}

export function RegimeBadge({ regime, className }: { regime?: EmotionRegime | null; className?: string }) {
  if (!regime) {
    return <span className={cn("text-xs text-muted-foreground", className)}>情绪阶段待计算</span>;
  }
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      <span className={cn("rounded px-2 py-1 text-xs font-bold",
        regime.phase ? phaseTone(regime.phase_label) : "bg-muted text-muted-foreground")}>
        {regime.phase_label}
      </span>
      <span className={cn("rounded border px-2 py-1 text-xs font-semibold", levelTone[regime.retreat.level])}>
        {regime.retreat.label}
      </span>
      <span className="text-[11px] text-muted-foreground">
        {regime.mode === "settled" ? "收盘定稿" : regime.settled_pending ? "收盘定稿待生成" : "盘中预估"}
      </span>
    </div>
  );
}

function SignalIcon({ signal }: { signal: EmotionRegimeSignal }) {
  if (signal.state === "risk") return <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-danger" />;
  if (signal.state === "healthy") return <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-success" />;
  return <CircleHelp className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />;
}

export function EmotionRegimePanel({ regime, compact = false }: {
  regime?: EmotionRegime | null;
  compact?: boolean;
}) {
  if (!regime) return null;
  const signals = safeArray<EmotionRegimeSignal>(regime.retreat?.signals);
  const temperature = finite(regime.temperature);
  const momentum = finite(regime.momentum);
  return (
    <div className={cn(
      "border-y border-border/60 bg-muted/15 py-3",
      regime.retreat.level === "warning" && "border-danger/40 bg-danger/5",
      regime.retreat.level === "confirmed" && "border-danger/60 bg-danger/10",
    )}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Activity className="h-4 w-4 text-primary" />
            <RegimeBadge regime={regime} />
            <span className="text-[11px] text-muted-foreground">
              置信度 {confidenceText(regime.confidence)} · 数据覆盖 {Math.round(regime.data_coverage * 100)}%
            </span>
          </div>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{regime.summary}</p>
        </div>
        <div className="flex shrink-0 gap-4 text-right text-xs tabular-nums">
          <div><span className="text-muted-foreground">温度</span><b className="ml-1 text-foreground">{temperature == null ? "—" : `${Math.round(temperature * 100)}%`}</b></div>
          <div><span className="text-muted-foreground">动量</span><b className={cn("ml-1", momentum != null && momentum < 0 ? "text-danger" : "text-success")}>
            {momentum == null ? "—" : `${momentum > 0 ? "+" : ""}${Math.round(momentum * 100)}%`}
          </b></div>
        </div>
      </div>

      {!compact && (
        <>
          <div className="mt-3 grid grid-cols-5 gap-1" aria-label="情绪周期五阶段">
            {PHASES.map((phase, index) => {
              const active = regime.phase === phase;
              return (
                <div key={phase} className="min-w-0 text-center">
                  <div className={cn("h-1.5 rounded-sm", active ? "bg-primary" : "bg-muted")} />
                  <div className={cn("mt-1 text-[10px] sm:text-xs", active ? "font-bold text-primary" : "text-muted-foreground")}>{phase}</div>
                  {index < PHASES.length - 1 && <span className="sr-only">到</span>}
                </div>
              );
            })}
          </div>
          <div className="mt-3 grid gap-x-4 gap-y-2 border-t border-dashed border-border pt-3 sm:grid-cols-2 lg:grid-cols-3">
            {[...signals].sort((a, b) => (a.state === "risk" ? -1 : a.state === "missing" ? 1 : 0) - (b.state === "risk" ? -1 : b.state === "missing" ? 1 : 0)).map((signal) => (
              <div key={signal.key} className="flex min-w-0 items-start gap-1.5 text-xs">
                <SignalIcon signal={signal} />
                <span className="min-w-0">
                  <b className={cn(signal.state === "risk" ? "text-danger" : "text-foreground")}>{signal.label}</b>
                  <span className="ml-1 text-muted-foreground">{signal.detail}</span>
                </span>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
