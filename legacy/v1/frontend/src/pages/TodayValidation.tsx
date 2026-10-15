import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle, Check, ChevronRight, Clock3, Eye, Loader2, RefreshCw,
  RotateCcw, ShieldAlert, Target, X,
} from "lucide-react";
import { api, type TodayTheme, type TodayValidationData, type TodayValidationItem, type ValidationState } from "@/lib/api";
import { cn } from "@/lib/utils";
import { GlassCard } from "@/components/ui/GlassCard";
import { AskAiButton } from "@/components/ui/AskAiButton";
import { pctColor } from "@/lib/colors";

const statusMeta: Record<ValidationState, { label: string; color: string; icon: typeof Check }> = {
  pending: { label: "待验证", color: "text-muted-foreground bg-muted/50", icon: Clock3 },
  watching: { label: "待观察", color: "text-warning bg-warning/10", icon: Eye },
  satisfied: { label: "已满足", color: "text-success bg-success/10", icon: Check },
  invalidated: { label: "已证伪", color: "text-danger bg-danger/10", icon: X },
  insufficient_data: { label: "数据不足", color: "text-muted-foreground bg-muted/50", icon: AlertTriangle },
  expired: { label: "已失效", color: "text-muted-foreground bg-muted/50", icon: Clock3 },
};

const pctTone = pctColor;
const fmt = (n: number | null | undefined, unit = "") => n == null ? "—" : `${Number.isInteger(n) ? n : n.toFixed(1)}${unit}`;
const time = (value?: string | null) => value ? value.slice(11, 16) : "—";

function SectionTitle({ children, extra }: { children: React.ReactNode; extra?: React.ReactNode }) {
  return <div className="mb-3 flex items-center justify-between"><h2 className="text-base font-bold">{children}</h2>{extra}</div>;
}

function MetricBar({ label, value, max = 10, detail }: { label: string; value: number | null; max?: number; detail?: string }) {
  const normalized = value == null ? 0 : Math.min(max, Math.max(0, value));
  return <div title={detail} className="grid grid-cols-[5.25rem_1fr_2rem] items-center gap-2 text-xs">
    <span className="text-muted-foreground">{label}</span>
    <div className="h-2 rounded-full bg-muted"><div className="h-full rounded-full bg-gradient-to-r from-primary/65 to-primary" style={{ width: `${normalized / max * 100}%` }} /></div>
    <span className="text-right font-medium">{value == null ? "—" : value.toFixed(1)}</span>
  </div>;
}

function Radar({ values }: { values: [string, number | null][] }) {
  const rows = values.length ? values.slice(0, 6) : [["赚钱反馈", null], ["接力晋级", null], ["封板质量", null], ["亏钱效应", null], ["梯队结构", null], ["市场宽度", null]] as [string, number | null][];
  const count = rows.length;
  const point = (i: number, scale: number) => { const a = Math.PI * 2 * i / count - Math.PI / 2; return `${100 + Math.cos(a) * 76 * scale},${100 + Math.sin(a) * 76 * scale}`; };
  const scaleOf = (value: number | null) => value == null ? .18 : Math.max(.08, Math.min(1, value / 10));
  const polygon = rows.map(([, v], i) => point(i, scaleOf(v))).join(" ");
  return <div className="relative mx-auto h-48 w-56">
    <svg viewBox="0 0 200 200" className="h-full w-full overflow-visible">
      {[.25, .5, .75, 1].map((s) => <polygon key={s} points={rows.map((_, i) => point(i, s)).join(" ")} fill="none" stroke="currentColor" className="text-border" />)}
      {rows.map((_, i) => <line key={i} x1="100" y1="100" x2={point(i, 1).split(",")[0]} y2={point(i, 1).split(",")[1]} stroke="currentColor" className="text-border" />)}
      <polygon points={polygon} fill="hsl(var(--primary) / .18)" stroke="hsl(var(--primary))" strokeWidth="2" />
      {rows.map(([, v], i) => { const [x, y] = point(i, scaleOf(v)).split(","); return <circle key={i} cx={x} cy={y} r="3" fill="hsl(var(--primary))" />; })}
    </svg>
    {rows.map(([label], i) => { const a = Math.PI * 2 * i / count - Math.PI / 2; return <span key={label} className="absolute -translate-x-1/2 -translate-y-1/2 whitespace-nowrap text-[10px] text-muted-foreground" style={{ left: `${50 + Math.cos(a) * 48}%`, top: `${50 + Math.sin(a) * 48}%` }}>{label}</span>; })}
  </div>;
}

