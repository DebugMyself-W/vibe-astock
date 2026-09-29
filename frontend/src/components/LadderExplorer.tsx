import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import * as echarts from "echarts";
import type { EChartsOption } from "echarts";
import { ChevronDown, ExternalLink, LineChart, Loader2, X } from "lucide-react";
import { StockConceptCard } from "@/components/StockConceptCard";
import { api, type Blocks, type HotConcept, type IndustryPeer, type IndustryPeers, type IntradayBundle, type KlineRow, type Quote } from "@/lib/api";
import { agentFetch, finite, safeArray, type LadderDetails, type LadderStock } from "@/lib/agent";
import { cn } from "@/lib/utils";
import { pctColor } from "@/lib/colors";


const LINE_COLORS = ["#ea580c", "#2563eb", "#16a34a", "#a855f7", "#0891b2", "#ca8a04"];

const industryPeersCache = new Map<string, IndustryPeers>();
const industryPeersRequests = new Map<string, Promise<IndustryPeers>>();

function industryPeersKey(stock: Pick<LadderStock, "code" | "industry">, reviewDate: string) {
  return `${reviewDate}|${stock.industry}|${stock.code}`;
}

function loadIndustryPeers(stock: Pick<LadderStock, "code" | "industry">, reviewDate: string) {
  const key = industryPeersKey(stock, reviewDate);
  const cached = industryPeersCache.get(key);
  if (cached) return Promise.resolve(cached);
  const pending = industryPeersRequests.get(key);
  if (pending) return pending;
  const request = api.industryPeers(stock.industry, stock.code, 300, reviewDate)
    .then((response) => {
      if (response.available) industryPeersCache.set(key, response);
      industryPeersRequests.delete(key);
      return response;
    })
    .catch((error) => {
      industryPeersRequests.delete(key);
      throw error;
    });
  industryPeersRequests.set(key, request);
  return request;
}

function industryPreview(stock: LadderStock, reviewDate: string): IndustryPeers {
  return {
    available: true,
    preview: true,
    board: { code: "", name: stock.industry || "同行业", basis: "industry" },
    requested_date: reviewDate,
    quote_date: reviewDate,
    focus_code: stock.code,
    total: 1,
    members: [{
      code: stock.code,
      name: stock.name,
      price: stock.price ?? null,
      pct: stock.pct ?? null,
      amount: stock.amount ?? null,
      mcap: null,
      float_cap: stock.float_cap ?? null,
      industry: stock.industry,
      is_focus: true,
    }],
    source: "local_focus_preview",
  };
}


