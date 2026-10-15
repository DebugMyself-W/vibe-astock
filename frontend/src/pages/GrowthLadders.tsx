import { useEffect, useMemo, useState } from "react";
import {
  CalendarDays, Check, ChevronDown, ChevronLeft, ChevronRight, ChevronUp,
  Loader2, RefreshCw, Share2,
} from "lucide-react";
import { StockModal } from "@/components/LadderExplorer";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { api, type GrowthLadderData, type GrowthLadderStock } from "@/lib/api";
import { cn } from "@/lib/utils";

const INDUSTRY_TONES = [
  "border-rose-400/40 bg-rose-400/12 text-rose-300",
  "border-orange-400/40 bg-orange-400/12 text-orange-300",
  "border-amber-400/40 bg-amber-400/12 text-amber-300",
  "border-pink-400/40 bg-pink-400/12 text-pink-300",
  "border-violet-400/40 bg-violet-400/12 text-violet-300",
  "border-fuchsia-400/40 bg-fuchsia-400/12 text-fuchsia-300",
  "border-sky-400/40 bg-sky-400/12 text-sky-300",
  "border-cyan-400/40 bg-cyan-400/12 text-cyan-300",
  "border-lime-400/40 bg-lime-400/12 text-lime-300",
  "border-emerald-400/40 bg-emerald-400/12 text-emerald-300",
];

const week = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

function compactDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "最新";
  const day = new Date(`${value}T00:00:00+08:00`);
  return `${value.slice(5, 7)}.${value.slice(8, 10)} ${week[day.getDay()]}`;
}

function clock(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "" : date.toLocaleTimeString("zh-CN", { hour12: false });
}