function ValidationDrawer({ item, onClose }: { item: TodayValidationItem; onClose: () => void }) {
  const meta = statusMeta[item.status];
  return <div className="fixed inset-0 z-50 flex justify-end">
    <button aria-label="关闭详情" className="absolute inset-0 bg-black/45" onClick={onClose} />
    <aside className="ladder-drawer glass relative m-3 w-[min(430px,calc(100vw-24px))] overflow-auto p-5">
      <div className="flex items-start justify-between gap-4"><div><span className={cn("rounded-full px-2.5 py-1 text-xs font-semibold", meta.color)}>{meta.label}</span><h2 className="mt-3 text-xl font-bold">{item.label}</h2><p className="mt-1 text-sm text-muted-foreground">{item.judgment}</p></div><button onClick={onClose}><X className="h-5 w-5" /></button></div>
      <div className="mt-5 grid grid-cols-2 gap-3 text-sm">
        <div className="rounded-xl bg-muted/40 p-3"><p className="text-xs text-muted-foreground">昨晚基准</p><p className="mt-1 font-bold">{fmt(item.baseline, item.unit)}</p></div>
        <div className="rounded-xl bg-muted/40 p-3"><p className="text-xs text-muted-foreground">当前读数</p><p className="mt-1 font-bold">{fmt(item.current_value, item.unit)}</p></div>
        <div className="rounded-xl bg-muted/40 p-3"><p className="text-xs text-muted-foreground">预期方向</p><p className="mt-1 font-bold">{item.expected_direction || "—"}</p></div>
        <div className="rounded-xl bg-muted/40 p-3"><p className="text-xs text-muted-foreground">有意义阈值</p><p className="mt-1 font-bold">{fmt(item.threshold, item.unit)}</p></div>
      </div>
      <h3 className="mb-3 mt-6 font-semibold">状态历史</h3>
      <div className="space-y-0 border-l border-border pl-4">{item.history.length ? item.history.map((row, i) => <div key={i} className="relative pb-5 text-sm"><span className="absolute -left-[1.22rem] top-1 h-2 w-2 rounded-full bg-primary" /><div className="flex justify-between"><b>{statusMeta[row.status]?.label || row.status}</b><span className="text-xs text-muted-foreground">{time(row.at)}</span></div><p className="mt-1 text-xs text-muted-foreground">读数 {fmt(row.value, item.unit)} · {row.snapshot}</p>{row.evidence && <p className="mt-1 text-xs">{row.evidence}</p>}</div>) : <p className="text-sm text-muted-foreground">尚无状态变化记录</p>}</div>
      <p className="mt-4 rounded-xl bg-muted/40 p-3 text-xs text-muted-foreground">证据：{item.evidence || "当前状态由固定指标、昨晚基准与阈值自动计算。"}</p>
    </aside>
  </div>;
}