function signed(value?: number | null, digits = 2) {
  const n = finite(value);
  return n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(digits)}%`;
}


function yi(value?: number | null) {
  const n = finite(value);
  if (n == null) return "—";
  return `${(n / 1e8).toFixed(n >= 1e10 ? 1 : 2)}亿`;
}


function Chart({ option, className, onSeriesClick }: { option: EChartsOption; className: string; onSeriesClick?: (seriesId: string) => void }) {
  const root = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!root.current) return;
    const chart = echarts.init(root.current);
    chart.setOption(option, true);
    if (onSeriesClick) {
      chart.on("click", (params) => {
        const seriesId = String(params.seriesId || "");
        if (params.componentType === "series" && seriesId) onSeriesClick(seriesId);
      });
    }
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(root.current);
    return () => { observer.disconnect(); chart.dispose(); };
  }, [option, onSeriesClick]);
  return <div ref={root} className={className} />;
}


function loadingBlock(text: string) {
  return <div className="flex min-h-28 items-center justify-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />{text}</div>;
}


function intradayOption(bundle: IntradayBundle | null, names: Record<string, string>, mainCode: string): EChartsOption {
  const lines = bundle ? Object.values(bundle.series).filter((line) => line.points.length) : [];
  const times = Array.from(new Set(lines.flatMap((line) => line.points.map((point) => point.time)))).sort();
  const byCode = Object.fromEntries(lines.map((line) => [line.code, new Map(line.points.map((point) => [point.time, point.pct]))]));
  return {
    animation: false,
    color: LINE_COLORS,
    tooltip: { trigger: "axis", valueFormatter: (value) => value == null ? "—" : `${Number(value).toFixed(2)}%` },
    legend: { type: "scroll", top: 0, textStyle: { fontSize: 11 } },
    grid: { top: 34, left: 46, right: 18, bottom: 28 },
    xAxis: { type: "category", data: times, boundaryGap: false, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: "{value}%", fontSize: 10 }, splitLine: { lineStyle: { type: "dashed", opacity: .5 } } },
    series: lines.map((line) => ({
      id: line.code,
      name: names[line.code] || line.code,
      type: "line",
      showSymbol: false,
      connectNulls: false,
      z: line.code === mainCode ? 10 : 2,
      lineStyle: { width: line.code === mainCode ? 3.2 : 1.35, opacity: line.code === mainCode ? 1 : .72 },
      areaStyle: line.code === mainCode ? { opacity: .045 } : undefined,
      emphasis: { focus: "series", lineStyle: { width: 3.5, opacity: 1 } },
      data: times.map((time) => byCode[line.code].get(time) ?? null),
    })),
  };
}


function stockMinuteOption(line: IntradayBundle | null, code: string): EChartsOption {
  const points = line?.series?.[code]?.points || [];
  let cumulativeAmount = 0;
  let cumulativeVolume = 0;
  let currentDate = "";
  const average = points.map((point) => {
    if (point.date && point.date !== currentDate) {
      currentDate = point.date;
      cumulativeAmount = 0;
      cumulativeVolume = 0;
    }
    cumulativeAmount += point.price * point.volume;
    cumulativeVolume += point.volume;
    return cumulativeVolume > 0 ? Number((cumulativeAmount / cumulativeVolume).toFixed(3)) : point.price;
  });
  return {
    animation: false,
    tooltip: { trigger: "axis", axisPointer: { type: "cross" } },
    grid: [{ left: 48, right: 20, top: 18, height: "62%" }, { left: 48, right: 20, top: "77%", height: "14%" }],
    xAxis: [
      { type: "category", data: points.map((point) => point.label || point.time), boundaryGap: false, axisLabel: { show: false } },
      { type: "category", data: points.map((point) => point.label || point.time), gridIndex: 1, axisLabel: { fontSize: 10, formatter: (value: string) => value.replace(" ", "\n") }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: "dashed", opacity: .5 } } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      { name: "分时", type: "line", data: points.map((point) => point.price), showSymbol: false, lineStyle: { color: "#2563eb", width: 1.8 }, areaStyle: { color: "rgba(37,99,235,.06)" }, markLine: line?.days === 1 && line.series?.[code]?.previous_close ? { symbol: "none", label: { formatter: "昨收 {c}", fontSize: 10 }, lineStyle: { color: "#94a3b8", type: "dashed" }, data: [{ yAxis: line.series[code].previous_close }] } : undefined },
      { name: "均价", type: "line", data: average, showSymbol: false, lineStyle: { color: "#d97706", width: 1.2 }, symbol: "none" },
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: points.map((point) => point.volume), itemStyle: { color: "rgba(234,88,12,.65)" } },
    ],
  };
}


function dailyOption(rows: KlineRow[]): EChartsOption {
  const normalized = rows.slice(-120);
  const closes = normalized.map((row) => Number(row.close));
  const ma = (period: number) => closes.map((_, index) => {
    if (index < period - 1) return null;
    const values = closes.slice(index - period + 1, index + 1);
    return Number((values.reduce((sum, value) => sum + value, 0) / period).toFixed(3));
  });
  const volumes = normalized.map((row) => row.volume ?? row.vol ?? 0);
  return {
    animation: false,
    tooltip: { trigger: "axis" },
    grid: [{ left: 50, right: 20, top: 18, height: "60%" }, { left: 50, right: 20, top: "73%", height: "14%" }],
    xAxis: [
      { type: "category", data: normalized.map((row) => row.datetime || row.date || ""), axisLabel: { show: false } },
      { type: "category", gridIndex: 1, data: normalized.map((row) => row.datetime || row.date || ""), axisLabel: { fontSize: 10 }, axisTick: { show: false } },
    ],
    yAxis: [
      { scale: true, axisLabel: { fontSize: 10 }, splitLine: { lineStyle: { type: "dashed", opacity: .5 } } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      { name: "K线", type: "candlestick", data: normalized.map((row) => [row.open, row.close, row.low, row.high]), itemStyle: { color: "#ef4444", color0: "#16a34a", borderColor: "#ef4444", borderColor0: "#16a34a" } },
      { name: "MA5", type: "line", data: ma(5), showSymbol: false, lineStyle: { color: "#2563eb", width: 1.2 } },
      { name: "MA10", type: "line", data: ma(10), showSymbol: false, lineStyle: { color: "#d97706", width: 1.2 } },
      { name: "MA20", type: "line", data: ma(20), showSymbol: false, lineStyle: { color: "#8b5cf6", width: 1.2 } },
      { name: "成交量", type: "bar", xAxisIndex: 1, yAxisIndex: 1, data: volumes.map((volume, index) => ({ value: volume, itemStyle: { color: normalized[index].close >= normalized[index].open ? "#ef4444" : "#16a34a" } })) },
    ],
    dataZoom: [{ type: "inside", start: 35, end: 100 }, { type: "slider", height: 14, bottom: 2, start: 35, end: 100 }],
  };
}


const miniKCache = new Map<string, KlineRow[]>();

function MiniDailyK({ code }: { code: string }) {
  const root = useRef<HTMLSpanElement>(null);
  const [rows, setRows] = useState<KlineRow[]>(() => miniKCache.get(code) || []);
  useEffect(() => {
    if (miniKCache.has(code) || !root.current) return;
    let active = true;
    const observer = new IntersectionObserver((entries) => {
      if (!entries[0]?.isIntersecting) return;
      observer.disconnect();
      api.kline(code, 4, 14).then((data) => {
        const value = safeArray<KlineRow>(data).slice(-14);
        miniKCache.set(code, value);
        if (active) setRows(value);
      }).catch(() => { miniKCache.set(code, []); });
    }, { rootMargin: "120px" });
    observer.observe(root.current);
    return () => { active = false; observer.disconnect(); };
  }, [code]);
  const bars = rows.slice(-14);
  if (!bars.length) return <span ref={root} className="block h-8 w-[82px]" />;
  const low = Math.min(...bars.map((row) => row.low));
  const high = Math.max(...bars.map((row) => row.high));
  const span = Math.max(high - low, .001);
  const y = (value: number) => 29 - ((value - low) / span) * 26;
  const width = 82 / bars.length;
  return <span ref={root} className="block h-8 w-[82px]"><svg aria-label={`${code} 近14日日K预览`} viewBox="0 0 82 32" className="h-8 w-[82px] overflow-visible">{bars.map((row, index) => {
    const red = row.close >= row.open;
    const x = index * width + width / 2;
    const top = Math.min(y(row.open), y(row.close));
    const body = Math.max(1, Math.abs(y(row.open) - y(row.close)));
    const color = red ? "#ef4444" : "#16a34a";
    return <g key={`${row.datetime || row.date || index}`}><line x1={x} x2={x} y1={y(row.high)} y2={y(row.low)} stroke={color} strokeWidth=".8" /><rect x={x - Math.max(1, width * .28)} y={top} width={Math.max(2, width * .56)} height={body} fill={color} /></g>;
  })}</svg></span>;
}


function StockModal({ stock, quoteDate, onClose }: { stock: Pick<LadderStock, "code" | "name" | "industry"> | IndustryPeer; quoteDate?: string | null; onClose: () => void }) {
  const [quote, setQuote] = useState<Quote | null>(null);
  const [minute, setMinute] = useState<IntradayBundle | null>(null);
  const [daily, setDaily] = useState<KlineRow[]>([]);
  const [blocks, setBlocks] = useState<Blocks | null>(null);
  const [hotConcepts, setHotConcepts] = useState<HotConcept[]>([]);
  const [chartTab, setChartTab] = useState<"minute" | "daily">("minute");
  const [intradayDays, setIntradayDays] = useState(1);
  const [intradayMenu, setIntradayMenu] = useState(false);
  const [minuteLoading, setMinuteLoading] = useState(true);
  const [contentTab, setContentTab] = useState<"market" | "concept">("market");
  const [selectedConcept, setSelectedConcept] = useState<string | null>(null);
  const [conceptPeers, setConceptPeers] = useState<IndustryPeers | null>(null);
  const [conceptLoading, setConceptLoading] = useState(false);
  const [conceptDetail, setConceptDetail] = useState<IndustryPeer | null>(null);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let active = true;
    setLoading(true); setBlocks(null); setHotConcepts([]); setSelectedConcept(null);
    Promise.allSettled([
      api.quote(stock.code), api.kline(stock.code), api.blocks(stock.code), api.hotConcepts(stock.code),
    ]).then((results) => {
      if (!active) return;
      if (results[0].status === "fulfilled") setQuote(results[0].value[stock.code] || null);
      if (results[1].status === "fulfilled") setDaily(safeArray<KlineRow>(results[1].value));
      if (results[2].status === "fulfilled") setBlocks(results[2].value);
      if (results[3].status === "fulfilled") setHotConcepts(safeArray<HotConcept>(results[3].value));
      setLoading(false);
    });
    return () => { active = false; };
  }, [stock.code, quoteDate]);

  useEffect(() => {
    let active = true;
    setMinuteLoading(true); setMinute(null);
    api.intraday([stock.code], quoteDate, intradayDays).then((data) => {
      if (active) setMinute(data);
    }).catch(() => { if (active) setMinute({ requested_date: quoteDate, series: {} }); })
      .finally(() => { if (active) setMinuteLoading(false); });
    return () => { active = false; };
  }, [stock.code, quoteDate, intradayDays]);

  useEffect(() => {
    if (!selectedConcept) return;
    let active = true;
    setConceptLoading(true); setConceptPeers(null);
    api.conceptPeers(selectedConcept, stock.code).then((data) => {
      if (active) setConceptPeers(data);
    }).catch(() => { if (active) setConceptPeers({ available: false, reason: "概念热门标的加载失败", members: [] }); })
      .finally(() => { if (active) setConceptLoading(false); });
    return () => { active = false; };
  }, [selectedConcept, stock.code]);

  const livePct = finite(quote?.change_pct);
  return createPortal((
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-3 backdrop-blur-[2px]" onMouseDown={(event) => { event.stopPropagation(); onClose(); }}>
      <section className="glass relative flex max-h-[92vh] w-full max-w-4xl flex-col overflow-hidden rounded-2xl border border-border bg-background shadow-2xl" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-start gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><h2 className="text-lg font-bold">{stock.name}</h2><span className="font-mono text-xs text-muted-foreground">{stock.code}</span></div>
            <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>成交额 {yi(quote?.amount_wan != null ? quote.amount_wan * 10000 : null)}</span>
              <span>流通市值 {quote?.float_mcap_yi ? `${quote.float_mcap_yi.toFixed(2)}亿` : "—"}</span>
              <span>换手率 {quote?.turnover_pct != null ? `${quote.turnover_pct.toFixed(2)}%` : "—"}</span>
              <span>行情日 {quoteDate || "最近交易日"}</span>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <div className="rounded-lg bg-muted p-1 text-xs">
              <div className="relative inline-block">
                <button className={cn("inline-flex items-center gap-1 rounded-md px-3 py-1.5", chartTab === "minute" && "bg-card font-semibold text-primary shadow-sm")} onClick={() => { setChartTab("minute"); setIntradayMenu((value) => !value); }}>{intradayDays}日分时<ChevronDown className="h-3.5 w-3.5" /></button>
                {intradayMenu && <div className="absolute right-0 top-[calc(100%+6px)] z-10 w-28 overflow-hidden rounded-xl border border-border bg-card py-1 shadow-xl">{[1, 2, 3, 4, 5].map((days) => <button key={days} onClick={() => { setIntradayDays(days); setChartTab("minute"); setIntradayMenu(false); }} className={cn("block w-full px-3 py-2 text-left hover:bg-muted", days === intradayDays && "bg-primary/10 font-semibold text-primary")}>{days}日分时</button>)}</div>}
              </div>
              <button className={cn("rounded-md px-3 py-1.5", chartTab === "daily" && "bg-card font-semibold text-primary shadow-sm")} onClick={() => setChartTab("daily")}>日K线</button>
            </div>
            <button aria-label="关闭行情卡片" onClick={onClose} className="rounded-lg p-2 text-muted-foreground hover:bg-muted hover:text-foreground"><X className="h-5 w-5" /></button>
          </div>
        </header>
        <div className="min-h-0 overflow-y-auto px-5 py-4">
          <div className="mb-3 flex gap-2 border-b border-border pb-2 text-sm">
            <button onClick={() => setContentTab("market")} className={cn("rounded-lg px-3 py-1.5", contentTab === "market" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>行情</button>
            <button onClick={() => setContentTab("concept")} className={cn("rounded-lg px-3 py-1.5", contentTab === "concept" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>概念</button>
          </div>
          {loading ? loadingBlock("加载行情数据…") : contentTab === "concept" ? (
            <div className="min-h-72 rounded-xl bg-muted/20 p-4">
              <StockConceptCard
                blocks={blocks}
                hotConcepts={hotConcepts}
                selectedConcept={selectedConcept}
                onConceptClick={setSelectedConcept}
                className="mb-0"
              />
              {selectedConcept && <section className="mt-4 overflow-hidden rounded-xl border border-border bg-card">
                <div className="flex items-center justify-between border-b border-border bg-muted/30 px-3 py-2"><span className="text-sm font-semibold">{selectedConcept} · 热门标的</span><button onClick={() => setSelectedConcept(null)} className="text-xs text-muted-foreground hover:text-foreground">收起</button></div>
                {conceptLoading ? loadingBlock("加载概念热门标的…") : !conceptPeers?.available ? <p className="p-4 text-sm text-muted-foreground">{conceptPeers?.reason || "暂无可用成分数据"}</p> : <div className="max-h-64 divide-y divide-border/60 overflow-y-auto">{safeArray<IndustryPeer>(conceptPeers.members).map((item, index) => <button key={item.code} onClick={() => setConceptDetail(item)} className="grid w-full grid-cols-[28px_minmax(0,1fr)_68px] items-center gap-2 px-3 py-2 text-left hover:bg-muted/50"><span className="text-xs text-muted-foreground">{index + 1}</span><span><b className="block truncate text-sm">{item.name}</b><small className="font-mono text-[10px] text-muted-foreground">{item.code} · {item.industry || selectedConcept}</small></span><span className={cn("text-right font-mono text-xs font-semibold", pctColor(item.pct))}>{signed(item.pct)}</span></button>)}</div>}
              </section>}
              <p className="mt-3 text-[11px] text-muted-foreground">点击概念标签即可查看该概念当日涨幅靠前的成分股。</p>
            </div>
          ) : (
            <>
              <div className="mb-1 flex items-baseline gap-3"><span className={cn("text-2xl font-bold", pctColor(livePct))}>{quote?.price ?? "—"}</span><span className={cn("font-semibold", pctColor(livePct))}>{signed(livePct)}</span></div>
              {chartTab === "minute" ? (minuteLoading ? loadingBlock(`加载 ${intradayDays} 日分时…`) : <Chart option={stockMinuteOption(minute, stock.code)} className="h-[360px] w-full" />) : <><Chart option={dailyOption(daily)} className="h-[360px] w-full" /><div className="-mt-5 flex items-center gap-3 px-2 text-[11px] text-muted-foreground"><span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-[#ef4444]" />涨</span><span><i className="mr-1 inline-block h-2 w-2 rounded-sm bg-[#16a34a]" />跌</span><span className="text-[#2563eb]">━ MA5</span><span className="text-[#d97706]">━ MA10</span><span className="text-[#8b5cf6]">━ MA20</span></div></>}
              {chartTab === "minute" && !minuteLoading && !(minute?.series?.[stock.code]?.points?.length) && <p className="-mt-48 mb-40 text-center text-sm text-muted-foreground">该行情日的分钟线不在数据源最近窗口内</p>}
            </>
          )}
        </div>
      </section>
      {conceptDetail && <StockModal stock={conceptDetail} quoteDate={conceptPeers?.quote_date || quoteDate} onClose={() => setConceptDetail(null)} />}
    </div>
  ), document.body);
}


function IndustryDrawer({ stock, reviewDate, onClose }: { stock: LadderStock; reviewDate: string; onClose: () => void }) {
  const [data, setData] = useState<IndustryPeers>(() => industryPeersCache.get(industryPeersKey(stock, reviewDate)) || industryPreview(stock, reviewDate));
  const [lines, setLines] = useState<IntradayBundle | null>(null);
  const [loading, setLoading] = useState(() => !industryPeersCache.has(industryPeersKey(stock, reviewDate)));
  const [detail, setDetail] = useState<IndustryPeer | LadderStock | null>(null);
  const [mainCode, setMainCode] = useState(stock.code);
  const rowRefs = useRef<Record<string, HTMLDivElement | null>>({});

  useEffect(() => {
    let active = true;
    const cached = industryPeersCache.get(industryPeersKey(stock, reviewDate));
    setLoading(!cached); setData(cached || industryPreview(stock, reviewDate)); setLines(null);
    setMainCode(stock.code);
    if (cached) return () => { active = false; };
    loadIndustryPeers(stock, reviewDate).then((response) => {
      if (!active) return;
      setData(response);
    }).catch(() => { if (active) setData({ available: false, reason: "同行业数据加载失败", members: [] }); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [reviewDate, stock.code, stock.industry]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = previous; };
  }, []);

  const sourceMembers = safeArray<IndustryPeer>(data?.members);
  const members: IndustryPeer[] = sourceMembers.some((item) => item.code === stock.code) ? sourceMembers : [{
    code: stock.code, name: stock.name, price: stock.price ?? null, pct: stock.pct ?? null,
    amount: stock.amount ?? null, mcap: null, float_cap: stock.float_cap ?? null,
    industry: stock.industry, is_focus: true,
  }, ...sourceMembers];
  const chartCodes = [mainCode, ...members.map((item) => item.code).filter((code) => code !== mainCode)].slice(0, 6);
  const chartKey = chartCodes.join(",");

  useEffect(() => {
    if (!data?.available || !chartKey) return;
    let active = true;
    setLines(null);
    api.intraday(chartKey.split(","), data.quote_date)
      .then((chart) => { if (active) setLines(chart); })
      .catch(() => { if (active) setLines({ requested_date: data.quote_date, series: {} }); });
    return () => { active = false; };
  }, [chartKey, data?.available, data?.quote_date]);

  useEffect(() => {
    rowRefs.current[mainCode]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [mainCode]);

  const selectMain = useCallback((code: string) => setMainCode(code), []);
  const mainStock = members.find((item) => item.code === mainCode);
  const names = Object.fromEntries(chartCodes.map((code) => {
    const item = members.find((member) => member.code === code);
    return [code, `${item?.name || code}${code === mainCode ? "（主）" : ""}`];
  }));
  const option = useMemo(() => intradayOption(lines, names, mainCode), [lines, mainCode, chartKey]); // eslint-disable-line react-hooks/exhaustive-deps

  return createPortal((
    <div className="fixed inset-0 z-50 bg-black/35" onMouseDown={onClose}>
      <aside className="ladder-drawer absolute inset-y-0 right-0 flex w-full flex-col border-l border-border bg-background shadow-2xl sm:w-[52vw] sm:min-w-[680px] sm:max-w-[920px]" onMouseDown={(event) => event.stopPropagation()}>
        <header className="flex items-center gap-3 border-b border-border px-5 py-4">
          <button aria-label="关闭同行业面板" onClick={onClose} className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"><X className="h-5 w-5" /></button>
          <div className="min-w-0 flex-1"><h2 className="truncate text-base font-bold">{data?.board?.name || stock.industry || "同行业"} · 分时叠加</h2><p className="mt-0.5 text-[11px] text-muted-foreground">主票 {mainStock?.name || stock.name} · 从 {reviewDate} 连板标的进入 · {data?.quote_date || "最近交易日"} 行情</p></div>
          <span className="text-xs text-muted-foreground">{data?.total ?? "—"} {data?.preview ? "只先显示" : data?.degraded ? "只样本" : "只"}</span>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {loading && <div role="status" className="mb-3 flex items-center gap-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />正在后台补全同行业标的…</div>}
          {!data?.available ? <div className="rounded-xl border border-warning/30 bg-warning/10 p-4 text-sm text-warning">{data?.reason || "数据不可用"}</div> : (
            <>
              {data.note && <div className={cn("mb-3 rounded-xl border p-3 text-xs leading-relaxed", data.degraded ? "border-warning/30 bg-warning/10 text-warning" : "border-border bg-muted/30 text-muted-foreground")}>{data.note}{data.classification_date && ` 分类快照：${data.classification_date}。`}</div>}
              <section className="rounded-2xl border border-border bg-card/60 p-3">
                <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-[11px] text-muted-foreground"><span>{data.preview ? "先显示主票；同行业标的后台补全" : data.degraded ? "主票 + 复盘日同行业涨停样本" : "主票 + 板块涨幅前 5（去重）"} · 相对昨收 · 同一涨跌幅坐标</span><span className="font-medium text-primary">点击曲线反选下方标的</span></div>
                {!lines ? loadingBlock("切换主票并加载分时…") : <Chart option={option} className="h-[310px] w-full" onSeriesClick={selectMain} />}
                {lines && !Object.values(lines.series).some((line) => line.points.length) && <p className="-mt-36 mb-28 text-center text-xs text-muted-foreground">该交易日分钟线暂不可用</p>}
              </section>
              <section className="mt-4 overflow-hidden rounded-2xl border border-border">
                <div className="flex items-center justify-between bg-muted/40 px-4 py-2 text-xs font-semibold"><span>{data.board?.name}{data.degraded ? "涨停样本" : "股票"}</span><span className="font-normal text-muted-foreground">按涨幅从高到低 · 点击切换主票</span></div>
                <div className="max-h-[calc(100vh-460px)] divide-y divide-border/60 overflow-y-auto">
                  {members.map((item, index) => (
                    <div key={item.code} ref={(node) => { rowRefs.current[item.code] = node; }} role="button" tabIndex={0}
                      onClick={() => selectMain(item.code)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") selectMain(item.code); }}
                      className={cn("grid w-full cursor-pointer grid-cols-[28px_minmax(0,1fr)_82px_58px_58px_44px] items-center gap-2 px-4 py-2.5 text-left transition-colors hover:bg-muted/50", item.code === mainCode && "border-l-2 border-l-primary bg-primary/5") }>
                      <span className="text-center text-xs text-muted-foreground">{index + 1}</span>
                      <span className="min-w-0"><span className="block truncate text-sm font-semibold">{item.name}{item.code === mainCode && <b className="ml-1 rounded bg-warning/20 px-1 py-0.5 text-[9px] text-warning">主</b>}</span><span className="font-mono text-[10px] text-muted-foreground">{item.code} · {item.industry || data.board?.name}</span></span>
                      <MiniDailyK code={item.code} />
                      <span className="text-right font-mono text-xs text-muted-foreground">{item.price ?? "—"}</span>
                      <span className={cn("text-right font-mono text-xs font-semibold", pctColor(item.pct))}>{signed(item.pct)}</span>
                      <button type="button" onClick={(event) => { event.stopPropagation(); setDetail(item); }} className="text-[11px] font-medium text-info hover:underline">行情</button>
                    </div>
                  ))}
                </div>
              </section>
            </>
          )}
        </div>
      </aside>
      {detail && <StockModal stock={detail} quoteDate={data?.quote_date} onClose={() => setDetail(null)} />}
    </div>
  ), document.body);
}


export function LadderDetailsPanel({ date }: { date: string }) {
  const [details, setDetails] = useState<LadderDetails | null>(null);
  const [open, setOpen] = useState(true);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<LadderStock | null>(null);
  useEffect(() => {
    if (!date) return;
    let active = true;
    setLoading(true);
    agentFetch<LadderDetails>(`/api/review/ladder?date=${encodeURIComponent(date)}`)
      .then((response) => { if (active) setDetails(response); })
      .catch(() => { if (active) setDetails({ available: false, date, reason: "梯队明细加载失败", tiers: [] }); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [date]);

  if (loading && !details) return <div className="mt-3 flex items-center gap-2 border-t border-dashed border-border pt-3 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />加载具体标的…</div>;
  if (!details?.available) return details ? <p className="mt-3 border-t border-dashed border-border pt-3 text-xs text-muted-foreground">具体标的暂不可用：{details.reason}</p> : null;
  const tiers = safeArray<LadderDetails["tiers"][number]>(details.tiers);
  return (
    <div className="mt-3 border-t border-dashed border-border pt-3">
      <button onClick={() => setOpen((value) => !value)} className="flex w-full items-center justify-between text-xs font-semibold">
        <span>具体标的 · {details.count ?? 0} 只</span><span className="inline-flex items-center gap-1 text-muted-foreground">{open ? "收起" : "展开"}<ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !open && "-rotate-90")} /></span>
      </button>
      {open && <div className="mt-2 max-h-72 space-y-2 overflow-y-auto pr-1">
        {tiers.map((tier) => (
          <div key={tier.boards} className="grid grid-cols-[44px_1fr] gap-2 rounded-xl bg-muted/30 p-2">
            <div><div className="text-sm font-extrabold text-danger">{tier.boards}板</div><div className="text-[10px] text-muted-foreground">{tier.count} 封</div></div>
            <div className="flex min-w-0 flex-wrap gap-1.5">
              {safeArray<LadderStock>(tier.stocks).map((stock) => (
                <button key={stock.code} onMouseEnter={() => { void loadIndustryPeers(stock, date).catch(() => undefined); }} onFocus={() => { void loadIndustryPeers(stock, date).catch(() => undefined); }} onClick={() => setSelected(stock)} title={`${stock.name} ${stock.industry} ${stock.reason || ""}`} className="group min-w-[118px] flex-1 rounded-lg border border-border bg-card px-2 py-1.5 text-left shadow-sm transition-colors hover:border-primary/50 hover:bg-primary/5">
                  <span className="flex items-center justify-between gap-2"><b className="truncate text-[12px]">{stock.name}</b><span className={cn("font-mono text-[11px]", pctColor(stock.pct))}>{signed(stock.pct)}</span></span>
                  <span className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-muted-foreground"><span className="truncate">{stock.industry || "行业未知"}</span><span>{stock.seal_time || "—"}</span></span>
                  <span className="mt-1 hidden items-center gap-1 text-[10px] font-medium text-primary group-hover:flex"><LineChart className="h-3 w-3" />同行业与行情 <ExternalLink className="h-2.5 w-2.5" /></span>
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>}
      <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">点击标的：先看同行业涨幅与分时对比，再点同行业列表进入个股行情卡。</p>
      {selected && <IndustryDrawer stock={selected} reviewDate={date} onClose={() => setSelected(null)} />}
    </div>
  );
}