function percent(value: number, compact = false) {
  const digits = compact && Math.abs(value) >= 10 ? 0 : 1;
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}%`;
}

function LadderCard({ stock, tone, open }: { stock: GrowthLadderStock; tone: string; open: () => void }) {
  return <button
    type="button"
    title={stock.eliminated ? `${stock.name}，昨天连阳 ${stock.level} 天，今天断阳` : `${stock.name}，连阳 ${stock.level} 天`}
    aria-label={`查看${stock.name}行情`}
    onClick={open}
    className={cn("relative flex h-16 min-w-0 flex-col items-center justify-start overflow-hidden rounded-md border border-border/70 bg-card/65 px-1 pb-1 pt-4 text-center shadow-sm transition active:scale-[.97] md:h-[78px] md:rounded-lg md:px-1.5 md:pb-1.5 md:pt-[18px]", stock.eliminated && "bg-muted/25 opacity-70")}
  >
    <div className="absolute left-0.5 top-0.5 max-w-[calc(100%-28px)] truncate rounded-sm bg-muted/70 px-1 text-[7px] font-semibold md:hidden">
      <span className={stock.pct_chg >= 0 ? "text-danger" : "text-success"}>{percent(stock.pct_chg, true)}</span>
    </div>
    {(stock.eliminated || stock.stage_high) && <span className={cn("absolute right-0.5 top-0.5 rounded-sm px-1 py-px text-[7px] font-bold", stock.eliminated ? "bg-muted text-muted-foreground" : "bg-amber-700 text-amber-100")}>{stock.eliminated ? "断阳" : "新高"}</span>}
    <div className="hidden w-full min-w-0 items-center justify-center gap-1 md:flex"><span className="truncate text-[11px] font-bold">{stock.name}</span><span className={cn("shrink-0 font-mono text-[9px] font-semibold", stock.pct_chg >= 0 ? "text-danger" : "text-success")}>{percent(stock.pct_chg)}</span></div>
    <span className="w-full truncate px-0.5 text-[11px] font-bold md:hidden">{stock.name}</span>
    <span className={cn("mt-1 max-w-[92%] truncate rounded-sm border px-1 text-[8px] leading-[13px] md:mt-2 md:text-[9px]", tone)}>{stock.industry || "未知"}</span>
    {!stock.eliminated && <span className={cn("absolute inset-x-0 bottom-0 h-0.5 border-b-2", tone.split(" ")[0])} />}
  </button>;
}

export function GrowthLadders() {
  const [data, setData] = useState<GrowthLadderData | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [selectedIndustries, setSelectedIndustries] = useState<string[]>([]);
  const [showBroken, setShowBroken] = useState(true);
  const [showMore, setShowMore] = useState(false);
  const [showAllIndustries, setShowAllIndustries] = useState(false);
  const [dateSheet, setDateSheet] = useState(false);
  const [selectedStock, setSelectedStock] = useState<GrowthLadderStock | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [shareState, setShareState] = useState("");

  useEffect(() => {
    let active = true;
    setLoading(true); setError("");
    api.growthLadderYang(selectedDate || undefined).then((next) => {
      if (!active) return;
      setData(next);
      if (!next.available && next.coverage !== "building") setError(next.note || "连阳天梯暂不可用");
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "天梯加载失败，请稍后重试"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [selectedDate, refresh]);

  useEffect(() => {
    if (data?.coverage !== "building") return;
    let active = true;
    const timer = window.setInterval(() => {
      api.growthLadderStatus().then((sync) => {
        if (!active) return;
        setData((current) => current ? { ...current, sync, available_dates: sync.available_dates || current.available_dates } : current);
        if (sync.state === "ready" || sync.state === "failed") setRefresh((value) => value + 1);
      }).catch(() => undefined);
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [data?.coverage]);

  useEffect(() => {
    if (!data?.live) return;
    let active = true;
    const timer = window.setInterval(() => {
      if (document.visibilityState !== "visible") return;
      api.growthLadderYang(selectedDate || undefined).then((next) => {
        if (!active) return;
        setData(next);
        setError(next.available ? "" : (next.note || "盘中天梯暂不可用"));
      }).catch(() => undefined);
    }, 15_000);
    return () => { active = false; window.clearInterval(timer); };
  }, [data?.live, selectedDate]);

  const counts = useMemo(() => {
    const map = new Map<string, number>();
    data?.levels.forEach((level) => level.items.forEach((stock) => {
      if (showBroken || !stock.eliminated) {
        map.set(stock.industry || "未知", (map.get(stock.industry || "未知") || 0) + 1);
      }
    }));
    return [...map.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"));
  }, [data, showBroken]);
  const colorByIndustry = useMemo(() => Object.fromEntries(counts.map(([name], index) => [name, INDUSTRY_TONES[index % INDUSTRY_TONES.length]])), [counts]);
  const visibleLevels = useMemo(() => (data?.levels || []).map((level) => {
    const items = level.items.filter((stock) => (!selectedIndustries.length || selectedIndustries.includes(stock.industry || "未知")) && (showBroken || !stock.eliminated));
    return { ...level, active: items.filter((stock) => !stock.eliminated), broken: items.filter((stock) => stock.eliminated) };
  }).filter((level) => level.active.length || level.broken.length), [data, selectedIndustries, showBroken]);
  const dates = [...(data?.available_dates || [])].sort();
  const currentDate = selectedDate || data?.date || dates[dates.length - 1] || "";
  const dateIndex = dates.indexOf(currentDate);
  const previous = dateIndex > 0 ? dates[dateIndex - 1] : "";
  const next = dateIndex >= 0 && dateIndex < dates.length - 1 ? dates[dateIndex + 1] : "";
  const shownIndustries = showAllIndustries ? counts : counts.slice(0, 12);
  const toggleIndustry = (name: string) => setSelectedIndustries((items) => items.includes(name) ? items.filter((item) => item !== name) : [...items, name]);
  const share = async () => {
    try { await navigator.clipboard.writeText(window.location.href); setShareState("链接已复制"); }
    catch { setShareState("复制失败"); }
    window.setTimeout(() => setShareState(""), 1800);
  };
  const reload = async () => {
    if (data?.coverage === "full") {
      setRefresh((value) => value + 1);
      return;
    }
    setLoading(true); setError("");
    try { await api.syncGrowthLadder(selectedDate || undefined); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "同步启动失败"); }
    finally { setLoading(false); setRefresh((value) => value + 1); }
  };
  const phaseNames: Record<string, string> = { queued: "排队", universe: "核对全市场名单", history: "回补日线", verify: "验证完整性", live: "盘中实时", live_quotes: "实时行情覆盖检查", complete: "已完成", failed: "同步失败", database: "数据库检查" };
  const sync = data?.sync;
  const building = data?.coverage === "building";
  const sourceSummary = Object.entries(data?.quality?.source_hits || {}).map(([name, count]) => `${name} ${count}`).join(" · ");
  const coverageText = data?.quality?.universe_count
    ? `${data.quality.covered_count}/${data.quality.universe_count}（${Math.round(data.quality.covered_count / data.quality.universe_count * 1000) / 10}%）`
    : "—";

  const status = <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
    <span className={cn("inline-flex items-center gap-1.5 font-semibold", data?.live && "text-danger", building && "text-warning")}><i className={cn("h-1.5 w-1.5 rounded-full not-italic", data?.live ? "bg-danger" : building ? "animate-pulse bg-warning" : "bg-muted-foreground/70")} />{building ? "全市场构建中" : data?.live ? "盘中实时" : "收盘数据"}</span>
    {building && sync && <span>{phaseNames[sync.phase] || sync.phase} {sync.completed}/{sync.total || "—"} · {sync.percent || 0}%</span>}
    {data?.updated_at && <span>更新于 {clock(data.updated_at)}{data.live ? "，每 15 秒刷新" : ""}</span>}
    {!data && loading && <span>正在排列天梯…</span>}
  </div>;

  return <div className="mx-auto max-w-[1320px]">
    <section className="glass overflow-hidden rounded-xl">
      <div className="hidden border-b border-border/70 px-5 py-4 md:block">
        <div className="flex flex-wrap items-center gap-4">
          <h1 className="whitespace-nowrap text-base font-bold">连阳天梯</h1>
          <label className="inline-flex items-center gap-2 text-xs"><input type="checkbox" checked={showBroken} onChange={(event) => setShowBroken(event.target.checked)} className="accent-primary" />显示断阳</label>
          <div className="min-w-[180px] flex-1">{status}</div>
          <div className="flex items-center gap-1">
            <button type="button" aria-label="上一个交易日" disabled={!previous || loading} onClick={() => setSelectedDate(previous)} className="rounded-lg border border-border p-2 disabled:opacity-35"><ChevronLeft className="h-4 w-4" /></button>
            <label className="relative"><CalendarDays className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" /><select aria-label="天梯交易日" value={dates.includes(currentDate) ? currentDate : ""} onChange={(event) => setSelectedDate(event.target.value)} className="h-9 min-w-[142px] rounded-lg border border-border bg-background/45 pl-8 pr-2 text-xs">{!dates.includes(currentDate) && <option value="" disabled>{building ? `${currentDate} 构建中` : "选择完整日期"}</option>}{[...dates].reverse().map((date) => <option key={date} value={date}>{date}</option>)}</select></label>
            <button type="button" aria-label="下一个交易日" disabled={!next || loading} onClick={() => setSelectedDate(next)} className="rounded-lg border border-border p-2 disabled:opacity-35"><ChevronRight className="h-4 w-4" /></button>
            <button type="button" aria-label="刷新连阳天梯" disabled={loading} onClick={() => void reload()} className="ml-1 rounded-lg border border-border p-2 text-muted-foreground disabled:opacity-40"><RefreshCw className={cn("h-4 w-4", (loading || building) && "animate-spin")} /></button>
          </div>
        </div>
        {data?.rule && <p className="mt-2 text-xs leading-6 text-muted-foreground">{data.rule}</p>}
      </div>

      <div className="sticky top-0 z-20 border-b border-border/70 bg-card/95 px-2 py-2.5 shadow-sm backdrop-blur md:hidden">
        <div className="flex items-center justify-between gap-2">
          <div className="flex min-w-0 items-center gap-1.5"><i className="h-3.5 w-1 rounded bg-danger" /><h1 className="whitespace-nowrap text-base font-bold">连阳天梯</h1>{selectedIndustries.length > 0 && <button type="button" onClick={() => setSelectedIndustries([])} className="ml-1 rounded border border-danger/40 bg-danger/10 px-1.5 py-0.5 text-[10px] text-danger">复位</button>}</div>
          <div className="flex gap-1"><button type="button" onClick={() => setDateSheet(true)} className="flex h-8 min-w-[104px] items-center justify-center gap-1 rounded-lg border border-border bg-muted/30 px-2 text-[11px]">{compactDate(currentDate)} <ChevronDown className="h-3 w-3" /></button><button type="button" aria-expanded={showMore} onClick={() => setShowMore((value) => !value)} className="flex h-8 min-w-14 items-center justify-center gap-1 rounded-lg border border-border bg-muted/30 px-2 text-[11px]">更多 {showMore ? <ChevronUp className="h-3 w-3" /> : <ChevronDown className="h-3 w-3" />}</button></div>
        </div>
        <div className="mt-2">{status}</div>
        {showMore && <div className="mt-2 border-t border-dashed border-border pt-2"><div className="flex items-center justify-between text-xs"><label className="inline-flex items-center gap-2"><input type="checkbox" checked={showBroken} onChange={(event) => setShowBroken(event.target.checked)} className="accent-primary" />显示断阳</label><button type="button" onClick={() => void share()} className="inline-flex items-center gap-1 rounded px-2 py-1"><Share2 className="h-3.5 w-3.5" />分享</button></div>{data?.rule && <p className="mt-2 text-xs leading-5 text-muted-foreground">{data.rule}</p>}</div>}
      </div>

      {!!counts.length && <div className="flex gap-1.5 overflow-x-auto border-b border-border/60 px-3 py-2 [scrollbar-width:none] md:flex-wrap md:px-5">
        <button type="button" aria-pressed={!selectedIndustries.length} onClick={() => setSelectedIndustries([])} className={cn("shrink-0 rounded border px-2 py-0.5 text-[10px]", !selectedIndustries.length ? "border-primary bg-primary text-primary-foreground" : "border-border bg-muted/25 text-muted-foreground")}>全部行业</button>
        {shownIndustries.map(([name, count]) => <button key={name} type="button" aria-pressed={selectedIndustries.includes(name)} onClick={() => toggleIndustry(name)} className={cn("shrink-0 rounded border px-2 py-0.5 text-[10px]", selectedIndustries.includes(name) ? "border-primary bg-primary text-primary-foreground" : colorByIndustry[name])}>{name} {count}</button>)}
        {counts.length > 12 && <button type="button" aria-expanded={showAllIndustries} onClick={() => setShowAllIndustries((value) => !value)} className="shrink-0 rounded border border-border px-2 py-0.5 text-[10px] text-muted-foreground">{showAllIndustries ? "收起" : `更多 (${counts.length - 12})`}</button>}
      </div>}

      <div className={cn("space-y-2 px-1 py-3 transition-opacity md:px-4", loading && data && "opacity-50")} aria-busy={loading || undefined}>
        {building ? <div role="status" className="mx-auto max-w-lg py-14 text-center"><Loader2 className="mx-auto h-6 w-6 animate-spin text-primary" /><p className="mt-4 text-sm font-semibold">正在构建完整全市场天梯</p><p className="mt-2 text-xs text-muted-foreground">{phaseNames[sync?.phase || ""] || sync?.phase || "准备数据"} · {sync?.completed || 0}/{sync?.total || "—"} · {sync?.percent || 0}%</p><div className="mx-auto mt-3 h-1.5 max-w-sm overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-primary transition-all" style={{ width: `${Math.max(0, Math.min(100, sync?.percent || 0))}%` }} /></div><p className="mt-4 text-xs leading-5 text-muted-foreground">完整性验证通过前不会展示局部或近似股票。</p></div>
          : error && !data?.levels.length ? <div role="alert" className="py-14 text-center"><p className="text-sm text-danger">{error}</p><button type="button" onClick={() => void reload()} className="mt-3 rounded-lg border border-danger/40 px-3 py-1.5 text-xs text-danger">重试同步</button></div>
          : !data && loading ? <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />正在排列天梯…</div>
          : visibleLevels.length ? visibleLevels.map((level) => <div key={level.level} className="flex items-stretch">
            <div className="mr-1 flex w-[22px] shrink-0 flex-col items-center pt-0.5 md:mr-2 md:w-10"><span className="z-10 grid h-[18px] w-[18px] place-items-center rounded bg-danger text-[10px] font-black text-white md:h-8 md:w-8 md:text-xs">{level.level}<span className="hidden md:inline">天</span></span><span className="mt-[-4px] w-px flex-1 border-l border-dashed border-border" /></div>
            <div className="min-w-0 flex-1 space-y-1.5">
              {!!level.active.length && <section><div className="mb-1 flex items-center gap-1 text-[9px] font-bold text-danger"><i className="h-1 w-1 rounded-full bg-danger" />连阳股 {level.active.length}</div><div className="grid grid-cols-5 gap-1.5 md:grid-cols-[repeat(auto-fill,minmax(110px,1fr))] md:gap-2">{level.active.map((stock) => <LadderCard key={`${stock.code}-on`} stock={stock} tone={colorByIndustry[stock.industry] || INDUSTRY_TONES[0]} open={() => setSelectedStock(stock)} />)}</div>{level.overflow > 0 && <p className="mt-1.5 text-[11px] text-muted-foreground">另有 {level.overflow} 只未显示</p>}</section>}
              {!!level.broken.length && <section className="rounded-lg border border-border/70 bg-muted/15 p-1.5"><div className="mb-1 flex items-center gap-1 text-[9px] font-bold text-muted-foreground"><i className="h-1 w-1 rounded-full bg-muted-foreground" />断阳 {level.broken.length}</div><div className="grid grid-cols-5 gap-1.5 md:grid-cols-[repeat(auto-fill,minmax(110px,1fr))] md:gap-2">{level.broken.map((stock) => <LadderCard key={`${stock.code}-off`} stock={stock} tone={colorByIndustry[stock.industry] || INDUSTRY_TONES[0]} open={() => setSelectedStock(stock)} />)}</div></section>}
            </div>
          </div>) : <div className="py-16 text-center text-sm text-muted-foreground">{selectedIndustries.length ? "没有符合筛选条件的股票" : "这一天没有连阳 3 天以上的股票"}</div>}
        <p className="pb-5 pt-2 text-center text-[10px] text-muted-foreground">点股票卡片查看行情</p>
      </div>
    </section>

    {data?.note && <p className={cn("mt-3 rounded-lg border px-3 py-2 text-xs leading-5", data.coverage === "approximate" ? "border-warning/30 bg-warning/5 text-warning" : "border-border text-muted-foreground")}>{data.note}</p>}
    <p className="mt-3 text-[11px] leading-5 text-muted-foreground">覆盖率：{coverageText} · 停牌 {data?.quality?.suspended_count || 0} · 算法 {data?.quality?.algorithm_version || "—"}<br />数据源命中：{sourceSummary || "—"} · 最近更新：{data?.updated_at ? new Date(data.updated_at).toLocaleString("zh-CN", { hour12: false }) : "—"} · 行业仅用于客观筛选，天梯不构成股票推荐。</p>
    <Disclaimer />

    {dateSheet && <div className="fixed inset-0 z-50 flex items-end justify-center bg-black/55" onClick={() => setDateSheet(false)}><div className="max-h-[60vh] w-full overflow-hidden rounded-t-2xl border border-border bg-card" onClick={(event) => event.stopPropagation()}><div className="flex items-center justify-between border-b border-border px-4 py-4"><h2 className="font-semibold">选择交易日</h2><button type="button" onClick={() => setDateSheet(false)} className="text-sm text-muted-foreground">关闭</button></div><div className="max-h-[300px] overflow-y-auto">{[...dates].reverse().map((date) => <button key={date} type="button" onClick={() => { setSelectedDate(date); setDateSheet(false); }} className={cn("flex w-full items-center justify-between border-b border-border/50 px-4 py-3.5 text-sm", date === currentDate && "bg-danger/10 font-semibold text-danger")}><span>{compactDate(date)}</span>{date === currentDate && <Check className="h-4 w-4" />}</button>)}</div></div></div>}
    {selectedStock && <StockModal stock={selectedStock} quoteDate={currentDate} initialChartTab="daily" onClose={() => setSelectedStock(null)} />}
    {shareState && <div role="status" className="fixed bottom-6 left-1/2 z-[70] -translate-x-1/2 rounded-full bg-foreground px-4 py-2 text-xs text-background shadow-lg">{shareState}</div>}
  </div>;
}