function ThemeDrawer({ theme, onClose }: { theme: TodayTheme; onClose: () => void }) {
  return <div className="fixed inset-0 z-50 flex justify-end"><button aria-label="关闭题材详情" className="absolute inset-0 bg-black/45" onClick={onClose} /><aside className="ladder-drawer glass relative m-3 w-[min(480px,calc(100vw-24px))] overflow-auto p-5"><div className="flex justify-between"><div><span className="text-xs font-semibold text-primary">统一题材抽屉</span><h2 className="mt-1 text-xl font-bold">{theme.name}</h2></div><button onClick={onClose}><X className="h-5 w-5" /></button></div>{theme.data_status !== "complete" && <div className="mt-4 flex gap-2 rounded-xl border border-warning/25 bg-warning/5 p-3 text-xs leading-relaxed text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" /><span><b>{theme.data_status === "partial" ? "仅有部分验证样本：" : "验证数据不足："}</b>{theme.missing_reason}</span></div>}<div className="mt-5 grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded-xl bg-muted/40 p-3"><span className="text-muted-foreground">生命周期</span><b className="mt-1 block">{theme.lifecycle}</b></div><div className="rounded-xl bg-muted/40 p-3"><span className="text-muted-foreground">样本中位涨跌</span><b className="mt-1 block">{fmt(theme.strength, "%")}</b></div><div className="rounded-xl bg-muted/40 p-3"><span className="text-muted-foreground">行情覆盖率</span><b className="mt-1 block">{theme.coverage == null ? "—" : `${Math.round(theme.coverage * 100)}%`}</b></div></div>{theme.binding_basis.length > 0 && <p className="mt-3 text-xs text-muted-foreground">冻结口径：{theme.binding_basis.join("、")} · {theme.sample_size} 只样本</p>}<div className="mt-5 space-y-2 text-sm"><p><b>昨晚逻辑：</b>{theme.logic || "—"}</p><p><b>失效条件：</b>{theme.risk || "—"}</p></div><h3 className="mb-2 mt-6 font-semibold">冻结成员</h3><div className="divide-y divide-border/60 rounded-xl border border-border/60">{theme.members.length ? theme.members.map((m) => <div key={m.code} className="flex items-center justify-between px-3 py-2 text-sm"><span><b>{m.name}</b><span className="ml-2 text-xs text-muted-foreground">{m.code}</span></span><span className={pctTone(m.pct)}>{fmt(m.pct, "%")}</span></div>) : <p className="p-3 text-sm text-muted-foreground">{theme.missing_reason}</p>}</div></aside></div>;
}

