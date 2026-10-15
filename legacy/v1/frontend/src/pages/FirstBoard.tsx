import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Flame, Loader2, Sparkles, AlertCircle, X, ArrowUp, ArrowDown, ArrowUpDown, ChevronLeft, ChevronRight, RotateCcw, RefreshCw, Download, Circle, ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { StockLink } from "@/components/ui/StockLink";
import { useDeepDive, DeepDivePanel, RunAllButton, type DiveItem } from "@/components/ui/DeepDive";
import { api, type FirstBoardData, type FirstBoardStock, type MarketSession } from "@/lib/api";
import { formatDragonTigerContext } from "@/lib/dragon-tiger";
import { DEFAULT_SORT, SORT_LABELS, sortFirstBoard, toggleSort, type SortKey, type SortRule } from "@/lib/firstboard-sort";
import { loadAgentConnection } from "@/lib/agent-api";
import { buildFirstBoardExport } from "@/lib/firstboard-export";
import { StockTrafficBadge } from "@/components/BoardTrafficLight";
import type { BoardTrafficLight, StockTrafficLight, TrafficLightColor } from "@/lib/agent";
import { cn } from "@/lib/utils";

const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`); // 元 → 亿

const dateLabel = (d: string) =>
  d.length === 8 ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6)}` : d;

export function firstBoardAnalysisStage(
  boardDate: string, finalized: boolean, session: MarketSession | null,
): string {
  if (finalized) return "settled";
  if (!session || !boardDate) return "snapshot";
  if (boardDate !== session.today) return "historical";
  if (session.phase === "已收盘") return "settled";
  if (session.phase === "集合竞价") return "auction";
  if (session.phase === "盘前") return "preopen";
  const hhmm = Number((session.now.match(/(\d{2}):(\d{2})$/) || []).slice(1).join(""));
  if (!Number.isFinite(hhmm) || !hhmm) return "live";
  if (hhmm < 1130) return "morning";
  if (hhmm < 1300) return "midday";
  if (hhmm < 1430) return "afternoon";
  if (hhmm <= 1500) return "closing";
  return "settled";
}

const stageLabel = (stage: string) => ({
  settled: "收盘定稿", historical: "历史快照", auction: "集合竞价",
  preopen: "盘前", morning: "上午盘中", midday: "午间",
  afternoon: "午后盘中", closing: "尾盘", live: "盘中", snapshot: "快照待确认",
}[stage] || stage);

const lightTone: Record<TrafficLightColor, string> = {
  green: "text-success", yellow: "text-warning", red: "text-danger", gray: "text-muted-foreground",
};

function ThemeGroupCell({ stock }: { stock: FirstBoardStock }) {
  const group = stock.theme_group;
  if (!group || group.type === "missing") return <span className="text-muted-foreground/50">—</span>;
  if (group.type === "catalyst_only") {
    return (
      <div className="max-w-44">
        <span className="rounded bg-warning/10 px-1.5 py-0.5 text-[10px] font-semibold text-warning">仅共同催化</span>
        <p className="mt-1 break-words text-[11px] text-muted-foreground">
          {group.catalysts.map((item) => `${item.name} · ${item.member_count}只`).join("；")}
        </p>
      </div>
    );
  }
  return (
    <div className="max-w-44">
      <p className="truncate" title={`${group.name} · ${group.member_count}只`}>
        <span className="font-medium text-foreground">{group.name}</span>
        <span className="ml-1 text-[10px] text-muted-foreground">{group.member_count}只</span>
      </p>
      {group.catalysts.length > 0 && (
        <p className="mt-1 break-words text-[10px] text-muted-foreground" title="共同催化不计入题材支持分">
          催化：{group.catalysts.map((item) => item.name).join("、")}
        </p>
      )}
    </div>
  );
}

