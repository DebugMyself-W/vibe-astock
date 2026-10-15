import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import {
  AlertTriangle, CheckCircle2, ChevronDown, Clock3, FlaskConical,
  History, Loader2, RefreshCw, Search, Sparkles, Target, X,
} from "lucide-react";

import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import {
  api, type BoardPatternAlgorithm, type BoardPatternBar, type BoardPatternCase,
  type BoardPatternEvent, type BoardPatternEvents, type BoardPatternMode, type BoardPatternResult,
} from "@/lib/api";
import { cn } from "@/lib/utils";

const SYNC_PHASE_LABELS: Record<string, string> = {
  queued: "等待同步", directory_repair: "规范证券目录清洗", corpus_universe: "历史证券清单", corpus_bars: "全市场日线",
  events: "连板事件重建", verify: "完整性验证", complete: "构建完成",
  interrupted: "上次构建被中断", failed: "构建失败", database: "数据库检查",
};

function signed(value?: number | null, digits = 2) {
  return value == null || !Number.isFinite(value) ? "—" : `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function pathText(event: Pick<BoardPatternEvent, "path" | "board_height">) {
  return event.path?.length === event.board_height
    ? event.path.map((type, index) => `${index + 1}${type}`).join(" → ")
    : "逐板路径待补齐";
}

function PatternChart({ bars, eventDate, compact = false }: {
  bars: BoardPatternBar[]; eventDate: string; compact?: boolean;
}) {
  const root = useRef<HTMLDivElement>(null);
  const option = useMemo<EChartsOption>(() => {
    const rows = bars || [];
    const dates = rows.map((row) => row.date);
    const closes = rows.map((row) => Number(row.close));
    const ma5 = closes.map((_, index) => index < 4 ? null : Number((closes.slice(index - 4, index + 1).reduce((a, b) => a + b, 0) / 5).toFixed(3)));
    const eventIndex = dates.indexOf(eventDate);
    const marks: Array<{ name: string; xAxis: number; itemStyle?: { color: string }; label?: { color: string } }> = [];
    if (eventIndex >= 0) {
      marks.push({ name: "观察日", xAxis: eventIndex, itemStyle: { color: "#ef4444" }, label: { color: "#ef4444" } });
      if (eventIndex + 1 < dates.length) marks.push({ name: "T+1", xAxis: eventIndex + 1, itemStyle: { color: "#2563eb" }, label: { color: "#2563eb" } });
      if (eventIndex + 3 < dates.length) marks.push({ name: "T+3", xAxis: eventIndex + 3, itemStyle: { color: "#2563eb" }, label: { color: "#2563eb" } });
    }
    return {
      animation: false,
      tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
      grid: [
        { left: compact ? 30 : 42, right: 12, top: 12, height: compact ? "63%" : "66%" },
        { left: compact ? 30 : 42, right: 12, top: compact ? "78%" : "80%", height: compact ? "15%" : "13%" },
      ],
      xAxis: [
        { type: "category", data: dates, axisLabel: { show: false }, axisTick: { show: false } },
        { type: "category", data: dates, gridIndex: 1, axisLabel: { show: !compact, fontSize: 9 }, axisTick: { show: false } },
      ],
      yAxis: [
        { scale: true, axisLabel: { show: !compact, fontSize: 9 }, splitLine: { lineStyle: { type: "dashed", opacity: .35 } } },
        { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
      ],
      series: [
        {
          name: "K线", type: "candlestick",
          data: rows.map((row) => [row.open, row.close, row.low, row.high]),
          itemStyle: { color: "#ef4444", color0: "#52c41a", borderColor: "#ef4444", borderColor0: "#52c41a" },
          markLine: marks.length ? { symbol: ["none", "none"], label: { formatter: "{b}", fontSize: compact ? 8 : 10 }, lineStyle: { type: "dashed", width: 1 }, data: marks } : undefined,
        },
        { name: "MA5", type: "line", data: ma5, showSymbol: false, lineStyle: { color: "#60a5fa", width: 1.4 } },
        {
          name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1,
          data: rows.map((row) => ({ value: row.volume || 0, itemStyle: { color: row.close >= row.open ? "rgba(251,113,133,.78)" : "rgba(82,196,26,.72)" } })),
        },
      ],
    };
  }, [bars, compact, eventDate]);
  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    chart.setOption(option, true);
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option]);
  return <div ref={root} className={compact ? "h-52 w-full" : "h-[360px] w-full md:h-[430px]"} />;
}

function EventCard({ item, active, onClick }: { item: BoardPatternEvent; active: boolean; onClick: () => void }) {
  const eligible = item.matching_eligible !== false;
  return <button type="button" onClick={onClick} disabled={!eligible} title={!eligible ? item.matching_note : undefined} className={cn(
    "w-full rounded-xl border px-3 py-3 text-left transition hover:border-primary/50 hover:bg-primary/5 disabled:cursor-not-allowed disabled:opacity-55 disabled:hover:border-border disabled:hover:bg-card/60",
    active ? "border-primary bg-primary/8 shadow-sm" : "border-border bg-card/60",
  )}>
    <div className="flex items-start justify-between gap-2">
      <div className="min-w-0"><b className="block truncate text-sm">{item.name}</b><small className="font-mono text-[10px] text-muted-foreground">{item.code} · {item.industry || "未知"}</small></div>
      <span className="shrink-0 text-sm font-black text-danger">{item.board_height}板</span>
    </div>
    <p className="mt-2 truncate text-[11px] text-muted-foreground" title={eligible ? pathText(item) : item.matching_note}>{eligible ? pathText(item) : "逐板路径待补齐 · 暂不可计算"}</p>
  </button>;
}

function EventPool({ data, selected, choose }: { data: BoardPatternEvents; selected: string; choose: (item: BoardPatternEvent) => void }) {
  return <aside className="glass min-h-0 overflow-hidden rounded-2xl border border-border/70 lg:sticky lg:top-4 lg:h-[calc(100vh-120px)]">
    <div className="flex items-center justify-between border-b border-border px-4 py-3">
      <h2 className="font-bold">剧本事件池</h2><span className="text-xs text-muted-foreground">{data.date} · {data.events.length}只</span>
    </div>
    <div className="max-h-[420px] space-y-5 overflow-y-auto p-3 lg:max-h-none lg:h-[calc(100%-52px)]">
      {data.groups.map((group) => <section key={group.key}>
        <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold"><span>{group.label}</span><span className="text-muted-foreground">{group.count}</span></div>
        <div className="space-y-2">{group.items.map((item) => <EventCard key={item.code} item={item} active={selected === item.code} onClick={() => choose(item)} />)}</div>
      </section>)}
      {!data.events.length && <div className="py-12 text-center text-xs text-muted-foreground">事件池尚未就绪</div>}
    </div>
  </aside>;
}

function StatCard({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div className="rounded-xl bg-muted/40 px-4 py-3"><span className="text-xs text-muted-foreground">{label}</span><b className="mt-1 block text-xl">{value}</b><small className="text-[10px] text-muted-foreground">{detail}</small></div>;
}

const FACTORS: Array<[keyof BoardPatternCase["scores"], string, string]> = [
  ["identity", "身份", "bg-blue-500"], ["path", "逐板板型", "bg-emerald-600"],
  ["volume_price", "量价形态", "bg-blue-500"], ["liquidity", "流动性", "bg-blue-500"],
  ["environment", "市场环境", "bg-blue-500"],
];

function CaseCard({ item }: { item: BoardPatternCase }) {
  return <article className="overflow-hidden rounded-2xl border border-border bg-card/60 p-3 shadow-sm">
    <div className="flex items-start justify-between gap-2 px-1">
      <div><b>{item.name}</b><small className="ml-1 font-mono text-muted-foreground">{item.code}</small><p className="mt-1 text-[10px] text-muted-foreground">{item.event_date} · {item.board_height}板 · {pathText(item)}</p></div>
      <span className={cn("rounded-lg border px-2 py-1 text-xs font-semibold", item.scores.total >= 80 ? "border-danger/35 bg-danger/5 text-danger" : "border-warning/35 bg-warning/5 text-warning")}>综合{item.scores.total} · 路径{item.scores.path}</span>
    </div>
    <PatternChart bars={item.chart} eventDate={item.event_date} compact />
    <div className="grid grid-cols-5 gap-2 px-1">{FACTORS.map(([key, label, color]) => <div key={key} className="min-w-0"><div className="mb-1 truncate text-[9px] text-muted-foreground">{label} {item.scores[key] as number}</div><div className="h-1.5 overflow-hidden rounded-full bg-muted"><div className={cn("h-full rounded-full", color)} style={{ width: `${item.scores[key] as number}%` }} /></div></div>)}</div>
    <div className="mt-3 flex items-end justify-between gap-3 px-1 text-[11px]">
      <div className="min-w-0 text-muted-foreground"><p className="truncate">同为{item.board_height}板节点 · {item.scores.exact_path ? "完整逐板路径一致" : `相同后缀 ${item.scores.path_suffix} 板`}</p><p className="truncate">题材：{item.theme || item.industry || "未知"}</p></div>
      <div className="shrink-0 text-right"><b>{item.result.label}</b><p>T+1 收 {signed(item.result.t1)}</p><p>T+3 收 {signed(item.result.t3)}</p></div>
    </div>
  </article>;
}

function ResultPanel({ result }: { result: BoardPatternResult }) {
  const target = result.target;
  const stats = result.stats;
  return <div className="space-y-4">
    <GlassCard className="overflow-hidden p-0">
      <div className="flex flex-wrap items-start justify-between gap-3 px-5 py-4">
        <div><h2 className="text-2xl font-black">{target.name} <span className="font-mono text-base">{target.code}</span></h2><p className="mt-1 text-xs text-muted-foreground">{result.mode === "intraday" ? "盘中预演" : "收盘定稿"}｜{target.board_height}板{target.status === "sealed" ? "连板核心" : "断板"}｜{pathText(target)}</p></div>
        <div className="flex flex-wrap gap-2 text-xs"><span className="rounded-lg border border-danger/35 bg-danger/5 px-2 py-1 text-danger">{target.status === "sealed" ? "连板进行中" : result.mode === "intraday" ? "临时断板" : "确认断板"}</span><span className="rounded-lg border border-primary/30 px-2 py-1">{result.algorithm.toUpperCase()} · {result.algorithm === "v1" ? "综合匹配" : "全景匹配"}</span><span className="rounded-lg border border-success/30 bg-success/5 px-2 py-1 text-success">样本置信度 {stats.sample_count >= 20 ? "高" : "有限"}</span></div>
      </div>
      <div className="border-t border-border px-3"><PatternChart bars={target.chart} eventDate={target.event_date} /></div>
    </GlassCard>
    <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
      <StatCard label="相似样本" value={String(stats.sample_count)} detail={`完全同板型 ${stats.complete_path_count}`} />
      <StatCard label="次日收红" value={signed(stats.next_red_rate, 0)} detail={`${stats.next_limit_up}/${stats.sample_count} 次日涨停`} />
      <StatCard label="次日平均最高" value={signed(stats.next_avg_high)} detail="历史样本" />
      <StatCard label="次日收盘中位数" value={signed(stats.next_close_median)} detail={`均值 ${signed(stats.next_close_mean)} · 跌停 ${stats.next_limit_down}`} />
    </div>
    {result.warnings.map((warning) => <div key={warning} className="flex items-start gap-2 rounded-xl border border-warning/35 bg-warning/10 px-4 py-3 text-xs text-warning"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{warning}</div>)}
    <section>
      <h3 className="mb-3 flex items-center gap-2 font-black"><History className="h-4 w-4" />{result.algorithm.toUpperCase()} · {result.algorithm === "v1" ? "综合匹配" : "全景匹配"} · 历史对照</h3>
      <div className="grid gap-3 xl:grid-cols-2">{result.cases.map((item) => <CaseCard key={`${item.event_date}-${item.code}`} item={item} />)}</div>
      {!result.cases.length && <GlassCard className="py-14 text-center text-sm text-muted-foreground">没有满足完整 T+3 验证条件的历史案例</GlassCard>}
    </section>
    <section><h3 className="mb-3 flex items-center gap-2 font-black"><Target className="h-4 w-4" />下一交易日验证剧本</h3><div className="grid gap-3 md:grid-cols-3">{result.scripts.map((script) => <GlassCard key={script.title} className="p-4"><b>{script.title}</b><p className="mt-2 text-xs leading-5 text-muted-foreground">{script.detail}</p></GlassCard>)}</div></section>
    <div className="rounded-2xl border border-info/30 bg-info/10 px-5 py-4 text-sm leading-7 text-muted-foreground">本地算法只对照同一连板高度与状态。V1 先按完整路径、相同路径后缀和量价结构排序；V2 按 20 日 OHLCV 形态排序。{result.disclaimer}</div>
  </div>;
}

export function BoardPatternResearch() {
  const [params, setParams] = useSearchParams();
  const mode = (params.get("mode") === "intraday" ? "intraday" : "close") as BoardPatternMode;
  const algorithm = (params.get("algorithm") === "v2" ? "v2" : "v1") as BoardPatternAlgorithm;
  const selectedDate = params.get("date") || "";
  const selectedCode = params.get("code") || "";
  const [data, setData] = useState<BoardPatternEvents | null>(null);
  const [result, setResult] = useState<BoardPatternResult | null>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [calculating, setCalculating] = useState(false);
  const [calculationStatus, setCalculationStatus] = useState("");
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [poolOpen, setPoolOpen] = useState(false);
  const resultRoot = useRef<HTMLElement>(null);

  const patchParams = useCallback((values: Record<string, string | null>) => {
    const next = new URLSearchParams(params);
    Object.entries(values).forEach(([key, value]) => value ? next.set(key, value) : next.delete(key));
    setParams(next, { replace: true });
  }, [params, setParams]);

  const loadEvents = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const next = await api.boardPatternEvents(mode, selectedDate || undefined);
      setData(next); setError("");
      const firstEligible = next.events.find((item) => item.matching_eligible !== false);
      if (!selectedCode && firstEligible) {
        patchParams({ code: firstEligible.code });
        setQuery(firstEligible.name);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "剧本事件池加载失败");
    } finally { if (!silent) setLoading(false); }
  }, [mode, patchParams, selectedCode, selectedDate]);

  useEffect(() => { void loadEvents(); }, [loadEvents, reload]);
  useEffect(() => {
    if (mode !== "intraday") return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") void loadEvents(true);
    }, 15_000);
    return () => window.clearInterval(timer);
  }, [loadEvents, mode]);
  useEffect(() => {
    const windowState = data?.intraday_window;
    if (!windowState) return;
    const boundary = windowState.available ? windowState.closes_at : windowState.opens_at;
    const boundaryAt = Date.parse(`${windowState.date}T${boundary}:02+08:00`);
    const delay = boundaryAt - Date.now();
    if (!Number.isFinite(delay) || delay <= 0 || delay > 24 * 60 * 60 * 1000) return;
    const timer = window.setTimeout(() => setReload((value) => value + 1), delay);
    return () => window.clearTimeout(timer);
  }, [data?.intraday_window]);
  useEffect(() => {
    const item = data?.events.find((event) => event.code === selectedCode);
    if (item && !query) setQuery(item.name);
  }, [data, query, selectedCode]);
  useEffect(() => {
    if (data?.coverage !== "building") return;
    const timer = window.setInterval(() => {
      api.boardPatternStatus().then((sync) => {
        setData((current) => current ? { ...current, sync } : current);
        if (sync.state === "ready" || sync.state === "failed") setReload((value) => value + 1);
      }).catch(() => undefined);
    }, 1800);
    return () => window.clearInterval(timer);
  }, [data?.coverage]);

  const suggestions = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle || data?.events.some((item) => item.code === selectedCode && item.name === query)) return [];
    return (data?.events || []).filter((item) => item.matching_eligible !== false && (item.code.includes(needle) || item.name.toLowerCase().includes(needle))).slice(0, 10);
  }, [data?.events, query, selectedCode]);
  const selectedEvent = useMemo(
    () => data?.events.find((item) => item.code === selectedCode) || null,
    [data?.events, selectedCode],
  );
  const intradayOpen = data?.intraday_window?.available === true;
  const intradayBlocked = mode === "intraday" && !intradayOpen;
  const choose = (item: BoardPatternEvent) => {
    setQuery(item.name); setResult(null); setCalculationStatus(""); setPoolOpen(false); patchParams({ code: item.code });
  };
  const calculate = async () => {
    if (intradayBlocked) {
      setCalculationStatus("");
      setError(data?.intraday_window?.reason || "盘中预演仅在交易日 09:25—15:00 开放");
      return;
    }
    if (!selectedCode || !selectedEvent) {
      setCalculationStatus(""); setError("请先从事件池或搜索候选中选择有效股票"); return;
    }
    if (selectedEvent.matching_eligible === false) {
      setCalculationStatus(""); setError(selectedEvent.matching_note || "该标的盘中逐板路径尚未完整，暂不可计算历史匹配"); return;
    }
    if (!data?.matching_available) {
      setCalculationStatus(""); setError("历史匹配库尚未完成，暂时不能计算"); return;
    }
    setCalculating(true); setError(""); setCalculationStatus(`正在计算 ${selectedEvent.name} 的历史匹配，首次计算可能需要几秒…`);
    try {
      const next = await api.boardPatternCalculate({ code: selectedCode, mode, algorithm, date: selectedDate || undefined });
      setResult(next);
      setCalculationStatus(`计算完成：${next.target.name} · ${next.stats.sample_count} 个完整历史样本`);
      window.requestAnimationFrame(() => resultRoot.current?.scrollIntoView({ behavior: "smooth", block: "start" }));
    }
    catch (reason) {
      setCalculationStatus(""); setError(reason instanceof Error ? reason.message : "匹配计算失败");
    }
    finally { setCalculating(false); }
  };
  const retry = async () => {
    setLoading(true); setError("");
    try { await api.syncBoardPattern(selectedDate || undefined); setReload((value) => value + 1); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "同步启动失败"); }
    finally { setLoading(false); }
  };
  const switchMode = (next: BoardPatternMode) => {
    setResult(null); setQuery(""); setCalculationStatus("");
    patchParams({ mode: next, code: null, date: next === "intraday" ? null : selectedDate || null });
  };
  const switchAlgorithm = (next: BoardPatternAlgorithm) => { setResult(null); setCalculationStatus(""); patchParams({ algorithm: next }); };
  const sync = data?.sync;
  const syncQuality = sync?.quality;
  const failures = sync?.failure_summary;

  return <div className="mx-auto max-w-[1660px] px-1 pb-10">
    <PageHeader title="连板剧本研究" subtitle="点选连板或断板节点，回填股票并对照历史路径、量价结构与后续表现" actions={<div className="flex items-center gap-2"><label className="hidden items-center gap-2 text-xs text-muted-foreground md:flex"><span>回溯范围</span><select value={selectedDate} onChange={(event) => { setResult(null); patchParams({ date: event.target.value || null }); }} className="h-10 rounded-xl border border-border bg-card px-3"><option value="">近1年</option>{[...(data?.available_dates || [])].reverse().map((date) => <option key={date} value={date}>{date}</option>)}</select></label><button aria-label="刷新剧本研究" onClick={() => setReload((value) => value + 1)} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card px-3 text-xs font-semibold"><RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />刷新</button></div>} />

    <GlassCard className="mb-4 p-4">
      <div className="grid gap-3 lg:grid-cols-[410px_minmax(0,1fr)]">
        <div className="grid grid-cols-2 gap-2">
          <button onClick={() => switchMode("close")} className={cn("rounded-xl border px-4 py-3 text-left", mode === "close" ? "border-primary bg-primary/8 text-primary" : "border-border")}><b className="block text-sm">收盘定稿</b><small className="text-[10px] text-muted-foreground">完整日K与最终连板状态</small></button>
          <button onClick={() => switchMode("intraday")} disabled={!intradayOpen} title={!intradayOpen ? (data?.intraday_window?.reason || "交易日 09:25—15:00 开放") : undefined} className={cn("rounded-xl border px-4 py-3 text-left disabled:cursor-not-allowed disabled:opacity-50", mode === "intraday" ? "border-primary bg-primary/8 text-primary" : "border-border")}><b className="flex items-center gap-1 text-sm"><Clock3 className="h-3.5 w-3.5" />盘中预演</b><small className="text-[10px] text-muted-foreground">交易日 09:25—15:00 开放</small></button>
        </div>
        <div className={cn("flex items-center rounded-xl border px-4 text-xs", mode === "intraday" ? "border-warning/30 bg-warning/8 text-warning" : "border-border bg-muted/30 text-muted-foreground")}>{mode === "intraday" ? (intradayBlocked ? (data?.intraday_window?.reason || "交易日 09:25—15:00 开放") : `行情锚定 ${data?.anchor_time?.replace("T", " ") || "—"}，每 15 秒刷新`) : "使用最近已完成交易日数据；目标观察日之后的数据不参与匹配。"}</div>
      </div>
      <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(280px,1fr)]">
        <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input disabled={intradayBlocked} value={query} onChange={(event) => { setQuery(event.target.value); patchParams({ code: null }); }} placeholder={intradayBlocked ? "盘中预演开放后可选择股票" : "输入事件池中的股票代码或名称"} className="h-10 w-full rounded-xl border border-border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary disabled:cursor-not-allowed disabled:opacity-55" />{!!suggestions.length && <div className="absolute inset-x-0 top-12 z-40 overflow-hidden rounded-xl border border-border bg-card shadow-xl">{suggestions.map((item) => <button key={item.code} onClick={() => choose(item)} className="flex w-full items-center justify-between px-3 py-2 text-left hover:bg-muted"><span><b>{item.name}</b><small className="ml-2 font-mono text-muted-foreground">{item.code}</small></span><small>{item.board_height}板 · {item.board_type}</small></button>)}</div>}</div>
        <button onClick={calculate} disabled={calculating || intradayBlocked} aria-busy={calculating} className="inline-flex h-10 items-center justify-center gap-2 rounded-xl bg-primary font-bold text-primary-foreground disabled:cursor-not-allowed disabled:opacity-55">{calculating ? <Loader2 className="h-4 w-4 animate-spin" /> : <FlaskConical className="h-4 w-4" />}{calculating ? "正在计算…" : "开始计算"}</button>
      </div>
      <p role="status" aria-live="polite" className={cn("mt-2 min-h-5 text-xs", calculating ? "font-semibold text-primary" : "text-muted-foreground")}>{intradayBlocked ? (data?.intraday_window?.reason || "盘中预演仅在交易日 09:25—15:00 开放") : calculationStatus || (selectedEvent ? `已选择 ${selectedEvent.name}（${selectedEvent.code}）` : "请从事件池或搜索候选中选择标的")}</p>
      <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-border pt-3">
        <div className="flex rounded-xl bg-muted/45 p-1"><button onClick={() => switchAlgorithm("v1")} className={cn("rounded-lg px-4 py-2 text-xs font-bold", algorithm === "v1" && "bg-primary text-primary-foreground shadow")}>V1 · 综合匹配</button><button onClick={() => switchAlgorithm("v2")} className={cn("rounded-lg px-4 py-2 text-xs font-bold", algorithm === "v2" && "bg-primary text-primary-foreground shadow")}>V2 · 全景匹配</button></div>
        <p className="text-xs text-muted-foreground">{algorithm === "v1" ? "同板高且最近两板一致，先按逐板路径、再按量价结构排序。" : "同状态与板高下，按事件日前 20 个交易日标准化 OHLCV 形态排序。"}</p>
      </div>
    </GlassCard>

    {error && <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-xs text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{error}<button onClick={() => setError("")} className="ml-auto"><X className="h-4 w-4" /></button></div>}
    {intradayBlocked && <div className="mb-4 flex items-center gap-2 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-xs text-warning"><Clock3 className="h-4 w-4" /><b>盘中预演尚未开放</b><span>{data?.intraday_window?.reason || "交易日 09:25—15:00 开放"}</span></div>}
    {data?.coverage === "building" && <div className="mb-4 rounded-xl border border-warning/30 bg-warning/10 px-4 py-3 text-xs text-warning"><div className="flex flex-wrap items-center gap-3"><Loader2 className="h-4 w-4 animate-spin" /><b>历史库构建中</b><span>当前阶段：{SYNC_PHASE_LABELS[sync?.phase || ""] || sync?.phase} · {sync?.completed || 0}/{sync?.total || 0} · {sync?.progress || 0}%</span><span>这是当前阶段进度；事件池可先查看，完整性验证前不展示部分匹配统计。</span>{sync?.state !== "running" && sync?.state !== "queued" && <button onClick={retry} className="ml-auto rounded-lg border border-warning/40 px-2 py-1 font-semibold">继续构建</button>}</div><div className="mt-2 h-1.5 overflow-hidden rounded-full bg-background/60"><div className="h-full bg-warning" style={{ width: `${sync?.progress || 0}%` }} /></div>{syncQuality && <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]"><span>规范证券 {syncQuality.canonical_instrument_count || 0}</span><span>证券清单 {syncQuality.universe_ready_dates || 0}/{syncQuality.corpus_required_dates || 280}</span><span>完整日线 {syncQuality.bars_ready_dates || 0}/{syncQuality.corpus_required_dates || 280}</span><span>v2 快照 {syncQuality.snapshot_ready_dates || 0}/{syncQuality.required_dates || 250}</span><span>污染移除 {syncQuality.invalid_directory_count || 0}</span><span>别名归并 {syncQuality.alias_merged_count || 0}</span><span>生命周期排除 {syncQuality.lifecycle_excluded_count || 0}</span><span>未知缺口 {syncQuality.unknown_gap_count || 0}</span><span>更新 {syncQuality.updated_at?.replace("T", " ") || "—"}</span></div>}</div>}
    {data?.sync.state === "failed" && <div className="mb-4 rounded-xl border border-danger/30 bg-danger/10 px-4 py-3 text-xs text-danger"><div className="flex items-center gap-2"><AlertTriangle className="h-4 w-4" /><span className="min-w-0 flex-1">{data.sync.error || "历史库构建失败"}</span>{(failures?.retriable_count || 0) > 0 && <button onClick={retry} className="rounded-lg border border-danger/35 px-3 py-1">重新同步</button>}</div>{failures && <div className="mt-2 border-t border-danger/20 pt-2 text-[11px]"><p>可重试 {failures.retriable_count} 项 · 确定性失败 {failures.deterministic_count} 项{failures.categories && ` · ${Object.entries(failures.categories).map(([key, value]) => `${key} ${value}`).join(" · ")}`}</p>{failures.samples.slice(0, 3).map((item) => <p key={`${item.trade_date}-${item.phase}-${item.source}`} className="mt-1 break-all">{item.trade_date || "全局"} · {SYNC_PHASE_LABELS[item.phase] || item.phase} · {item.category || "unknown"} · {item.message}</p>)}</div>}</div>}

    <button onClick={() => setPoolOpen((value) => !value)} className="mb-3 flex w-full items-center justify-between rounded-xl border border-border bg-card px-4 py-3 text-sm font-semibold lg:hidden">事件池 {data?.events.length || 0}只<ChevronDown className={cn("h-4 w-4 transition", poolOpen && "rotate-180")} /></button>
    <div className="grid items-start gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
      {data && <div className={cn(!poolOpen && "hidden lg:block")}><EventPool data={data} selected={selectedCode} choose={choose} /></div>}
      <main ref={resultRoot} className="min-w-0 scroll-mt-4">
        {loading && !data ? <GlassCard className="grid min-h-[520px] place-items-center"><span className="inline-flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin text-primary" />正在读取连板事件池…</span></GlassCard>
          : result ? <ResultPanel result={result} />
          : <GlassCard className="grid min-h-[520px] place-items-center text-center"><div className="max-w-md"><Sparkles className="mx-auto h-12 w-12 text-primary/45" /><h2 className="mt-4 text-lg font-bold">选择事件并开始计算</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">主标 K 线、历史相似样本、五项透明评分和下一交易日验证剧本将在这里展开。</p>{data?.matching_available ? <p className="mt-4 inline-flex items-center gap-1 text-xs text-success"><CheckCircle2 className="h-4 w-4" />近一年历史库已通过完整性验证</p> : <p className="mt-4 text-xs text-warning">历史匹配将在完整库构建完成后开放</p>}</div></GlassCard>}
      </main>
    </div>
    <p className="mt-4 text-[11px] leading-5 text-muted-foreground">算法 {data?.algorithm_version || "board-pattern-v3"} · 规范证券 {data?.quality.canonical_instrument_count || 0} · 280天语料 {data?.quality.bars_ready_dates || 0}/{data?.quality.corpus_required_dates || 280} · 250天快照 {data?.quality.history_sessions || 0}/{data?.quality.required_sessions || 250} · 数据源命中 {Object.entries(data?.quality.source_hits || {}).map(([key, value]) => `${key} ${value}`).join(" · ") || "—"} · 最近进度 {data?.quality.updated_at?.replace("T", " ") || "—"}</p>
    <Disclaimer />
  </div>;
}