export function TodayValidation() {
  const [data, setData] = useState<TodayValidationData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<TodayValidationItem | null>(null);
  const [theme, setTheme] = useState<TodayTheme | null>(null);
  const [historyAt, setHistoryAt] = useState<string | undefined>();

  const load = async (at?: string) => { setLoading(true); setError(""); try { setData(await api.todayValidation(data?.prediction_date, at)); setHistoryAt(at); } catch (e) { setError(e instanceof Error ? e.message : "加载失败"); } finally { setLoading(false); } };
  useEffect(() => { load(); }, []);
  useEffect(() => { if (!data?.available || historyAt || data.stage === "已定稿") return; const timer = window.setInterval(() => load(), 60_000); return () => window.clearInterval(timer); }, [data?.available, data?.stage, historyAt]);

  const aiContext = useMemo(() => JSON.stringify({ prediction_date: data?.prediction_date, eval_date: data?.eval_date, observed_at: data?.observed_at, stage: data?.stage, selected_validation: selected, selected_theme: theme, regime: data?.regime }, null, 2), [data, selected, theme]);

  if (loading && !data) return <div className="flex h-[60vh] items-center justify-center gap-2 text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin" /> 正在读取验证档案…</div>;
  if (error && !data) return <GlassCard className="mt-8 text-center"><ShieldAlert className="mx-auto h-8 w-8 text-danger" /><p className="mt-3">{error}</p><button onClick={() => load()} className="mt-4 rounded-lg bg-primary/15 px-4 py-2 text-sm text-primary">重试</button></GlassCard>;
  if (!data?.available) return <div><header className="mb-6"><h1 className="text-2xl font-extrabold">今日验证</h1><p className="mt-1 text-sm text-muted-foreground">把昨晚的判断，交给今天的盘面验证</p></header><GlassCard className="py-16 text-center"><Target className="mx-auto h-10 w-10 text-muted-foreground/50" /><h2 className="mt-4 text-lg font-bold">还没有可验证条件</h2><p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">{data?.reason || "请先完成一份收盘复盘，系统会从固定指标中生成 2—5 条次日验证条件。"}</p></GlassCard></div>;

  const items = data.validations || [];
  const summary = data.summary;
  const dims = Object.entries(data.regime?.dimensions || {});
  const feedback = data.feedback?.buckets || [];
  const maxFeedback = Math.max(1, ...feedback.map((x) => x.count ?? 0));
  return <div className="space-y-4 pb-8">
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div><div className="flex flex-wrap items-center gap-3"><h1 className="text-2xl font-extrabold tracking-tight">今日验证</h1><span className="rounded-full bg-primary/10 px-3 py-1 text-xs font-semibold text-primary"><span className="mr-1 inline-block h-2 w-2 animate-pulse rounded-full bg-primary" />{data.stage}{data.observed_at ? ` · ${time(data.observed_at)}` : ""}</span></div><p className="mt-1 text-sm text-muted-foreground">把昨晚的判断，交给今天的盘面验证</p></div>
      <div className="flex gap-2"><button aria-label="刷新今日验证" onClick={() => load(historyAt)} disabled={loading} className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card/60 px-3 py-2 text-sm"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button>{historyAt && <button onClick={() => load()} className="inline-flex items-center gap-1 rounded-lg border border-border px-3 py-2 text-sm"><RotateCcw className="h-4 w-4" />回到最新</button>}<AskAiButton context={aiContext} suggestions={["昨晚哪些判断已经被证伪？", "当前最大的盘面风险是什么？"]} /></div>
    </header>

    {historyAt && <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning">正在回看历史截面，仅使用当时已落盘的数据，不包含未来信息。</div>}
    {data.delayed && <div className="rounded-xl border border-warning/30 bg-warning/10 px-4 py-2 text-sm text-warning">数据采集延迟，当前状态可能滞后；系统不会把数据中断判成盘面变化。</div>}

    <GlassCard className="overflow-x-auto p-4"><div className="flex min-w-[680px] items-start">{(data.checkpoints || []).map((cp, i) => { const done = cp.state === "completed"; const active = cp.history_key === historyAt || (!historyAt && done && !(data.checkpoints || []).slice(i + 1).some((x) => x.state === "completed")); return <div key={cp.key} className="relative flex flex-1 flex-col items-center"><div className={cn("absolute left-0 top-3 h-0.5 w-full -translate-x-1/2", i === 0 ? "hidden" : done ? "bg-primary" : "bg-border")} /><button aria-label={`回看 ${cp.time} ${cp.label}`} disabled={!cp.history_key} onClick={() => cp.history_key && load(cp.history_key)} className={cn("relative z-10 flex h-7 w-7 items-center justify-center rounded-full border text-xs font-bold", done ? "border-primary bg-primary text-primary-foreground" : "border-border bg-muted text-muted-foreground", active && "ring-4 ring-primary/15")}>{done ? <Check className="h-3.5 w-3.5" /> : i + 1}</button><b className="mt-2 text-xs">{cp.time} {cp.label}</b><span className={cn("mt-0.5 text-[10px]", done ? "text-primary" : cp.state === "missing" ? "text-warning" : "text-muted-foreground")}>{done ? "已记录" : cp.state === "missing" ? "数据缺失" : "待开始"}</span></div>; })}</div></GlassCard>

    <GlassCard className="grid grid-cols-2 gap-3 p-4 sm:grid-cols-4">{([
      ["验证进度", `${(summary?.satisfied || 0) + (summary?.invalidated || 0)}/${summary?.total || 0}`, Target, "text-primary bg-primary/10"],
      ["已满足", summary?.satisfied || 0, Check, "text-success bg-success/10"],
      ["待观察", (summary?.watching || 0) + (summary?.pending || 0), Eye, "text-warning bg-warning/10"],
      ["已证伪", summary?.invalidated || 0, X, "text-danger bg-danger/10"],
    ] as const).map(([label, value, Icon, tone]) => <div key={label} className="flex items-center justify-center gap-3 border-border/60 sm:border-r sm:last:border-0"><span className={cn("rounded-full p-2", tone)}><Icon className="h-5 w-5" /></span><div><p className="text-xs text-muted-foreground">{label}</p><b className="text-xl">{value}</b></div></div>)}</GlassCard>

    <div className="grid gap-4 lg:grid-cols-[1.35fr_.85fr]">
      <div className="space-y-4">
        <GlassCard className="p-4"><SectionTitle extra={<span className="text-xs text-muted-foreground">昨晚 {data.prediction_date}</span>}>昨晚判断与今日验证</SectionTitle><div className="space-y-2">{items.map((item) => { const meta = statusMeta[item.status]; const Icon = meta.icon; return <button key={item.id} onClick={() => setSelected(item)} className="grid w-full grid-cols-[auto_1fr_auto] items-center gap-3 rounded-xl border border-border/60 bg-card/35 px-3 py-2.5 text-left transition hover:border-primary/30 hover:bg-primary/[.03]"><span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-semibold", meta.color)}><Icon className="h-3 w-3" />{meta.label}</span><div className="min-w-0"><p className="truncate text-sm font-medium">{item.judgment}</p><p className="mt-0.5 text-[11px] text-muted-foreground">{item.label} · 基准 {fmt(item.baseline, item.unit)} · 预期{item.expected_direction}</p></div><div className="flex items-center gap-2"><b className={pctTone(item.current_value)}>{fmt(item.current_value, item.unit)}</b><ChevronRight className="h-4 w-4 text-muted-foreground" /></div></button>; })}{!items.length && <p className="py-6 text-center text-sm text-muted-foreground">条件数据不足</p>}</div></GlassCard>

        <GlassCard className="p-4"><SectionTitle extra={<span className="rounded-lg bg-primary/10 px-3 py-1 text-xs font-semibold text-primary">{data.regime?.phase_label || data.regime?.phase || "数据不足"}</span>}>情绪状态</SectionTitle><div className="grid items-center gap-3 sm:grid-cols-[220px_1fr]"><Radar values={dims} /><div className="space-y-3">{dims.length ? dims.slice(0, 6).map(([k, v]) => <MetricBar key={k} label={k} value={v} detail={data.regime?.dimension_evidence?.[k]?.detail} />) : <p className="text-sm text-muted-foreground">六维信号尚未完整落盘</p>}<p className="rounded-lg bg-muted/35 p-3 text-xs leading-relaxed text-muted-foreground">六维已归一化至 0–10 分；悬停可查看原始证据。{data.regime?.summary}</p></div></div></GlassCard>

        <GlassCard className="p-4"><SectionTitle>主线题材</SectionTitle><div className="divide-y divide-border/60">{(data.themes || []).slice(0, 5).map((row) => <button key={row.id} onClick={() => setTheme(row)} className="grid w-full grid-cols-[1fr_auto_auto] items-center gap-4 py-2.5 text-left"><div><b className="text-sm">{row.name}</b><p className="mt-0.5 line-clamp-1 text-[11px] text-muted-foreground">{row.data_status === "complete" ? row.logic || "题材逻辑待补齐" : row.missing_reason}</p></div><span className={cn("rounded-lg px-2 py-1 text-[11px]", row.data_status === "complete" ? "bg-primary/10 text-primary" : "bg-warning/10 text-warning")}>{row.data_status === "partial" ? "部分样本" : row.data_status === "missing" ? "数据不足" : row.lifecycle}</span><span className={cn("min-w-14 text-right text-sm font-bold", pctTone(row.strength))}>{fmt(row.strength, "%")}</span></button>)}{!(data.themes || []).length && <p className="py-6 text-center text-sm text-muted-foreground">暂无已冻结的真实事件题材映射</p>}</div></GlassCard>
      </div>

      <div className="space-y-4">
        <GlassCard className="p-4"><SectionTitle>盘中变化</SectionTitle><div className="relative space-y-4 pl-5 before:absolute before:bottom-2 before:left-[5px] before:top-2 before:w-px before:bg-border">{(data.events || []).length ? (data.events || []).map((event) => <button key={event.id} onClick={() => { const match = items.find((x) => event.scope.includes(x.metric)); if (match) setSelected(match); }} className="relative block w-full text-left"><span className={cn("absolute -left-5 top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-card", event.severity === "risk" ? "bg-danger" : event.severity === "watch" ? "bg-warning" : "bg-primary")} /><div className="flex justify-between gap-2"><b className="text-sm">{event.title}</b><span className="text-xs text-muted-foreground">{time(event.occurred_at)}</span></div><p className="mt-1 text-xs text-muted-foreground">{event.evidence}</p></button>) : <div className="py-6 text-center text-sm text-muted-foreground">尚无超过噪声阈值的状态变化</div>}</div></GlassCard>

        <GlassCard className="p-4"><SectionTitle>昨日强势股反馈</SectionTitle>{data.feedback?.complete === false && <div className="mb-3 flex gap-2 rounded-lg border border-warning/25 bg-warning/5 p-2 text-xs text-warning"><AlertTriangle className="h-3.5 w-3.5 shrink-0" />{data.feedback.reason || "部分分类数据不足，未知项不按 0 展示"}</div>}<div className="space-y-2">{feedback.map((row, i) => <div key={row.label} className="grid grid-cols-[4rem_2rem_1fr_2.5rem] items-center gap-2 text-xs"><span>{row.label}</span><b className={i < 2 && row.count != null ? "text-danger" : ""}>{row.count ?? "—"}</b><div className="h-2 rounded-full bg-muted"><div className={cn("h-full rounded-full", i === 0 ? "bg-danger" : i === 1 ? "bg-primary" : i >= 3 ? "bg-success" : "bg-muted-foreground/60")} style={{ width: `${(row.count ?? 0) / maxFeedback * 100}%` }} /></div><span className="text-right text-muted-foreground">{row.ratio == null ? "—" : `${Math.round(row.ratio * 100)}%`}</span></div>)}</div></GlassCard>

        <GlassCard className="p-4"><SectionTitle>接力结构</SectionTitle><div className="grid grid-cols-5 gap-2">{[5, 4, 3, 2, 1].map((board) => { const count = (data.feedback?.members || []).filter((m) => (m as { boards?: number }).boards === board).length; return <div key={board} className="rounded-lg bg-muted/40 px-1 py-2 text-center"><span className="block text-[10px] text-muted-foreground">{board === 1 ? "首板" : `${board}板`}</span><b className={board >= 3 ? "text-danger" : ""}>{count || "—"}</b></div>; })}</div></GlassCard>

        <GlassCard className="p-4"><SectionTitle>关键风险</SectionTitle><div className="space-y-2">{(data.risks || []).map((risk, i) => <div key={i} className="flex gap-2 rounded-lg bg-warning/5 p-2 text-sm"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" /><span>{risk}</span></div>)}{!(data.risks || []).length && <p className="py-4 text-center text-sm text-muted-foreground">昨晚未记录额外风险项</p>}</div></GlassCard>
      </div>
    </div>
    <p className="text-center text-[11px] text-muted-foreground">状态颜色仅表达验证结果；红涨绿跌遵循 A 股习惯。所有结论仅供复盘，不构成投资建议。</p>
    {selected && <ValidationDrawer item={selected} onClose={() => setSelected(null)} />}
    {theme && <ThemeDrawer theme={theme} onClose={() => setTheme(null)} />}
  </div>;
}