export function FirstBoard() {
  const [data, setData] = useState<FirstBoardData | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const [loaded, setLoaded] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [sortRules, setSortRules] = useState<SortRule[]>(DEFAULT_SORT);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [concurrency, setConcurrency] = useState(3);
  const [environment, setEnvironment] = useState<BoardTrafficLight | null>(null);
  const [liveLights, setLiveLights] = useState<Record<string, StockTrafficLight>>({});
  const [session, setSession] = useState<MarketSession | null>(null);
  const selectAllRef = useRef<HTMLInputElement>(null);
  const tradingDate = selectedDate.split("-").join("") || data?.date || "";
  const boardDate = dateLabel(tradingDate);
  const analysisStage = firstBoardAnalysisStage(boardDate, data?.finalized === true, session);
  const dd = useDeepDive(`firstboard-${analysisStage}`, tradingDate);
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date());
  const provider = loadAgentConnection()?.provider;
  const serial = !!provider && ["codex-private", "claude", "codebuddy"].includes(provider);

  useEffect(() => { setSelected(new Set()); }, [data?.date]);
  useEffect(() => {
    const available = new Set(data?.stocks.map((s) => s.code));
    setSelected((current) => new Set([...current].filter((code) => available.has(code))));
  }, [data]);
  useEffect(() => {
    if (selectAllRef.current) selectAllRef.current.indeterminate = selected.size > 0 && selected.size < (data?.stocks.length || 0);
  }, [selected, data]);

  useEffect(() => {
    let active = true;
    setRefreshing(true); setError("");
    Promise.all([
      api.firstBoard(selectedDate || undefined),
      api.marketSession().catch(() => null),
    ]).then(([next, initialSession]) => {
      if (!active) return;
      if (initialSession) setSession(initialSession);
      if (selectedDate && next.date?.split("-").join("") !== selectedDate.split("-").join("")) {
        throw new Error("返回交易日与所选日期不符，请刷新或检查后端版本");
      }
      if (!next.date || !Array.isArray(next.stocks)) throw new Error("暂无可用首板数据，请稍后重试");
      setData(next);
    }).catch((err) => {
      if (active) setError(err instanceof Error ? err.message : "首板数据读取失败，请重试");
    }).finally(() => {
      if (active) { setLoaded(true); setRefreshing(false); }
    });
    return () => { active = false; };
  }, [selectedDate, refreshVersion]);

  useEffect(() => {
    if (!data?.date || !data.stocks.length) return;
    let active = true;
    let lightTimer: ReturnType<typeof setInterval> | undefined;
    let poolTimer: ReturnType<typeof setInterval> | undefined;
    const boardDate = dateLabel(data.date);

    const refreshLights = async () => {
      try {
        const nextSession = await api.marketSession();
        if (!active) return;
        setSession(nextSession);
        if (boardDate !== nextSession.today) {
          if (active) { setEnvironment(null); setLiveLights({}); }
          return;
        }
        const intraday = nextSession.phase === "盘中" || nextSession.phase === "集合竞价";
        // 收盘后展示首板接口附带的收盘定稿灯。不能再用实时池重新算一遍，
        // 否则题材成员会与复盘落盘的事实池出现两套口径。
        if (!intraday) {
          setEnvironment(null); setLiveLights({});
          return;
        }
        const chunks: string[][] = [];
        const codes = data.stocks.map((stock) => stock.code);
        for (let index = 0; index < codes.length; index += 100) chunks.push(codes.slice(index, index + 100));
        const results = await Promise.all(chunks.map((chunk) => api.boardTrafficLight(chunk, intraday ? "live" : "settled")));
        if (!active || !results.length) return;
        setEnvironment(results[0]);
        setLiveLights(Object.assign({}, ...results.map((result) => result.stocks || {})));
        if (intraday && !lightTimer) {
          lightTimer = setInterval(refreshLights, 5_000);
          poolTimer = setInterval(() => setRefreshVersion((version) => version + 1), 60_000);
        }
      } catch {
        if (active) { setEnvironment(null); setLiveLights({}); }
      }
    };
    void refreshLights();
    return () => {
      active = false;
      if (lightTimer) clearInterval(lightTimer);
      if (poolTimer) clearInterval(poolTimer);
    };
  }, [data?.date, data?.stocks]);

  const changeDate = (date: string) => {
    if (date === selectedDate) return;
    if (dd.busy) dd.stopAll();
    setSelected(new Set()); setData(null); setLoaded(false); setError("");
    setSelectedDate(date);
  };
  const refresh = () => setRefreshVersion((version) => version + 1);
  const moveSort = (index: number, offset: number) => setSortRules((current) => {
    const next = [...current], target = index + offset;
    if (target < 0 || target >= next.length) return current;
    [next[index], next[target]] = [next[target], next[index]];
    return next;
  });

  const settledForAnalysis = analysisStage === "settled" || analysisStage === "historical";
  const snapshotAsOf = settledForAnalysis
    ? (data?.persisted_at || `${boardDate} 15:00`)
    : (session?.now || `${boardDate} 时间暂缺`);
  const buildPrompt = (s: FirstBoardStock) =>
    `交易日 ${boardDate}，数据快照 ${snapshotAsOf}（${stageLabel(analysisStage)}），A 股首板涨停股「${s.name}（${s.code}）」的客观数据：\n` +
    `现价 ${s.price} 元，涨停 +${s.pct}%，首次封板时间 ${s.seal_time || "未知"}，` +
    `炸板 ${s.break_count} 次，成交额 ${yi(s.amount)}，流通市值 ${yi(s.float_cap)}，` +
    `所属行业 ${s.industry || "未知"}，涨停题材：${s.reason || "（暂缺，需要自查）"}，` +
    `事件树归组：${s.theme_group?.type === "theme" ? `${s.theme_group.name}（同题材${s.theme_group.member_count}只）` : "未形成有效产业题材"}。\n\n` +
    "请深入分析这只股票在上述交易日涨停的原因，不要把该日期当成当前日期：\n" +
    `1. 先调用工具查询截至上述交易日的近期新闻与研报；调用 query_news / query_reports 时必须传 end_date=${boardDate}，明确资料日期，不用之后的信息解释当日行情；结合题材串，说清当日涨停最可能的驱动因素（消息面 / 题材面 / 资金面）；\n` +
    "2. 就**这个题材板块整体**说清它的强度与所处阶段（情绪性的一日游 / 有产业逻辑或业绩支撑），" +
    "并给出依据 —— 只讲题材板块层面，不要由此推断这只个股接下来会怎样；\n" +
    "3. 客观列出值得注意的点（炸板情况、封板时间早晚、流通盘大小、题材扩散位置）。\n" +
    (settledForAnalysis
      ? "4. **龙虎榜席位与公开画像**：以下预取数据以该交易日为截止日。若上榜，分别分析买方席位、卖方席位和双方净额，说明席位来源类型（机构专用、沪深股通、营业部）及集中度；对能够由公开资料可靠识别的席位，尽可能说明其公开市场称谓和口碑、常见操作风格及本次行为是否相符。必须区分可核实事实、市场称谓与推断；无法可靠对应具体游资时写“无法可靠识别”，不得仅凭营业部名称强行归因。若未上榜或席位数据为空，明确说明，不得臆测。\n"
      : "4. 当前仍是盘中快照，龙虎榜尚未形成收盘定稿，本次不根据未完成榜单推测席位。\n") +
    "个股层面只陈述已经发生的客观数据与事实，方向与强弱判断做到题材板块层面为止：" +
    "不预测个股涨跌、不给个股参与倾向、不推荐任何标的、不构成投资建议。" +
    "输出用纯 Markdown（不要在表格或正文里使用 <br> 等 HTML 标签）。";

  const ctx = (s: FirstBoardStock) =>
    `${boardDate} ${stageLabel(analysisStage)}首板股 ${s.name}(${s.code}) 涨停原因深入分析 · 快照 ${snapshotAsOf}`;
  const diveItem = (s: FirstBoardStock): DiveItem => ({
    key: s.code,
    name: s.name,
    prompt: buildPrompt(s),
    context: ctx(s),
    prepareContext: settledForAnalysis ? async () => {
      try {
        const dragonTiger = await api.dragonTiger(s.code, boardDate);
        return formatDragonTigerContext(dragonTiger, boardDate);
      } catch (error) {
        return `龙虎榜预取失败：${error instanceof Error ? error.message : "数据源暂不可用"}。不得据此推测席位来源或游资身份。`;
      }
    } : undefined,
  });

  const stocks = useMemo(() => sortFirstBoard((data?.stocks ?? []).map((stock) => ({
    ...stock, traffic_light: liveLights[stock.code] || stock.traffic_light,
  })), sortRules), [data, liveLights, sortRules]);
  const historicalEnvironment = data?.stocks.find((stock) => stock.traffic_light?.environment)?.traffic_light?.environment;
  const environmentLight = environment?.light || historicalEnvironment?.light || "gray";
  const environmentLabel = environment?.label || historicalEnvironment?.label || "历史环境数据缺失";
  const environmentAction = environmentLight === "green" ? "可进入个股筛选" :
    environmentLight === "yellow" ? "暂缓新仓" : environmentLight === "red" ? "停止新仓" : "环境待确认";
  const nameByCode = Object.fromEntries(stocks.map((s) => [s.code, s.name]));
  const exported = useMemo(() => buildFirstBoardExport(tradingDate, dd, stocks),
    [tradingDate, dd.date, dd.completed, dd.analysis, dd.names, stocks]);
  const exportAnalyses = () => {
    if (!exported || refreshing || !loaded) return;
    const url = URL.createObjectURL(new Blob([exported.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = exported.filename;
    document.body.appendChild(anchor); anchor.click(); anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  return (
    <div>
      <PageHeader
        title="首板分析"
        subtitle="首板涨停股（连板数=1）· 涨停原因题材 · 每只可让 AI 深入分析"
      />

        <div className="mb-4 grid grid-cols-2 gap-3 sm:grid-cols-3">
          <GlassCard className="col-span-2 min-w-0 py-3 text-center sm:col-span-1">
            <div className="text-xs text-muted-foreground">交易日 · {selectedDate ? "自选" : "自动"}</div>
            <div className="mt-1 flex min-h-8 items-center justify-center gap-1">
              <input type="date" aria-label="交易日" max={today} value={selectedDate || dateLabel(data?.date || "")}
                onChange={(event) => changeDate(event.target.value)}
                className="h-8 w-40 min-w-0 max-w-full rounded border border-border bg-transparent px-1 font-mono text-base font-bold text-primary" />
              <button type="button" title="恢复自动日期" aria-label="恢复自动日期" disabled={!selectedDate} onClick={() => changeDate("")}
                className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary disabled:opacity-30">
                <RotateCcw className="h-3.5 w-3.5" />
              </button>
            </div>
            <div className="mt-1 text-[10px] text-muted-foreground/70">
              {data?.finalized
                ? `收盘定稿${data.persisted_at ? " · 已落盘" : ""} · 保留最近 ${data.retention_days ?? 7} 个交易日`
                : "盘中实时 · 收盘后自动定稿落盘"}
            </div>
          </GlassCard>
          {[
            { label: "涨停家数", value: data?.total_zt == null ? "—" : `${data.total_zt} 家` },
            { label: "其中首板", value: data?.first_count == null ? "—" : `${data.first_count} 家` },
          ].map((c) => (
            <GlassCard key={c.label} className="py-3 text-center">
              <div className="text-xs text-muted-foreground">{c.label}</div>
              <div className="mt-1 font-mono text-lg font-bold text-primary">{c.value}</div>
            </GlassCard>
          ))}
        </div>

      {(error || data?.data_note || data?.reason_note) && (
        <p role="status" className="mb-3 flex items-start gap-1.5 break-words text-xs text-muted-foreground">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span>{error || data?.data_note || `涨停原因：${data?.reason_note}`}</span>
        </p>
      )}

      <section aria-label="首板开仓环境" className="mb-4 min-w-0 border-y border-border py-3">
        <div className="flex flex-wrap items-center gap-2">
          <ShieldAlert className="h-4 w-4 text-muted-foreground" />
          <b className="text-sm">开仓环境</b>
          <Caliber text={
            "环境总灯由大盘环境、情绪周期、打板生态合成。至少两项分灯为绿且没有红灯，环境总灯才为绿。\n" +
            "环境绿只代表进入人工筛选；环境黄暂缓新仓，环境红停止新仓。个股质量灯仍独立展示。"
          } />
          <span className={cn("inline-flex items-center gap-1 text-xs font-semibold", lightTone[environmentLight])}>
            <Circle className="h-2.5 w-2.5 fill-current" />{environmentLabel}
          </span>
          <span className={cn("text-xs font-bold", lightTone[environmentLight])}>{environmentAction}</span>
          {environment?.as_of && <span className="ml-auto text-[11px] text-muted-foreground">{environment.as_of}</span>}
        </div>
        <p className="mt-1 text-xs text-muted-foreground">
          环境灯决定是否建议新开仓；个股质量独立保留，用于盘中筛选或收盘后的次日1进2观察。
        </p>
      </section>

      <GlassCard>
        <div className="mb-2 flex flex-wrap items-center gap-2 text-sm font-semibold">
          <Flame className="h-4 w-4 text-primary" /> 首板名单
          <Caliber text={
            "「炸板」是当天开板过几次，0 就是全天没开过板 —— 这张表里的票**最终都封住了涨停**，\n" +
            "开板次数本身不再直接判红：末封时间和回封后的稳定时长会共同判断分歧是否得到承接。\n" +
            "个股质量四项各计1分：首封≤10:30、换手率3%–25%、方向支持、封板过程；方向支持由同一产业题材或同行业至少2只满足其一，合计只计1分；≥3/4且无风险项为晋级候选。\n" +
            "题材归组复用短线复盘的题材事件树；业绩增长、回购、增持等共同催化单独展示，不计题材支持分。\n" +
            "封板资金和封板资金/流通市值仅展示，不计分。\n" +
            "默认按首次封板时间从早到晚排；组合排序按优先级依次比较，同值才比较下一列，缺失值置后。\n" +
            "「涨停原因」是上游给出的涨停题材标签串，不等同于对单只股票涨停的因果归因；\n" +
            "「行业」经常只有四个字（像「互联网电」「自动化设」）——是上游把名字截到四字，\n" +
            "不是这里显示不全；怕猜错所以不替它补全称。"
          } />
          <span className="text-xs font-normal text-muted-foreground">
            客观公开榜单，非推荐 / 非预测
          </span>
          <span className="ml-auto inline-flex max-w-full flex-wrap items-center gap-2 font-normal">
            <span className="text-xs text-muted-foreground">已选 {selected.size} 只</span>
            <button type="button" aria-label="清空选择" title="清空选择" disabled={!selected.size || dd.busy} onClick={() => setSelected(new Set())}
              className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground disabled:opacity-40"><X className="h-3.5 w-3.5" /></button>
            <label className="inline-flex items-center gap-1 text-xs text-muted-foreground">{serial ? "CLI 队列" : "并发"}
              <select aria-label="同时分析数量" value={serial ? 1 : concurrency} disabled={serial || dd.busy} onChange={(e) => setConcurrency(Number(e.target.value))}
                title={serial ? "CLI 通道按顺序分析" : "同时分析数量"} className="h-8 rounded border border-border bg-card px-1.5 disabled:opacity-50">
                {[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}
              </select>
            </label>
            <RunAllButton dd={dd} items={stocks.map(diveItem)} selectedItems={stocks.filter((s) => selected.has(s.code)).map(diveItem)}
              concurrency={serial ? 1 : concurrency} nameOf={(k) => nameByCode[k] || k} />
          </span>
          <button type="button" onClick={exportAnalyses} disabled={!exported || refreshing || !loaded} aria-label="导出当日分析"
            title={`导出 ${dateLabel(tradingDate)} 已完成分析（${exported?.count || 0} 只，Markdown）`}
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary disabled:opacity-40">
            <Download className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={refresh} disabled={refreshing || !loaded} aria-label="刷新首板数据" title="刷新首板数据"
            className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary disabled:opacity-40">
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
        <div aria-label="排序优先级" className="mb-3 flex flex-wrap items-center gap-2 border-b border-border/50 pb-3 text-xs">
          <span className="text-muted-foreground">排序</span>
          {sortRules.map((rule, index) => (
            <div key={rule.key} className="inline-flex min-h-8 items-center gap-1 border-l border-border pl-2">
              <span className="w-3 text-center font-mono text-muted-foreground">{index + 1}</span>
              <span>{SORT_LABELS[rule.key]}</span>
              <button type="button" aria-label={`切换${SORT_LABELS[rule.key]}排序方向`} title={rule.direction === "asc" ? "当前升序，切换降序" : "当前降序，切换升序"}
                onClick={() => setSortRules((rules) => rules.map((r) => r.key === rule.key ? { ...r, direction: r.direction === "asc" ? "desc" : "asc" } : r))}
                className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted">
                {rule.direction === "asc" ? <ArrowUp className="h-3.5 w-3.5" /> : <ArrowDown className="h-3.5 w-3.5" />}
              </button>
              <button type="button" disabled={index === 0} onClick={() => moveSort(index, -1)} aria-label={`提高${SORT_LABELS[rule.key]}排序优先级`} title="提高排序优先级"
                className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted disabled:opacity-25"><ChevronLeft className="h-3.5 w-3.5" /></button>
              <button type="button" disabled={index === sortRules.length - 1} onClick={() => moveSort(index, 1)} aria-label={`降低${SORT_LABELS[rule.key]}排序优先级`} title="降低排序优先级"
                className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted disabled:opacity-25"><ChevronRight className="h-3.5 w-3.5" /></button>
              <button type="button" onClick={() => setSortRules((rules) => rules.filter((r) => r.key !== rule.key))} aria-label={`移除${SORT_LABELS[rule.key]}排序`} title="移除排序条件"
                className="inline-flex h-7 w-7 items-center justify-center rounded hover:bg-muted"><X className="h-3.5 w-3.5" /></button>
            </div>
          ))}
          {!sortRules.length && <span className="text-muted-foreground">原始顺序</span>}
          <button type="button" onClick={() => setSortRules(DEFAULT_SORT)} aria-label="恢复默认排序" title="恢复默认排序：首封升序"
            className="ml-auto inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary"><RotateCcw className="h-3.5 w-3.5" /></button>
        </div>
        {!loaded ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" /> 加载中…
          </div>
        ) : stocks.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">{error || data?.data_note || "暂无首板数据"}</div>
        ) : (
          <div className="overflow-x-auto">
            <table aria-label="首板名单" className="w-full min-w-[980px] table-fixed text-sm">
              <colgroup>
                <col className="w-9" />
                <col className="w-[6.5rem]" />
                <col className="w-[7rem]" />
                <col className="w-[5rem]" />
                <col className="w-[3.5rem]" />
                <col className="w-[5.5rem]" />
                <col className="w-[6.5rem]" />
                <col className="w-[11rem]" />
                <col className="w-[8.5rem]" />
                <col className="w-[5rem]" />
                <col className="w-[5.5rem]" />
              </colgroup>
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  <th className="w-9 px-2 py-2">
                    <input ref={selectAllRef} type="checkbox" aria-label="选择全部首板股票" disabled={dd.busy}
                      checked={stocks.length > 0 && selected.size === stocks.length}
                      onChange={(e) => setSelected(e.target.checked ? new Set(stocks.map((s) => s.code)) : new Set())}
                      className="h-4 w-4 align-middle accent-primary" />
                  </th>
                  {[
                    { id: "name", label: "名称" },
                    { id: "quality", label: "个股质量" },
                    { id: "seal", label: "首 / 末封", sortKey: "seal_time" as SortKey },
                    { id: "break", label: "炸板", sortKey: "break_count" as SortKey },
                    { id: "quote", label: "现价 / 涨幅", sortKey: "pct" as SortKey },
                    { id: "capital", label: "成交 / 流值" },
                    { id: "reason", label: "涨停原因" },
                    { id: "theme", label: "题材归组" },
                    { id: "industry", label: "行业", sortKey: "industry" as SortKey },
                    { id: "action", label: "" },
                  ].map((header) => {
                    const key = header.sortKey;
                    const index = sortRules.findIndex((rule) => rule.key === key);
                    const rule = sortRules[index];
                    const Icon = !rule ? ArrowUpDown : rule.direction === "asc" ? ArrowUp : ArrowDown;
                    return <th key={header.id} aria-sort={index === 0 ? rule.direction === "asc" ? "ascending" : "descending" : undefined}
                      className="whitespace-nowrap px-1.5 py-2 font-medium">
                      {key ? <button type="button" onClick={() => setSortRules((rules) => toggleSort(rules, key))}
                        aria-label={`${header.label}排序`} title={`${header.label}：${rule ? `${rule.direction === "asc" ? "升序" : "降序"}，优先级 ${index + 1}` : "未排序"}；点击切换升序、降序、取消`}
                        className={`inline-flex min-h-8 items-center gap-1 hover:text-primary ${rule ? "text-primary" : ""}`}>
                        {header.label}<Icon className="h-3.5 w-3.5 shrink-0" /><span className="inline-block w-2 text-[10px] tabular-nums">{rule ? index + 1 : ""}</span>
                      </button> : header.label}
                    </th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {stocks.map((s) => (
                  <Fragment key={s.code}>
                    <tr className="border-b border-border/30">
                      <td className="w-9 px-2 py-2">
                        <input type="checkbox" aria-label={`选择 ${s.name} ${s.code}`} checked={selected.has(s.code)} disabled={dd.busy}
                          onChange={(e) => setSelected((current) => { const next = new Set(current); if (e.target.checked) next.add(s.code); else next.delete(s.code); return next; })}
                          className="h-4 w-4 align-middle accent-primary" />
                      </td>
                      <td className="px-1.5 py-2">
                        <StockLink code={s.code} className="block truncate font-medium" title={s.name}>{s.name}</StockLink>
                        <StockLink code={s.code} className="block font-mono text-[10px] text-muted-foreground/50">{s.code}</StockLink>
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-2"><StockTrafficBadge light={s.traffic_light} /></td>
                      <td className="px-1.5 py-2 font-mono text-[11px]">
                        <span className="block">首 {s.seal_time || "—"}</span>
                        <span className="block text-muted-foreground">末 {s.last_seal_time || "—"}</span>
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-2 font-mono">
                        {s.break_count > 0 ? <span className="text-primary">{s.break_count} 次</span> : <span className="text-muted-foreground/50">0</span>}
                      </td>
                      <td className="px-1.5 py-2 font-mono text-[11px]">
                        <span className="block">{s.price == null ? "—" : fmt(s.price)}</span>
                        <span className="block text-danger">{s.pct == null ? "—" : `${s.pct > 0 ? "+" : ""}${fmt(s.pct)}%`}</span>
                      </td>
                      <td className="whitespace-nowrap px-1.5 py-2 font-mono text-[10px] text-muted-foreground">
                        <span className="block">成 {yi(s.amount)}</span>
                        <span className="block">流 {yi(s.float_cap)}</span>
                      </td>
                      <td className="px-1.5 py-2 text-[11px]">
                        {s.reason ? <span className="line-clamp-2 break-words text-foreground" title={s.reason}>{s.reason}</span> : <span className="text-muted-foreground/50">—</span>}
                      </td>
                      <td className="px-1.5 py-2 text-[11px]"><ThemeGroupCell stock={s} /></td>
                      <td className="break-words px-1.5 py-2 text-[11px] text-muted-foreground">{s.industry}</td>
                      <td className="whitespace-nowrap px-1.5 py-2 text-right">
                        <button
                          onClick={() => dd.toggle(diveItem(s))}
                          disabled={dd.busy && !dd.analysis[s.code] && !dd.runningKeys.includes(s.code) && !dd.queuedKeys.includes(s.code)}
                          className="inline-flex items-center gap-1 rounded-lg border border-primary/50 bg-primary/10 px-2 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-40"
                        >
                          {dd.runningKeys.includes(s.code) ? <Loader2 className="h-3 w-3 animate-spin" /> : dd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                          {dd.open === s.code ? "收起" : dd.runningKeys.includes(s.code) ? "分析中" : dd.queuedKeys.includes(s.code) ? "等待" : dd.errors[s.code] ? "重试" : dd.completed[s.code] ? "展开" : "分析"}
                        </button>
                      </td>
                    </tr>
                    {dd.open === s.code && (
                      <DeepDivePanel
                        dd={dd}
                        stockKey={s.code}
                        colSpan={11}
                        noteTitle={`首板深析 · ${dateLabel(tradingDate)} · ${s.name}`}
                        onRerun={() => dd.rerun(diveItem(s))}
                      />
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      <Disclaimer />
    </div>
  );
}
