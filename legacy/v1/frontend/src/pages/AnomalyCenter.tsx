import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  AlertTriangle, Calculator, ChevronLeft, ChevronRight, ExternalLink, Info,
  Loader2, RefreshCw, Search, ShieldAlert, X,
} from "lucide-react";
import { StockModal } from "@/components/LadderExplorer";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { GlassCard } from "@/components/ui/GlassCard";
import { PageHeader } from "@/components/ui/PageHeader";
import { Dialog } from "@/components/workspace/Dialog";
import {
  api, type AnomalyCalculation, type AnomalyMonitor, type AnomalyPrediction,
  type AnomalyPredictions, type AnomalySearchItem, type AnomalySync,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type Tab = "prediction" | "monitor" | "calculator";
type MonitorType = "all" | "risk_warning" | "severe_abnormal";

const TABS: Array<{ id: Tab; label: string }> = [
  { id: "prediction", label: "异动预测" },
  { id: "monitor", label: "监控池" },
  { id: "calculator", label: "单股计算" },
];

function pct(value?: number | null, digits = 2) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function pctClass(value?: number | null) {
  return value == null ? "text-muted-foreground" : value >= 0 ? "text-danger" : "text-success";
}

function marketLabel(value: string) {
  return value === "SH" ? "沪市" : value === "SZ" ? "深市" : value === "BJ" ? "北交所" : value;
}

function CoverageBar({ data, retrying, sync, retry }: { data: AnomalyPredictions | null; retrying: boolean; sync: AnomalySync | null; retry: () => void }) {
  if (!data) return null;
  const covered = data.quality.history_covered;
  const total = data.quality.universe_count;
  const ratio = total ? (covered / total) * 100 : 0;
  const official = data.quality.prediction_source === "eastmoney_price_anomaly";
  return <div className={cn(
    "mb-4 flex flex-wrap items-center gap-x-5 gap-y-2 rounded-xl border px-4 py-3 text-xs",
    data.coverage === "full" ? "border-success/25 bg-success/8" : "border-warning/35 bg-warning/10",
  )} role="status">
    <span className="font-semibold">{official ? "公开预测源已更新" : data.coverage === "full" ? "本地解释覆盖完成" : `本地解释覆盖 ${ratio.toFixed(1)}%`}</span>
    {official && <span className="text-muted-foreground">预测 {data.quality.prediction_rows ?? 0} 条 · 源日期 {data.quality.prediction_source_date || "—"}</span>}
    <span className="text-muted-foreground">本地日线 {covered}/{total || "—"}</span>
    <span className="text-muted-foreground">本地缺失 {data.quality.missing_count} 只</span>
    {!!data.quality.ineligible_count && <span className="text-muted-foreground">规则不适用 {data.quality.ineligible_count} 只</span>}
    <span className="text-muted-foreground">基线 {data.trade_date || "—"}</span>
    <span className="text-muted-foreground">更新 {data.updated_at?.replace("T", " ") || "—"}</span>
    <span className="min-w-0 flex-1 text-muted-foreground">{retrying && sync ? `${sync.phase} · ${sync.completed}/${sync.total || "—"}` : data.note}</span>
    {data.coverage !== "full" && <button onClick={retry} disabled={retrying} className="inline-flex items-center gap-1 rounded-lg border border-warning/40 bg-background/60 px-2.5 py-1.5 font-semibold text-warning disabled:opacity-60">
      <RefreshCw className={cn("h-3.5 w-3.5", retrying && "animate-spin")} />{retrying ? "正在补齐" : "重试缺失数据"}
    </button>}
  </div>;
}

function PredictionTable({ title, hint, items, open }: {
  title: string; hint: string; items: AnomalyPrediction[]; open: (row: AnomalyPrediction) => void;
}) {
  return <GlassCard className="overflow-hidden p-0">
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/70 px-4 py-3.5">
      <div className="flex items-center gap-2"><h2 className="font-bold">{title}</h2><span className="rounded-full bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">{items.length}</span></div>
      <p className="text-[11px] text-muted-foreground">{hint}</p>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[860px] text-left text-xs">
        <thead className="bg-muted/35 text-muted-foreground"><tr>
          <th className="sticky left-0 z-10 bg-muted px-4 py-3">证券</th><th className="px-3 py-3">当前涨跌</th>
          <th className="px-3 py-3">偏离值</th><th className="px-3 py-3">触发条件</th>
          <th className="px-3 py-3">状态</th><th className="px-3 py-3">规则</th>
        </tr></thead>
        <tbody className="divide-y divide-border/55">
          {items.map((row) => <tr key={`${row.code}-${row.rule}`} onClick={() => open(row)} className="cursor-pointer transition hover:bg-primary/5">
            <td className="sticky left-0 bg-card px-4 py-3"><b className="block text-sm">{row.name}</b><span className="font-mono text-[10px] text-muted-foreground">{row.code} · {marketLabel(row.market)}</span></td>
            <td className={cn("px-3 py-3 font-mono font-semibold", pctClass(row.current_pct))}>{pct(row.current_pct)}</td>
            <td className={cn("px-3 py-3 font-mono font-semibold", pctClass(row.deviation))}>{pct(row.deviation)}</td>
            <td className="px-3 py-3">{row.status === "已触发" ? <span className="font-semibold text-danger">已达到</span> : <><span className={cn("font-mono font-semibold", pctClass(row.required_pct))}>{pct(row.required_pct)}</span><small className="ml-1 text-muted-foreground">可触发</small></>}</td>
            <td className="px-3 py-3"><span className={cn("rounded-full px-2 py-1 font-semibold", row.status === "已触发" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning")}>{row.status}</span></td>
            <td className="max-w-[260px] px-3 py-3"><span className="block truncate" title={row.rule}>{row.rule}</span><small className="text-muted-foreground">{row.progress_days ? `当前窗口第 ${row.progress_days} 日 · ` : `${row.window_days} 个交易日 · `}对比 {row.benchmark}</small></td>
          </tr>)}
          {!items.length && <tr><td colSpan={6} className="px-4 py-12 text-center text-muted-foreground">当前覆盖范围内暂无符合条件的标的</td></tr>}
        </tbody>
      </table>
    </div>
  </GlassCard>;
}

function PredictionPanel({ reloadToken, onRefreshDone }: { reloadToken: number; onRefreshDone: () => void }) {
  const [data, setData] = useState<AnomalyPredictions | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retrying, setRetrying] = useState(false);
  const [sync, setSync] = useState<AnomalySync | null>(null);
  const [pollToken, setPollToken] = useState(0);
  const [selected, setSelected] = useState<AnomalyPrediction | null>(null);
  useEffect(() => {
    let active = true; setLoading(true); setError("");
    api.anomalyPredictions().then((value) => { if (active) setData(value); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "异动预测加载失败"); })
      .finally(() => { if (active) { setLoading(false); if (reloadToken > 0) onRefreshDone(); } });
    return () => { active = false; };
  }, [reloadToken, pollToken, onRefreshDone]);
  useEffect(() => {
    if (!retrying) return;
    const timer = window.setInterval(() => {
      api.anomalyStatus().then((sync) => {
        setSync(sync);
        if (sync.state === "complete" || sync.state === "failed") {
          window.clearInterval(timer); setRetrying(false); setPollToken((value) => value + 1);
          if (sync.state === "failed") setError(sync.error || "同步失败，请稍后重试");
        }
      }).catch(() => { /* 下一轮继续 */ });
    }, 1500);
    return () => window.clearInterval(timer);
  }, [retrying]);
  const retry = async () => {
    setRetrying(true); setError("");
    try {
      const sync = await api.syncAnomalyCenter(data?.trade_date || undefined);
      setSync(sync);
      if (sync.state === "complete" || sync.state === "failed") { setRetrying(false); setPollToken((value) => value + 1); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "同步启动失败"); setRetrying(false); }
  };
  if (loading && !data) return <Loading label="读取全市场异动基线…" />;
  return <div className="space-y-4">
    {error && <ErrorBox text={error} />}
    <CoverageBar data={data} retrying={retrying} sync={sync} retry={retry} />
    <PredictionTable title="次日预测" hint="公开异动源给出的下一交易日可能触发条件" items={data?.groups.next_day || []} open={setSelected} />
    <PredictionTable title="当日预测" hint="公开异动源当前交易日候选，不将次日候选重复搬入" items={data?.groups.same_day || []} open={setSelected} />
    <PredictionTable title="已触发" hint="公开异动源已达到条件的记录，不代替交易所正式认定" items={data?.groups.triggered || []} open={setSelected} />
    {selected && <StockModal stock={selected} quoteDate={data?.trade_date} onClose={() => setSelected(null)} initialChartTab="minute" warning={{
      title: selected.status === "已触发" ? "严重异动条件已触发" : "接近严重异动阈值",
      detail: `${selected.rule}；累计偏离值 ${pct(selected.deviation)}；${selected.reset_date ? `窗口重置日 ${selected.reset_date}` : "请以交易所公告为准"}。`,
    }} />}
  </div>;
}

function MonitorPanel({ reloadToken, onRefreshDone }: { reloadToken: number; onRefreshDone: () => void }) {
  const [type, setType] = useState<MonitorType>("all");
  const [page, setPage] = useState(1);
  const [jumpPage, setJumpPage] = useState("1");
  const [data, setData] = useState<Awaited<ReturnType<typeof api.anomalyMonitors>> | null>(null);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<AnomalyMonitor | null>(null);
  useEffect(() => { setPage(1); setJumpPage("1"); }, [type]);
  useEffect(() => { setJumpPage(String(page)); }, [page]);
  useEffect(() => {
    let active = true; setLoading(true);
    api.anomalyMonitors(type, page).then((value) => { if (active) setData(value); }).finally(() => { if (active) { setLoading(false); if (reloadToken > 0) onRefreshDone(); } });
    return () => { active = false; };
  }, [type, page, reloadToken, onRefreshDone]);
  const filters: Array<{ id: MonitorType; label: string }> = [
    { id: "all", label: "全部" }, { id: "risk_warning", label: "风险提示" }, { id: "severe_abnormal", label: "严重异动" },
  ];
  return <GlassCard className="overflow-hidden p-0">
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
      <div className="flex flex-wrap gap-2">{filters.map((item) => <button key={item.id} onClick={() => setType(item.id)} className={cn("rounded-lg border px-3 py-1.5 text-xs font-semibold", type === item.id ? "border-primary bg-primary text-primary-foreground" : "border-border bg-background/40 text-muted-foreground hover:text-foreground")}>{item.label} <span className="ml-1 opacity-75">{data?.counts[item.id] ?? "—"}</span></button>)}</div>
      <span className={cn("text-xs", data?.coverage === "partial" ? "text-warning" : "text-muted-foreground")}>{data?.coverage === "partial" ? `部分数据 · ${data.errors.join("；")}` : `更新 ${data?.updated_at?.replace("T", " ") || "—"}`}</span>
    </div>
    <div className="overflow-x-auto">
      <table className="w-full min-w-[1080px] text-left text-xs">
        <thead className="bg-muted/35 text-muted-foreground"><tr>
          <th className="sticky left-0 z-10 min-w-40 bg-muted px-4 py-3">证券</th><th className="px-3 py-3">类型</th><th className="px-3 py-3">市场</th>
          <th className="px-3 py-3">监控期</th><th className="px-3 py-3">剩余天数</th><th className="min-w-72 px-3 py-3">异动原因</th><th className="px-3 py-3">公告日期</th>
          <th className="sticky right-0 z-10 bg-muted px-4 py-3 text-right">公告</th>
        </tr></thead>
        <tbody className="divide-y divide-border/55">
          {(data?.items || []).map((row) => <tr key={`${row.code}-${row.type}-${row.announcement_date}`} className="hover:bg-primary/5">
            <td className="sticky left-0 bg-card px-4 py-3"><button className="text-left" onClick={() => setSelected(row)}><b className="block text-sm hover:text-primary">{row.name}</b><span className="font-mono text-[10px] text-muted-foreground">{row.code}</span></button></td>
            <td className="px-3 py-3"><span className={cn("rounded-full px-2 py-1 font-semibold", row.type === "severe_abnormal" ? "bg-danger/10 text-danger" : "bg-warning/10 text-warning")}>{row.type_label}</span></td>
            <td className="px-3 py-3">{marketLabel(row.market)}</td><td className="whitespace-nowrap px-3 py-3">{row.start || "—"} 至 {row.end || "—"}</td>
            <td className="px-3 py-3">{row.remaining_days == null ? "—" : `${row.remaining_days} 天`}</td><td className="px-3 py-3 leading-5">{row.reason || row.rule || "—"}</td>
            <td className="whitespace-nowrap px-3 py-3">{row.announcement_date || "—"}</td>
            <td className="sticky right-0 bg-card px-4 py-3 text-right">{row.link ? <a href={row.link} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-semibold text-primary hover:underline">查看<ExternalLink className="h-3 w-3" /></a> : <span className="text-muted-foreground">—</span>}</td>
          </tr>)}
          {!loading && !data?.items.length && <tr><td colSpan={8} className="px-4 py-14 text-center text-muted-foreground">该分类暂无监控记录</td></tr>}
        </tbody>
      </table>
    </div>
    <div className="flex items-center justify-between border-t border-border px-4 py-3 text-xs text-muted-foreground">
      <span>共 {data?.pagination.total ?? 0} 条 · 每页 20 条</span>
      <div className="flex items-center gap-2"><button aria-label="上一页" disabled={page <= 1} onClick={() => setPage((value) => value - 1)} className="rounded-lg border border-border p-1.5 disabled:opacity-30"><ChevronLeft className="h-4 w-4" /></button><span>{page} / {data?.pagination.pages || 1}</span><button aria-label="下一页" disabled={page >= (data?.pagination.pages || 1)} onClick={() => setPage((value) => value + 1)} className="rounded-lg border border-border p-1.5 disabled:opacity-30"><ChevronRight className="h-4 w-4" /></button><label className="ml-2 inline-flex items-center gap-1">跳至<input aria-label="跳转页码" min={1} max={data?.pagination.pages || 1} type="number" value={jumpPage} onChange={(event) => setJumpPage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") setPage(Math.min(data?.pagination.pages || 1, Math.max(1, Number(jumpPage) || 1))); }} className="h-7 w-14 rounded-md border border-border bg-background px-2 text-center" />页</label><button onClick={() => setPage(Math.min(data?.pagination.pages || 1, Math.max(1, Number(jumpPage) || 1)))} className="h-7 rounded-md border border-border px-2 font-semibold hover:text-primary">确定</button></div>
    </div>
    {selected && <StockModal stock={{ code: selected.code, name: selected.name, industry: "" }} onClose={() => setSelected(null)} warning={{ title: selected.type_label, detail: `${selected.reason || selected.rule}${selected.end ? `；预计监控结束 ${selected.end}` : ""}${selected.announcement_date ? `；公告日期 ${selected.announcement_date}` : ""}` }} />}
  </GlassCard>;
}

function RulesDialog({ close }: { close: () => void }) {
  return <Dialog titleId="anomaly-rules-title" close={close} className="max-w-3xl">
    <div className="flex items-center justify-between border-b border-border px-6 py-4"><div><h2 id="anomaly-rules-title" className="text-lg font-bold">A股异动监控规则</h2><p className="mt-1 text-xs text-muted-foreground">规则引擎 anomaly-center-v4 · 公开预测口径 + 本地复合涨跌幅解释</p></div><button aria-label="关闭规则" onClick={close} className="rounded-lg p-2 hover:bg-muted"><X className="h-5 w-5" /></button></div>
    <div className="space-y-4 px-6 py-5 text-sm leading-7">
      <section><h3 className="font-bold">一般异动</h3><p className="text-muted-foreground">主板 3 日累计偏离 ±20%，ST/*ST ±12%；创业板与科创板 ±30%；北交所 ±40%。</p></section>
      <section><h3 className="font-bold">严重异动</h3><p className="text-muted-foreground">10 日累计上涨 100% 或下跌 50%，30 日累计上涨 200% 或下跌 70%；10 日内主板 4 次、创业板/科创板/北交所 3 次同向一般异动。</p></section>
      <section><h3 className="font-bold">计算口径</h3><p className="text-muted-foreground">股票区间复合涨跌幅减去对应基准指数区间复合涨跌幅。官方公布异动后的下一交易日或复牌日重置；停牌及无涨跌幅限制日不参与。</p></section>
      <div className="rounded-xl border border-warning/30 bg-warning/10 p-3 text-xs text-warning">本页是本地测算工具，不代表交易所正式认定。监控状态与处罚依据请以交易所公告为准。</div>
    </div>
    <div className="flex justify-end border-t border-border px-6 py-4"><button onClick={close} className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground">我知道了</button></div>
  </Dialog>;
}

function CalculatorPanel() {
  const [showRules, setShowRules] = useState(() => { try { return localStorage.getItem("anomaly-rules-seen-v1") !== "1"; } catch { return true; } });
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<AnomalySearchItem[]>([]);
  const [selected, setSelected] = useState<AnomalySearchItem | null>(null);
  const [result, setResult] = useState<AnomalyCalculation | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!query.trim() || selected?.code === query.trim()) { setSuggestions([]); return; }
    const timer = window.setTimeout(() => { api.anomalySearch(query).then(setSuggestions).catch(() => setSuggestions([])); }, 180);
    return () => window.clearTimeout(timer);
  }, [query, selected?.code]);
  const closeRules = () => { try { localStorage.setItem("anomaly-rules-seen-v1", "1"); } catch { /* private mode */ } setShowRules(false); };
  const calculate = async () => {
    const code = selected?.code || query.trim();
    if (!/^\d{6}$/.test(code)) { setError("请输入 6 位股票代码，或从联想结果中选择股票"); return; }
    setLoading(true); setError(""); setResult(null);
    try { setResult(await api.anomalyCalculate(code)); } catch (reason) { setError(reason instanceof Error ? reason.message : "计算失败"); }
    finally { setLoading(false); }
  };
  return <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
    <GlassCard className="h-fit">
      <div className="mb-4 flex items-center gap-2"><Calculator className="h-5 w-5 text-primary" /><h2 className="font-bold">单股异动计算</h2></div>
      <label className="text-xs font-semibold text-muted-foreground">股票代码或名称</label>
      <div className="relative mt-2">
        <Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        <input value={query} onChange={(event) => { setQuery(event.target.value); setSelected(null); }} onKeyDown={(event) => { if (event.key === "Enter") calculate(); }} placeholder="例如 600825 / 新华传媒" className="h-10 w-full rounded-xl border border-border bg-background/60 pl-9 pr-3 text-sm outline-none focus:border-primary" />
        {!!suggestions.length && <div className="absolute inset-x-0 top-12 z-20 overflow-hidden rounded-xl border border-border bg-card shadow-xl">{suggestions.map((item) => <button key={item.code} onClick={() => { setSelected(item); setQuery(item.code); setSuggestions([]); }} className="flex w-full items-center justify-between px-3 py-2.5 text-left hover:bg-muted"><span><b className="text-sm">{item.name}</b><small className="ml-2 font-mono text-muted-foreground">{item.code}</small></span><small className="text-muted-foreground">{marketLabel(item.market)}</small></button>)}</div>}
      </div>
      <button onClick={calculate} disabled={loading} className="mt-3 inline-flex h-10 w-full items-center justify-center gap-2 rounded-xl bg-primary text-sm font-bold text-primary-foreground disabled:opacity-60">{loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <Calculator className="h-4 w-4" />}开始计算</button>
      <button onClick={() => setShowRules(true)} className="mt-3 w-full text-center text-xs font-semibold text-primary hover:underline">查看 A 股异动监控规则</button>
      <p className="mt-4 text-[11px] leading-5 text-muted-foreground">本地计算不限次数。计算使用现有全市场日线与对应板块基准，不会为单股重复抓取全市场。</p>
    </GlassCard>
    <div>
      {error && <ErrorBox text={error} />}
      {!result && !error && <GlassCard className="grid min-h-80 place-items-center text-center"><div><ShieldAlert className="mx-auto h-10 w-10 text-muted-foreground/45" /><h3 className="mt-3 font-semibold">输入股票后查看全部适用规则</h3><p className="mt-1 text-xs text-muted-foreground">包含一般异动、10/30 日严重异动、触发价格与窗口重置</p></div></GlassCard>}
      {result && <CalculationResult result={result} />}
    </div>
    {showRules && <RulesDialog close={closeRules} />}
  </div>;
}

function CalculationResult({ result }: { result: AnomalyCalculation }) {
  return <div className="space-y-4">
    <GlassCard>
      <div className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-xl font-bold">{result.stock.name} <span className="font-mono text-sm text-muted-foreground">{result.stock.code}</span></h2><p className="mt-1 text-xs text-muted-foreground">{result.stock.board} · {result.stock.industry} · 基准 {result.benchmark.name}</p></div><div className="text-right"><b className={cn("font-mono text-lg", pctClass(result.stock.current_pct))}>{pct(result.stock.current_pct)}</b><p className="text-[11px] text-muted-foreground">计算日 {result.trade_date}</p></div></div>
      <div className="mt-4 rounded-xl bg-muted/30 p-3 text-sm">一般异动阈值 <b>±{result.general_rule.threshold}%</b> · 10 日内同向异动 <b>{result.general_rule.same_direction_count} 次</b>进入严重异动观察</div>
    </GlassCard>
    <GlassCard className="overflow-hidden p-0"><div className="border-b border-border px-4 py-3 font-bold">严重异动测算</div><div className="overflow-x-auto"><table className="w-full min-w-[680px] text-left text-xs"><thead className="bg-muted/35 text-muted-foreground"><tr><th className="px-4 py-3">规则</th><th className="px-3 py-3">累计偏离</th><th className="px-3 py-3">当日所需</th><th className="px-3 py-3">次日所需</th><th className="px-3 py-3">状态</th></tr></thead><tbody className="divide-y divide-border/55">{result.severe_rules.map((rule) => <tr key={`${rule.window_days}-${rule.threshold}`}><td className="px-4 py-3 font-semibold">{rule.rule}</td><td className={cn("px-3 py-3 font-mono", pctClass(rule.deviation))}>{pct(rule.deviation)}</td><td className="px-3 py-3 font-mono">{pct(rule.required_today)}</td><td className="px-3 py-3 font-mono">{pct(rule.required_next)}</td><td className="px-3 py-3"><span className={cn("rounded-full px-2 py-1 font-semibold", rule.triggered ? "bg-danger/10 text-danger" : "bg-muted text-muted-foreground")}>{rule.triggered ? "已触发" : "未触发"}</span></td></tr>)}</tbody></table></div></GlassCard>
    <div className="rounded-xl border border-warning/25 bg-warning/8 px-4 py-3 text-xs text-warning">{result.disclaimer}</div>
  </div>;
}

function Loading({ label }: { label: string }) { return <div role="status" className="grid min-h-72 place-items-center text-sm text-muted-foreground"><span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin text-primary" />{label}</span></div>; }
function ErrorBox({ text }: { text: string }) { return <div role="alert" className="mb-4 flex items-start gap-2 rounded-xl border border-danger/25 bg-danger/10 px-4 py-3 text-xs text-danger"><AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />{text}</div>; }

export function AnomalyCenter() {
  const [params, setParams] = useSearchParams();
  const rawTab = params.get("tab") as Tab | null;
  const tab: Tab = TABS.some((item) => item.id === rawTab) ? rawTab! : "prediction";
  const [reloadToken, setReloadToken] = useState(0);
  const [refreshing, setRefreshing] = useState(false);
  const [refreshMessage, setRefreshMessage] = useState("");
  const changeTab = (next: Tab) => { const value = new URLSearchParams(params); value.set("tab", next); setParams(value, { replace: true }); };
  const finishRefresh = useCallback(() => {
    setRefreshing(false);
    setRefreshMessage(`已刷新 ${new Date().toLocaleTimeString("zh-CN", { hour12: false })}`);
  }, []);
  const refresh = () => {
    setRefreshing(true);
    setRefreshMessage("正在刷新…");
    setReloadToken((value) => value + 1);
    if (tab === "calculator") window.setTimeout(finishRefresh, 250);
  };
  return <div className="mx-auto max-w-[1500px] px-1 pb-10">
    <PageHeader title="监管异动" subtitle="交易所监控池与本地严重异动规则测算" actions={<div className="flex items-center gap-2"><span aria-live="polite" className="text-[11px] text-muted-foreground">{refreshMessage}</span><button aria-label="刷新监管异动" onClick={refresh} disabled={refreshing} className="inline-flex h-10 items-center gap-2 rounded-xl border border-border bg-card/60 px-3 text-xs font-semibold shadow-sm hover:border-primary/40 disabled:opacity-60"><RefreshCw className={cn("h-4 w-4", refreshing && "animate-spin")} />{refreshing ? "刷新中" : "刷新"}</button></div>} />
    <div className="mb-5 flex gap-1 rounded-xl border border-border bg-card/45 p-1">{TABS.map((item) => <button key={item.id} onClick={() => changeTab(item.id)} className={cn("flex-1 rounded-lg px-4 py-2.5 text-sm font-semibold transition", tab === item.id ? "bg-primary text-primary-foreground shadow" : "text-muted-foreground hover:bg-muted hover:text-foreground")}>{item.label}</button>)}</div>
    <div className="mb-4 flex items-start gap-2 rounded-xl border border-info/20 bg-info/5 px-4 py-3 text-xs leading-5 text-muted-foreground"><Info className="mt-0.5 h-4 w-4 shrink-0 text-info" />预测分组与触发条件采用公开异动源原始状态；本地全市场日线用于覆盖校验、基准解释和数据源不可用时兜底。结果不代表交易所正式认定，监控池与公告单独标注来源。</div>
    {tab === "prediction" ? <PredictionPanel reloadToken={reloadToken} onRefreshDone={finishRefresh} /> : tab === "monitor" ? <MonitorPanel reloadToken={reloadToken} onRefreshDone={finishRefresh} /> : <CalculatorPanel key={reloadToken} />}
    <Disclaimer />
  </div>;
}
