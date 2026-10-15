import { useState, useEffect, useRef, Fragment } from "react";
import { pctColor } from "@/lib/colors";
import { Sparkles, Loader2, RefreshCw, Gauge, ArrowDownUp, TrendingUp, TrendingDown, Plus, X, Flame, BarChart3, Globe, Download, Bell, BellRing, ChevronDown } from "lucide-react";
import { toast } from "sonner";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Caliber } from "@/components/ui/Caliber";
import { AskAiButton } from "@/components/ui/AskAiButton";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { StockLink } from "@/components/ui/StockLink";
import { finite, type BoardTrafficLight } from "@/lib/agent";
import { api, type IndexQuote, type Quote, type MarketOverview, type ShortTermEmotion, type LianbanStock, type TurnoverTop, type GlobalIndex, type MarketSession, type OverseasSnapshot, type OverseasRow, type LiveEmotion, type LimitBreakAlerts } from "@/lib/api";
import { useDeepDive, DeepDivePanel, RunAllButton, type DiveItem } from "@/components/ui/DeepDive";
import { loadWatch, saveWatch, addCodes } from "@/lib/watchlist";
import { cn } from "@/lib/utils";
import { buildDailyEmotionExport } from "@/lib/daily-review-emotion-export";
import { EmotionRegimePanel } from "@/components/EmotionRegime";
import { BoardTrafficLightPanel, StockTrafficBadge } from "@/components/BoardTrafficLight";

// A股红涨绿跌。全球市场（美股/港股指数）**也沿用红涨**——与整个看板及东财等中国平台一致，
// A 股惯例：红涨绿跌。与国际绿涨惯例相反，是有意选择，全站必须一致，勿改。

const AUTO_KEY = "vibe-astock-auto-refresh";
const LIMIT_ALERT_KEY = "vibe-astock-limit-break-alerts";
// 下方 N 日→N+1 跟踪名单的提醒不能和上方今日二板池共用开关或去重状态。
const FEEDBACK_LIMIT_ALERT_KEY = "vibe-astock-feedback-limit-break-alerts";
const WATCH_LIMIT_ALERT_KEY = "vibe-astock-watch-limit-break-alerts";
const OVERSEAS_COLLAPSE_KEY = "vibe-astock-overseas-collapsed";
const LIVE_MS = 5_000;      // 轻量行情：腾讯批量，5 秒
const HEAVY_MS = 60_000;    // 板块资金 / 成交额榜：东财+akshare，60 秒

const fmt = (v: number) => v.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
const yi = (v: number | null) => (v == null ? "—" : `${fmt(v / 1e8)} 亿`); // 元 → 亿
// 连板表字段较多；分析入口放在名称列，避免固定最右操作列遮住“概念”等末列。
// 两张名单独立滚动，避免长列表把整页拉得过长；横向仍可查看全部字段。
// 窄屏不能再把 11 列硬压进卡片：保持可读的最小表宽，并在表格容器内横向滑动。
const BOARD_TABLE_WRAP = "max-h-[680px] overflow-auto overscroll-contain pb-2";
const BOARD_TABLE_HEAD = "sticky top-0 z-10 bg-card/95 shadow-sm backdrop-blur";
// 桌面端固定布局优先把有限宽度分配给“涨停原因”；窄屏改由容器横向滚动承载。
const BOARD_TABLE = "min-w-[960px] w-full table-fixed text-[11px] [&_th]:overflow-hidden [&_th]:text-ellipsis";
// 今日二板表和历史跟踪表都只展示规则统一的个股灯。
const LIVE_BOARD_COLUMN_WIDTHS = ["3%", "12%", "6%", "18%", "6%", "7%", "9%", "8%", "8%", "16%", "7%"];
const SETTLED_BOARD_COLUMN_WIDTHS = ["3%", "12%", "6%", "17%", "6%", "7%", "9%", "8%", "8%", "15%", "9%"];
const closeBoardResult = (sealed: boolean | undefined, breakCount: number | null | undefined) => {
  const breaks = typeof breakCount === "number" && Number.isFinite(breakCount) ? breakCount : null;
  if (sealed === true) return breaks && breaks > 0 ? `封板（炸 ${breaks} 次）` : "封板";
  if (sealed === false) return breaks && breaks > 0 ? `炸板（${breaks} 次）` : "炸板";
  return breaks && breaks > 0 ? `炸板（${breaks} 次）` : "状态待确认";
};
const trackedCloseResult = (stage: "not_ready" | "observe" | "sealed" | undefined, breakCount?: number | null) => {
  if (stage === "sealed") return breakCount && breakCount > 0 ? `封板（炸 ${breakCount} 次）` : "封板";
  if (stage === "observe") return "临板";
  if (stage === "not_ready") return "未封板";
  return "状态待确认";
};
const liveBoardResult = (sealed: boolean | undefined, breakCount: number | null | undefined, alert?: { status: string; break_count: number }) => {
  if (alert?.status === "break") return `炸板（${alert.break_count} 次）`;
  if (alert?.status === "sealed") return closeBoardResult(true, breakCount);
  return closeBoardResult(sealed, breakCount);
};
const beijingDay = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());
const overseasCollapseKey = (date: string) => `${OVERSEAS_COLLAPSE_KEY}:${date}`;

export function DailyReview() {
  const [indices, setIndices] = useState<IndexQuote[]>([]);
  const [idxErr, setIdxErr] = useState(false);
  const [overview, setOverview] = useState<MarketOverview | null>(null);
  const [emotion, setEmotion] = useState<ShortTermEmotion | null>(null);
  const [turnover, setTurnover] = useState<TurnoverTop | null>(null);
  const [globalIdx, setGlobalIdx] = useState<GlobalIndex[]>([]);
  // 实时行情属于哪一场：盘前接口返回的是上一场收盘，不标出来会被当成今天的
  const [session, setSession] = useState<MarketSession | null>(null);
  // 隔夜外围：指数 + 七姐妹。走自己的接口是为了拿到**行情所属交易日**
  // （vr 的 /api/global/indices 不回时间，界面就只能按本机今天贴日期）
  const [oversea, setOversea] = useState<OverseasSnapshot | null>(null);
  // 默认展开；仅记录当天由用户主动折叠的选择，换日自然回到展开。
  const overseasDay = session?.today || beijingDay();
  const [overseasExpanded, setOverseasExpanded] = useState(
    () => localStorage.getItem(overseasCollapseKey(beijingDay())) !== "1",
  );
  // 今日实时打板情绪。与下面那块「昨日短线情绪」是两回事：
  // 这块随盘变，那块是已收盘那一场的定稿。
  const [liveEmo, setLiveEmo] = useState<LiveEmotion | null>(null);
  // 明细很长，默认展示；用户可只保留上方情绪摘要。
  const [liveDetailsExpanded, setLiveDetailsExpanded] = useState(true);
  const [traffic, setTraffic] = useState<BoardTrafficLight | null>(null);
  // N 日连板名单在 N+1 的跟踪：和「今日触及连板」分开存，不能互相覆盖。
  const [feedbackQuotes, setFeedbackQuotes] = useState<Record<string, Quote>>({});
  const [feedbackTraffic, setFeedbackTraffic] = useState<BoardTrafficLight | null>(null);
  const trafficRequestId = useRef(0);
  const feedbackTrafficRequestId = useRef(0);
  const trafficBusy = useRef(false);
  const feedbackTrafficBusy = useRef(false);
  const liveEmotionBusy = useRef(false);
  const trafficMode = useRef<"live" | "settled" | null>(null);
  const [limitAlerts, setLimitAlerts] = useState<LimitBreakAlerts | null>(null);
  const [limitAlertsEnabled, setLimitAlertsEnabled] = useState<boolean>(
    () => localStorage.getItem(LIMIT_ALERT_KEY) === "1");
  const seenLimitEventIds = useRef(new Set<string>());
  const limitAlertsReady = useRef(false);
  const [feedbackLimitAlerts, setFeedbackLimitAlerts] = useState<LimitBreakAlerts | null>(null);
  const [feedbackLimitAlertsEnabled, setFeedbackLimitAlertsEnabled] = useState<boolean>(
    () => localStorage.getItem(FEEDBACK_LIMIT_ALERT_KEY) === "1");
  const seenFeedbackLimitEventIds = useRef(new Set<string>());
  const feedbackLimitAlertsReady = useRef(false);
  // 自选股的炸板提醒同样独立：只监控用户本地维护的代码列表。
  const [watchLimitAlertsEnabled, setWatchLimitAlertsEnabled] = useState<boolean>(
    () => localStorage.getItem(WATCH_LIMIT_ALERT_KEY) === "1");
  const seenWatchLimitEventIds = useRef(new Set<string>());
  const watchLimitAlertsReady = useRef(false);
  // 自动刷新开关：**默认关**（别替用户决定要不要一直打请求），选择记在本地
  const [autoRefresh, setAutoRefresh] = useState<boolean>(
    () => localStorage.getItem(AUTO_KEY) === "1");
  // 关注股票（自选，存本地）
  const [watchCodes, setWatchCodes] = useState<string[]>(loadWatch);
  const [watchQuotes, setWatchQuotes] = useState<Record<string, Quote>>({});
  const [watchInput, setWatchInput] = useState("");
  const [watchLoading, setWatchLoading] = useState(false);
  const [selectedLiveStocks, setSelectedLiveStocks] = useState<Set<string>>(new Set());
  const [selectedSettledStocks, setSelectedSettledStocks] = useState<Set<string>>(new Set());
  const liveStockCodes = (liveEmo?.lianban_stocks ?? []).map((stock) => stock.code).join(",");
  const feedbackStockCodes = (emotion?.lianban_stocks ?? []).map((stock) => stock.code).join(",");

  // 各数据块请求是否已结束：区分「加载中」与「数据源暂不可用」（非交易时段/被限流时后端返回空）
  const [ovDone, setOvDone] = useState(false);
  const [emoDone, setEmoDone] = useState(false);
  const [toDone, setToDone] = useState(false);
  const [ovError, setOvError] = useState("");
  const [emoError, setEmoError] = useState("");
  const [toError, setToError] = useState("");
  const [liveError, setLiveError] = useState("");
  const errorText = (error: unknown) => error instanceof Error ? error.message : "数据源暂时不可用";

  const refreshTraffic = (codes: string[]) => {
    const mode = session?.phase === "盘中" ? "live" : session?.phase === "已收盘" ? "settled" : null;
    if (!mode) { setTraffic(null); return; }
    if (trafficBusy.current) return;
    trafficBusy.current = true;
    const requestId = ++trafficRequestId.current;
    if (trafficMode.current !== mode) {
      trafficMode.current = mode;
      setTraffic(null);
    }
    api.boardTrafficLight([...new Set(codes)], mode).then((next) => {
      if (requestId === trafficRequestId.current) setTraffic(next);
    }).catch(() => {
      if (requestId === trafficRequestId.current) setTraffic(null);
    }).finally(() => { trafficBusy.current = false; });
  };

  const refreshFeedbackTracking = (codes: string[]) => {
    const uniqueCodes = [...new Set(codes.filter(Boolean))];
    if (!uniqueCodes.length) { setFeedbackQuotes({}); setFeedbackTraffic(null); return; }
    api.quote(uniqueCodes.join(",")).then(setFeedbackQuotes).catch(() => setFeedbackQuotes({}));

    // 盘中看实时灯；收盘后必须切到 settled，以 N+1 最终涨停池计算收盘灯。
    // 非交易日不伪造当前状态。
    if (session?.phase !== "盘中" && session?.phase !== "已收盘") { setFeedbackTraffic(null); return; }
    if (feedbackTrafficBusy.current) return;
    feedbackTrafficBusy.current = true;
    const requestId = ++feedbackTrafficRequestId.current;
    api.boardTrafficLight(uniqueCodes, session?.phase === "已收盘" ? "settled" : "live").then((next) => {
      if (requestId === feedbackTrafficRequestId.current) setFeedbackTraffic(next);
    }).catch(() => {
      if (requestId === feedbackTrafficRequestId.current) setFeedbackTraffic(null);
    }).finally(() => { feedbackTrafficBusy.current = false; });
  };

  // 轻量实时组：全是腾讯批量行情，一次一个请求，5 秒一刷不吃力
  const loadLive = () => {
    api.indices().then(setIndices).catch(() => setIdxErr(true));
    api.overseas().then(setOversea).catch(() => {});
    api.marketSession().then(setSession).catch(() => {});
    if (!liveEmotionBusy.current) {
      liveEmotionBusy.current = true;
      api.liveEmotion().then((data) => {
        setLiveError("");
        setLiveEmo({
          ...data,
          lianban_stocks: Array.isArray(data.lianban_stocks) ? data.lianban_stocks : [],
        });
      }).catch((error) => setLiveError(errorText(error)))
        .finally(() => { liveEmotionBusy.current = false; });
    }
    refreshTraffic([...watchCodes, ...liveStockCodes.split(",").filter(Boolean)]);
    refreshFeedbackTracking(feedbackStockCodes.split(",").filter(Boolean));
  };

  // 重量组：板块资金走 akshare + JS 引擎（90 个行业）、成交额榜走东财 clist。
  // **不能跟着 5 秒刷** —— 会撞限流，而且这两样本身变化慢。
  const loadHeavy = () => {
    api.globalIndices().then(setGlobalIdx).catch(() => {});
    setOvDone(false);
    setToDone(false);
    api.marketOverview().then((data) => {
      setOverview(data);
      setOvError(data.warnings?.join("；") || "");
    }).catch((error) => setOvError(errorText(error))).finally(() => setOvDone(true));
    api.turnoverTop().then((data) => {
      setTurnover(data);
      setToError(data.reason || "");
    }).catch((error) => setToError(errorText(error))).finally(() => setToDone(true));
  };

  // 短线情绪锚在已收盘那一场，只在进页面时取一次，不参与任何轮询
  const loadSettled = (refresh = false) => {
    setEmoDone(false);
    setEmoError("");
    api.emotion(refresh).then((data) => setEmotion({
      ...data,
      lianban_stocks: Array.isArray(data.lianban_stocks) ? data.lianban_stocks : [],
    })).catch((error) => setEmoError(errorText(error))).finally(() => setEmoDone(true));
  };

  const loadIndices = () => { loadLive(); loadHeavy(); loadSettled(); };

  // 数据块占位：请求没回来 = 加载中；回来了但为空 = 数据源暂不可用（别让用户干等）
  const pending = (done: boolean, error = "") => (
    <p className={cn("py-4 text-center text-sm", error ? "text-warning" : "text-muted-foreground/60")}>
      {error || (done ? "暂无数据：数据源暂时不可用，可点「大盘指数」旁的刷新重试" : "加载中…")}
    </p>
  );

  const refreshWatch = (codes: string[]) => {
    if (!codes.length) { setWatchQuotes({}); return; }
    setWatchLoading(true);
    api.quote(codes.join(",")).then(setWatchQuotes).catch(() => {}).finally(() => setWatchLoading(false));
  };

  useEffect(() => {
    loadIndices();
    refreshWatch(loadWatch());
  }, []);

  useEffect(() => {
    setOverseasExpanded(localStorage.getItem(overseasCollapseKey(overseasDay)) !== "1");
  }, [overseasDay]);

  // 今日连板名单是异步来的，名单一到就把它纳入实时个股灯；历史定稿名单不参与。
  useEffect(() => {
    refreshTraffic([...watchCodes, ...liveStockCodes.split(",").filter(Boolean)]);
  }, [liveEmo?.date, liveStockCodes, session?.phase]);

  // 下方是上一交易日 N 的名单，但报价和个股灯观察 N+1 的当前状态。
  useEffect(() => {
    refreshFeedbackTracking(feedbackStockCodes.split(",").filter(Boolean));
  }, [emotion?.date, feedbackStockCodes, session?.phase]);

  useEffect(() => { setSelectedLiveStocks(new Set()); }, [liveEmo?.date]);
  useEffect(() => { setSelectedSettledStocks(new Set()); }, [emotion?.date]);

  // ⭐ 自动刷新。三条不能写错的地方：
  //  ① **交易时段用后端给的 `session.phase` 判断**，不要用 `new Date().getHours()`
  //     —— 那是本机时区，人在海外会盘中不刷、半夜狂刷。后端算的是北京时间。
  //  ② 收盘 / 非交易日**不刷** —— 数字不会动，白打请求还可能撞限流。
  //  ③ cleanup 必须把两个句柄都清掉，且**句柄只属于这一次 effect**。
  //     共享句柄或忘了清，旧的定时器会活下来跟新的并行 → 实际频率翻倍。
  useEffect(() => {
    const live = Boolean(session?.poll);
    if (!autoRefresh || !live) return;

    const liveTimer = setInterval(() => {
      loadLive();
      if (watchCodes.length) refreshWatch(watchCodes);
    }, LIVE_MS);
    const heavyTimer = setInterval(loadHeavy, HEAVY_MS);
    return () => { clearInterval(liveTimer); clearInterval(heavyTimer); };
  }, [autoRefresh, session?.poll, watchCodes, liveStockCodes, feedbackStockCodes]);

  const toggleAuto = () => {
    const next = !autoRefresh;
    setAutoRefresh(next);
    localStorage.setItem(AUTO_KEY, next ? "1" : "0");
  };

  const toggleOverseas = () => {
    setOverseasExpanded((expanded) => {
      const next = !expanded;
      if (next) localStorage.removeItem(overseasCollapseKey(overseasDay));
      else localStorage.setItem(overseasCollapseKey(overseasDay), "1");
      return next;
    });
  };

  const toggleLimitAlerts = () => {
    const next = !limitAlertsEnabled;
    setLimitAlertsEnabled(next);
    localStorage.setItem(LIMIT_ALERT_KEY, next ? "1" : "0");
    if (next && "Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  };

  const toggleFeedbackLimitAlerts = () => {
    const next = !feedbackLimitAlertsEnabled;
    setFeedbackLimitAlertsEnabled(next);
    localStorage.setItem(FEEDBACK_LIMIT_ALERT_KEY, next ? "1" : "0");
    if (next && "Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  };

  const toggleWatchLimitAlerts = () => {
    const next = !watchLimitAlertsEnabled;
    setWatchLimitAlertsEnabled(next);
    localStorage.setItem(WATCH_LIMIT_ALERT_KEY, next ? "1" : "0");
    if (next && "Notification" in window && Notification.permission === "default") {
      void Notification.requestPermission();
    }
  };

  const addWatch = () => {
    // 支持一次粘贴多只（逗号 / 空格分隔）；全部无效或重复则清空输入、无副作用。
    const { next, added } = addCodes(watchCodes, watchInput);
    setWatchInput("");
    if (!added) return;
    setWatchCodes(next); saveWatch(next); refreshWatch(next);
    refreshTraffic([...next, ...liveStockCodes.split(",").filter(Boolean)]);
  };

  const removeWatch = (c: string) => {
    const next = watchCodes.filter((x) => x !== c);
    setWatchCodes(next); saveWatch(next); refreshWatch(next);
    refreshTraffic([...next, ...liveStockCodes.split(",").filter(Boolean)]);
  };

  const today = new Date().toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  // 是否处在会动的时段（后端判定，避免本机时区坑）
  const liveNow = session?.phase === "盘中";
  const feedbackSession = session?.phase === "盘中" || session?.phase === "已收盘";
  // 这张表的名单与静态字段锚在 emotion.date；不要在周末把它们写成含糊的“昨日”。
  const feedbackAsOf = session?.today
    ? `${session.today}${session?.phase === "已收盘" ? " 收盘" : " 实时"}`
    : "当前实时";
  const feedbackLightHeading = session?.phase === "盘中"
    ? "N+1 实时个股灯"
    : session?.phase === "已收盘"
      ? "N+1 收盘个股灯"
      : "N 日收盘个股灯";
  const settledTitle = session?.phase === "非交易日" ? "最近交易日短线情绪" : "上一交易日短线情绪";
  const liveTitle = liveEmo?.available && liveEmo.phase === "已收盘" ? "今日收盘打板情绪" : "今日实时打板情绪";

  useEffect(() => {
    seenLimitEventIds.current = new Set();
    limitAlertsReady.current = false;
    setLimitAlerts(null);
  }, [liveEmo?.date]);

  useEffect(() => {
    const codes = liveStockCodes.split(",").filter(Boolean);
    if (!limitAlertsEnabled || !liveNow || !codes.length) return;
    let active = true;
    const refresh = () => {
      api.limitBreakAlerts(codes).then((snapshot) => {
        if (!active) return;
        setLimitAlerts(snapshot);
        if (!limitAlertsReady.current) {
          snapshot.events.filter((event) => codes.includes(event.code)).forEach((event) => seenLimitEventIds.current.add(event.id));
          limitAlertsReady.current = true;
          return;
        }
        snapshot.events.filter((event) => codes.includes(event.code)).forEach((event) => {
          if (seenLimitEventIds.current.has(event.id)) return;
          seenLimitEventIds.current.add(event.id);
          if (event.kind !== "break") return;
          const message = `${event.name}（${event.code}）${event.at} 炸板，现价 ${event.price}，涨停价 ${event.limit_up}`;
          toast.error(message, { duration: 8000 });
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification("炸板提醒", { body: message });
          }
        });
      }).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, LIVE_MS);
    return () => { active = false; clearInterval(timer); };
  }, [limitAlertsEnabled, liveNow, liveEmo?.date, liveStockCodes]);

  // N 日连板股在 N+1 的炸板监控：独立轮询、独立首次快照和独立通知去重。
  // 仅盘中有效；收盘后的这张表是 N+1 收盘结果，不再产生“实时预警”。
  useEffect(() => {
    seenFeedbackLimitEventIds.current = new Set();
    feedbackLimitAlertsReady.current = false;
    setFeedbackLimitAlerts(null);
  }, [emotion?.date]);

  useEffect(() => {
    const codes = feedbackStockCodes.split(",").filter(Boolean);
    if (!feedbackLimitAlertsEnabled || !liveNow || !codes.length) return;
    let active = true;
    const refresh = () => {
      api.limitBreakAlerts(codes).then((snapshot) => {
        if (!active) return;
        setFeedbackLimitAlerts(snapshot);
        if (!feedbackLimitAlertsReady.current) {
          snapshot.events.filter((event) => codes.includes(event.code)).forEach((event) => seenFeedbackLimitEventIds.current.add(event.id));
          feedbackLimitAlertsReady.current = true;
          return;
        }
        snapshot.events.filter((event) => codes.includes(event.code)).forEach((event) => {
          if (seenFeedbackLimitEventIds.current.has(event.id)) return;
          seenFeedbackLimitEventIds.current.add(event.id);
          if (event.kind !== "break") return;
          const message = `昨日连板跟踪：${event.name}（${event.code}）${event.at} 炸板，现价 ${event.price}，涨停价 ${event.limit_up}`;
          toast.error(message, { duration: 8000 });
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification("昨日连板炸板提醒", { body: message });
          }
        });
      }).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, LIVE_MS);
    return () => { active = false; clearInterval(timer); };
  }, [feedbackLimitAlertsEnabled, liveNow, emotion?.date, feedbackStockCodes]);

  // 自选股与两张连板表分别保留首次快照和通知去重，开关互不影响。
  useEffect(() => {
    seenWatchLimitEventIds.current = new Set();
    watchLimitAlertsReady.current = false;
  }, [watchCodes.join(",")]);

  useEffect(() => {
    if (!watchLimitAlertsEnabled || !liveNow || !watchCodes.length) return;
    let active = true;
    const refresh = () => {
      api.limitBreakAlerts(watchCodes).then((snapshot) => {
        if (!active) return;
        const events = snapshot.events.filter((event) => watchCodes.includes(event.code));
        if (!watchLimitAlertsReady.current) {
          events.forEach((event) => seenWatchLimitEventIds.current.add(event.id));
          watchLimitAlertsReady.current = true;
          return;
        }
        events.forEach((event) => {
          if (seenWatchLimitEventIds.current.has(event.id)) return;
          seenWatchLimitEventIds.current.add(event.id);
          if (event.kind !== "break") return;
          const message = `自选股：${event.name}（${event.code}）${event.at} 炸板，现价 ${event.price}，涨停价 ${event.limit_up}`;
          toast.error(message, { duration: 8000 });
          if ("Notification" in window && Notification.permission === "granted") {
            new Notification("自选股炸板提醒", { body: message });
          }
        });
      }).catch(() => {});
    };
    refresh();
    const timer = setInterval(refresh, LIVE_MS);
    return () => { active = false; clearInterval(timer); };
  }, [watchLimitAlertsEnabled, liveNow, watchCodes]);

  const dataSummary = indices.length
    ? indices.map((i) => `${i.name} ${i.price}（${i.change_pct > 0 ? "+" : ""}${i.change_pct}%）`).join("；")
    : "（指数数据未取到）";

  // 实时与收盘分析必须使用不同命名空间；同一只股票不能互相覆盖上下文与结果。
  const dd = useDeepDive("lianban-settled", emotion?.date || "");
  const liveDd = useDeepDive("lianban-live", liveEmo?.date || "");
  const emotionExport = buildDailyEmotionExport(emotion, dd, feedbackTraffic);
  const selectedEmotionExport = buildDailyEmotionExport(emotion, dd, feedbackTraffic, { selectedCodes: selectedSettledStocks });
  const downloadEmotionExport = (exported: NonNullable<typeof emotionExport>) => {
    const url = URL.createObjectURL(new Blob([exported.content], { type: "text/markdown;charset=utf-8" }));
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = exported.filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  const settledLianbanPrompt = (s: LianbanStock) =>
    `${emotion?.date || ""}（已收盘）A 股连板股「${s.name}（${s.code}）」的客观数据：\n` +
    `该日收盘 ${s.price} 元、涨停 +${s.pct}%，已连续涨停 ${s.boards} 天（${s.boards} 连板），` +
    `成交额 ${yi(s.amount)}，流通市值 ${yi(s.float_cap)}，所属概念/行业 ${s.industry || "未知"}，` +
    `涨停原因题材：${s.reason || "（暂缺，需要自查）"}。\n\n` +
    "请深入分析这只股票本轮连板的驱动：\n" +
    `1. 先调用工具查询这只股票的近期新闻与研报；调用 query_news / query_reports 时必须传 end_date=${emotion?.date || ""}，结合上面的题材串，说清本轮连板的核心驱动（消息面 / 题材面 / 资金面），以及走到第 ` +
    `${s.boards} 板的位置上驱动有没有变化；\n` +
    "2. 就**这个题材板块整体**说清它的强度与所处阶段（情绪接力 / 有产业逻辑或业绩支撑，发酵期 / 分歧期），" +
    "并给出依据 —— 只讲题材板块层面，不要由此推断这只个股接下来会怎样；\n" +
    "3. 客观列出值得注意的点（连板高度、成交额是放大还是缩量、流通盘大小、题材扩散位置）。\n" +
    "4. 查询该股近期龙虎榜（优先该交易日）：若上榜，必须分别给出“买方席位分析”“卖方席位分析”“买卖双方对照”，逐席列出买入额、卖出额与净额，区分机构专用、沪深股通和营业部，并说明卖压集中度；即使买卖榜出现同名席位也不得省略。任一侧为空时明确写明该侧数据缺失，不得臆测。\n" +
    "5. 对能够被公开资料可靠识别的席位，说明其可能关联的游资、市场公开风评及常见操作风格，并区分已核实事实、市场称谓与推断；无法可靠对应到具体游资时直接写“无法可靠识别”，不得仅凭营业部名称强行归因。\n" +
    "个股层面只陈述已经发生的客观数据与事实，方向与强弱判断做到题材板块层面为止：" +
    "不预测个股涨跌、不给个股参与倾向、不推荐任何标的、不构成投资建议。" +
    "输出用纯 Markdown（不要在表格或正文里使用 <br> 等 HTML 标签）。";
  const liveLianbanPrompt = (s: LianbanStock) =>
    `${liveEmo?.date || ""} ${liveEmo?.as_of || ""}（${liveEmo?.phase || "盘中"}）A 股连板股「${s.name}（${s.code}）」的客观快照：\n` +
    `现价 ${s.price} 元、当日涨幅 ${s.pct > 0 ? "+" : ""}${s.pct}%，当日曾触及 ${s.boards} 板，状态 ${s.close_status || "待确认"}，` +
    `成交额 ${yi(s.amount)}，流通市值 ${yi(s.float_cap)}，所属概念/行业 ${s.industry || "未知"}，` +
    `涨停原因题材：${s.reason || "（暂缺，需要自查）"}。\n\n` +
    "请结合可查询的近期新闻、研报和同题材封板扩散，分析当前已发生的驱动、题材阶段、封板质量与客观风险。" +
    "必须明确这是盘中快照（收盘时则是当日最终状态），不预测后续涨跌、不给参与倾向、不推荐标的。" +
    "输出用纯 Markdown。";
  const lianbanCtx = (s: LianbanStock, mode: "live" | "settled") =>
    `${mode === "live" ? "今日实时触及" : "历史收盘"}连板股 ${s.name}(${s.code}) ${s.boards}板 深入分析`;
  const settledLianbanItem = (s: LianbanStock): DiveItem => ({ key: s.code, prompt: settledLianbanPrompt(s), context: lianbanCtx(s, "settled") });
  const liveLianbanItem = (s: LianbanStock): DiveItem => ({ key: s.code, prompt: liveLianbanPrompt(s), context: lianbanCtx(s, "live") });
  const [liveSort, setLiveSort] = useState<{ key: "boards" | "pct" | "amount" | "float_cap" | "industry"; direction: "asc" | "desc" }>({ key: "boards", direction: "desc" });
  const liveStocks = [...(liveEmo?.lianban_stocks ?? [])].sort((a, b) => {
    if (liveSort.key === "industry") {
      return a.industry.localeCompare(b.industry, "zh-CN") * (liveSort.direction === "asc" ? 1 : -1);
    }
    const av = a[liveSort.key] ?? 0;
    const bv = b[liveSort.key] ?? 0;
    return (Number(av) - Number(bv)) * (liveSort.direction === "asc" ? 1 : -1);
  });
  const sortLive = (key: typeof liveSort.key) => setLiveSort((old) => ({
    key,
    direction: old.key === key && old.direction === "desc" ? "asc" : "desc",
  }));
  const [settledSort, setSettledSort] = useState<"asc" | "desc">("asc");
  const settledStocks = [...(emotion?.lianban_stocks ?? [])].sort((a, b) =>
    a.industry.localeCompare(b.industry, "zh-CN") * (settledSort === "asc" ? 1 : -1),
  );
  const sortSettledIndustry = () => setSettledSort((direction) => direction === "asc" ? "desc" : "asc");
  const liveExportData: ShortTermEmotion | null = liveEmo?.date ? {
    date: liveEmo.date,
    zt_count: liveEmo.zt_count ?? 0,
    dt_count: liveEmo.dt_count ?? null,
    zb_count: liveEmo.zb_count ?? null,
    max_boards: liveEmo.max_boards ?? 0,
    lianban_count: liveEmo.two_board_count ?? liveStocks.length,
    ladder: [],
    lianban_stocks: liveStocks,
    seal_rate: liveEmo.seal_rate ?? null,
    break_rate: liveEmo.break_rate ?? null,
    promotion_rate: liveEmo.promotion_rate ?? null,
    yzt_count: liveEmo.promotion_base ?? null,
  } : null;
  const liveEmotionExport = buildDailyEmotionExport(liveExportData, liveDd, traffic, {
    title: liveEmo?.phase === "已收盘" ? "今日收盘二板情绪" : "今日实时二板情绪",
    filenamePrefix: liveEmo?.phase === "已收盘" ? "今日收盘二板情绪" : "今日实时二板情绪",
    dataLabel: liveEmo?.phase === "已收盘" ? "收盘定稿" : "盘中实时",
    useTrafficLights: true,
  });
  const selectedLiveEmotionExport = buildDailyEmotionExport(liveExportData, liveDd, traffic, {
    selectedCodes: selectedLiveStocks,
    title: liveEmo?.phase === "已收盘" ? "今日收盘二板情绪" : "今日实时二板情绪",
    filenamePrefix: liveEmo?.phase === "已收盘" ? "今日收盘二板情绪" : "今日实时二板情绪",
    dataLabel: liveEmo?.phase === "已收盘" ? "收盘定稿" : "盘中实时",
    useTrafficLights: true,
  });

  const sentiment = overview?.sentiment;
  const sectors = overview?.sectors || [];
  const sentCells = sentiment ? [
    { k: "上涨家数", v: sentiment.up, up: true },
    { k: "下跌家数", v: sentiment.down, up: false },
    { k: "平盘", v: sentiment.flat, up: null },
    { k: "涨停", v: sentiment.zt, up: true },
    { k: "真实涨停", v: sentiment.zt_real, up: true },
    { k: "跌停", v: sentiment.dt, up: false },
    { k: "真实跌停", v: sentiment.dt_real, up: false },
    { k: "活跃度", v: sentiment.active, up: null },
  ] : [];
  const marketAsOf = liveEmo?.date && liveEmo?.as_of
    ? `${liveEmo.date} ${liveEmo.as_of}`
    : session?.now || "时间暂缺";
  const liveEmotionSummary = liveEmo?.available ? [
    `涨停 ${liveEmo.zt_count ?? "—"} 家`, `跌停 ${liveEmo.dt_count ?? "—"} 家`,
    `炸板 ${liveEmo.zb_count ?? "—"} 家`, `最高触及 ${liveEmo.max_boards ?? "—"} 板`,
    `封板率 ${liveEmo.seal_rate == null ? "—" : `${(liveEmo.seal_rate * 100).toFixed(1)}%`}`,
    `炸板率 ${liveEmo.break_rate == null ? "—" : `${(liveEmo.break_rate * 100).toFixed(1)}%`}`,
    `晋级率 ${liveEmo.promotion_rate == null ? "—" : `${(liveEmo.promotion_rate * 100).toFixed(1)}%`}`,
  ].join("；") : `不可用（${liveEmo?.reason || "数据尚未返回"}）`;
  const lianbanSummary = (liveEmo?.lianban_stocks ?? []).length
    ? (liveEmo?.lianban_stocks ?? []).slice(0, 15).map((stock) =>
      `${stock.name}(${stock.code}) ${stock.boards}板 ${stock.pct > 0 ? "+" : ""}${stock.pct}% ${stock.close_status || "状态待确认"} [${stock.industry || "行业未知"}]`).join("；")
    : "暂无当前连板明细";
  const sectorFlowSummary = sectors.length ? (() => {
    const ranked = [...sectors].sort((a, b) => b.net - a.net);
    const inflow = ranked.filter((sector) => sector.net > 0).slice(0, 5)
      .map((sector) => `${sector.name} +${fmt(sector.net)}亿`).join("、") || "无";
    const outflow = ranked.filter((sector) => sector.net < 0).slice(-5).reverse()
      .map((sector) => `${sector.name} ${fmt(sector.net)}亿`).join("、") || "无";
    return `净流入前列：${inflow}；净流出前列：${outflow}`;
  })() : "板块资金暂不可用";
  const marketAiContext = [
    `盘面上下文时间：${marketAsOf}`,
    `交易会话：${session?.label || "会话状态暂缺"}；阶段 ${session?.phase || "未知"}`,
    `行情所属交易日：${session?.quotes_of || "未知"}；北京时间 ${session?.now || "未知"}`,
    `指数：${dataSummary}`,
    `实时情绪：${liveEmotionSummary}`,
    `当前连板：${lianbanSummary}`,
    `板块资金（更新于 ${overview?.updated || "未知"}）：${sectorFlowSummary}`,
    "口径：以上是页面当前快照；盘中数据会变化，回答必须引用上述时间，不得当作收盘定稿。",
  ].join("\n");

  return (
    <div>
      <PageHeader
        title="盘面数据"
        subtitle={`${session?.label ?? today} · 指数 / 外围 / 板块资金一屏看全（复盘统计在「复盘看板」）`}
        actions={
          <div className="flex items-center gap-2">
            {/* 开着但不在交易时段时要说清「为什么不动」——否则会被当成坏了 */}
            <button
              onClick={toggleAuto}
              title={autoRefresh
                ? `已开：指数/外围/自选每 ${LIVE_MS / 1000} 秒、板块资金与成交额榜每 ${HEAVY_MS / 1000} 秒。只在盘中生效`
                : "开启后在交易时段自动刷新行情"}
              className={cn(
                "inline-flex items-center gap-1.5 rounded-lg px-3 py-2 text-sm transition-colors",
                autoRefresh
                  ? "bg-primary/15 text-primary hover:bg-primary/25"
                  : "text-muted-foreground hover:text-primary",
              )}
            >
              <RefreshCw className={cn("h-3.5 w-3.5", autoRefresh && liveNow && "animate-spin")} />
              {autoRefresh ? (liveNow ? `每 ${LIVE_MS / 1000} 秒自动刷新` : "自动刷新（非交易时段暂停）") : "自动刷新"}
            </button>
            <AskAiButton
              context={marketAiContext}
              label="问 AI"
              suggestions={["今天大盘怎么走", "哪些指数领涨领跌", "盘面有什么值得注意"]}
            />
          </div>
        }
      />

      {liveNow && <div className="mb-6"><BoardTrafficLightPanel light={traffic} live /></div>}

      {/* 1. 大盘指数（实时） */}
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <h3 className="text-sm font-semibold text-muted-foreground">大盘指数</h3>
          <Caliber text={
            "涨跌幅对比前一交易日收盘。\n" +
        "「实时」是延时行情，页面上没标截至几点。"
          } />
          {session && (
            <span className={cn("text-[11px]", session.is_today ? "text-muted-foreground/50" : "text-warning")}>
              {session.label}
            </span>
          )}
          {/* 集合竞价（09:15-09:25）还没成交，指数等于昨收、涨跌幅是 0 */}
          {session?.phase === "集合竞价" && (
            <span className="text-[11px] text-muted-foreground/50">还没成交，涨跌幅为 0 是正常的</span>
          )}
        </div>
        <button onClick={loadIndices} className="text-muted-foreground hover:text-primary" title="刷新"><RefreshCw className="h-3.5 w-3.5" /></button>
      </div>
      <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {indices.length === 0
          ? [1, 2, 3, 4].map((i) => (
              <GlassCard key={i} className="p-3">
                <p className="text-xs text-muted-foreground">{idxErr ? "行情未接通" : "加载中…"}</p>
                <p className="mt-1 font-mono text-lg font-bold text-muted-foreground/40">—</p>
              </GlassCard>
            ))
          : indices.map((i) => (
              <GlassCard key={i.name} className="p-3">
                <p className="truncate text-xs text-muted-foreground">{i.name}</p>
                <p className={cn("mt-1 font-mono text-lg font-bold", pctColor(i.change_pct))}>{i.price}</p>
                <p className={cn("text-xs", pctColor(i.change_pct))}>{i.change_pct > 0 ? "+" : ""}{i.change_pct}%</p>
              </GlassCard>
            ))}
      </div>

      {/* 1b. 隔夜外围：美股 / 港股指数 + 美股七姐妹 */}
      {(oversea?.available || globalIdx.length > 0) && (() => {
        // 优先用带交易日的新接口；它挂了才退回 vr 那份（没有日期，只能不标）
        const rows: OverseasRow[] = oversea?.available && oversea.indices?.length
          ? oversea.indices
          : globalIdx.map((g) => ({ name: g.name, price: g.price ?? 0, change_pct: g.change_pct ?? 0,
                                    session: null, region: g.region }));
        const us = rows.filter((r) => r.region === "美股");
        const hk = rows.filter((r) => r.region !== "美股");
        const mag7 = oversea?.available ? (oversea.mag7 ?? []) : [];
        const cell = (r: OverseasRow, sub?: string) => (
          <GlassCard key={r.name} className="p-3">
            <p className="truncate text-xs text-muted-foreground">
              {r.name}{sub && <span className="ml-1 font-mono text-[10px] text-muted-foreground/40">{sub}</span>}
            </p>
            <p className={cn("mt-1 font-mono text-lg font-bold", pctColor(r.change_pct))}>{r.price}</p>
            <p className={cn("text-xs", pctColor(r.change_pct))}>
              {r.change_pct > 0 ? "+" : ""}{r.change_pct}%
            </p>
          </GlassCard>
        );
        return (
          <>
            <div className="mb-3 flex flex-wrap items-baseline gap-2">
              <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Globe className="h-4 w-4" /> 隔夜外围</h3>
              <Caliber text={
                "美股港股的涨跌幅都是对比它们各自的前一交易日收盘。\n" +
            "港股在北京时间白天可能正在交易，所以会标「盘中」——那是抓取那一刻的延时行情，没标具体几点。"
              } />
              <span className="text-[11px] text-muted-foreground/50">A 股常看美股 / 港股脸色</span>
              {/* 标签由后端给（含盘前/盘中/收盘）—— 前端别自己拼"收盘"，
                  港股在北京白天可能正在交易，那时候标"收盘"就是错的 */}
              {oversea?.us_label && <span className="text-[11px] text-warning">{oversea.us_label}</span>}
              {oversea?.hk_label && <span className="text-[11px] text-warning">{oversea.hk_label}</span>}
              <button type="button" onClick={toggleOverseas} aria-expanded={overseasExpanded}
                aria-label={overseasExpanded ? "折叠隔夜外围" : "展开隔夜外围"}
                className="ml-auto inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground">
                {overseasExpanded ? "折叠" : "展开"}<ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !overseasExpanded && "-rotate-90")} />
              </button>
            </div>

            {overseasExpanded && us.length > 0 && (
              <div className="mb-3 grid grid-cols-2 gap-3 sm:grid-cols-3">{us.map((r) => cell(r))}</div>
            )}

            {overseasExpanded && mag7.length > 0 && (
              <>
                <p className="mb-2 text-[11px] text-muted-foreground/60">
                  美股七姐妹 · 权重股带指数走，看它们比看指数细
                </p>
                <div className="mb-4 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-7">
                  {mag7.map((r) => cell(r, r.ticker))}
                </div>
              </>
            )}

            {overseasExpanded && hk.length > 0 && (
              <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3">{hk.map((r) => cell(r))}</div>
            )}
          </>
        );
      })()}

      {/* 2. 关注股票（自选） */}
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-sm font-semibold text-muted-foreground">关注股票</h3>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={toggleWatchLimitAlerts}
            disabled={!liveNow || !watchCodes.length}
            aria-label={watchLimitAlertsEnabled ? "关闭自选股炸板预警" : "开启自选股炸板预警"}
            title={liveNow ? "仅盘中监测自选股先封板后炸板" : "仅盘中可开启，收盘后自动停止"}
            className={cn("inline-flex h-8 items-center gap-1 rounded border px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
              watchLimitAlertsEnabled ? "border-danger/50 bg-danger/10 text-danger" : "border-border text-muted-foreground hover:text-primary")}
          >
            {watchLimitAlertsEnabled ? <BellRing className="h-3.5 w-3.5" /> : <Bell className="h-3.5 w-3.5" />}
            {watchLimitAlertsEnabled ? "自选股炸板预警中" : "自选股炸板预警"}
          </button>
          {watchCodes.length > 0 && (
            <button onClick={() => refreshWatch(watchCodes)} className="text-muted-foreground hover:text-primary" title="刷新价格">
              {watchLoading ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            </button>
          )}
        </div>
      </div>
      <GlassCard className="mb-6">
        <div className="mb-3 flex gap-2">
          <input
            value={watchInput}
            onChange={(e) => setWatchInput(e.target.value.replace(/[^\d,\s]/g, "").slice(0, 80))}
            onKeyDown={(e) => e.key === "Enter" && addWatch()}
            placeholder="加自选：可批量，如 600519 000858"
            className="min-w-0 flex-1 rounded-lg border border-border bg-black/20 px-3 py-2 text-sm outline-none focus:border-primary/50 sm:w-60 sm:flex-none"
          />
          <button onClick={addWatch}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-lg bg-primary/15 px-4 py-2 text-sm font-medium text-primary shadow-glow hover:bg-primary/25">
            <Plus className="h-4 w-4" /> 增加
          </button>
        </div>
        {watchCodes.length === 0 ? (
          <p className="text-sm text-muted-foreground/60">加上你关注的股票，随时看它们的实时价格与涨跌。数据存本地，不上传。</p>
        ) : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
            {watchCodes.map((c) => {
              const q = watchQuotes[c];
              return (
                <div key={c} className="group relative rounded-lg bg-muted/25 p-3">
                  <button onClick={() => removeWatch(c)} title="移除"
                    className="absolute right-1.5 top-1.5 text-muted-foreground/40 opacity-0 transition-opacity hover:text-destructive group-hover:opacity-100">
                    <X className="h-3.5 w-3.5" />
                  </button>
                  {}
                  {(() => {
                    const ok = q != null && Number.isFinite(q.price) && q.price > 0;
                    return (
                      <>
                        <p className="truncate text-xs text-muted-foreground">
                          <StockLink code={c}>{q?.name || c}</StockLink>
                        </p>
                        <p className={cn("mt-1 font-mono text-lg font-bold",
                          ok ? pctColor(q!.change_pct) : "text-muted-foreground/40")}>
                          {ok ? q!.price : "—"}
                        </p>
                        <p className={cn("text-xs", ok ? pctColor(q!.change_pct) : "text-muted-foreground/40")}>
                          {ok ? `${q!.change_pct > 0 ? "+" : ""}${q!.change_pct}%` : "行情不可用"}
                        </p>
                        <div className="mt-2"><StockTrafficBadge light={traffic?.stocks?.[c]} /></div>
                      </>
                    );
                  })()}
                </div>
              );
            })}
          </div>
        )}
      </GlassCard>

      {/* 4. 市场宽度与涨跌停统计：只展示原始事实，不再输出独立情绪结论 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Gauge className="h-4 w-4" /> 市场宽度与涨跌停统计</h3>
        <Caliber text={
          "涨停 / 真实涨停 / 跌停 / 真实跌停 / 活跃度这几个数取自乐咕乐股，不是我们算的。\n" +
          "「真实」与普通涨跌停的差额由它自己的口径决定，具体算法未公开。\n" +
          "活跃度同样是它的算法，**不能按上涨家数占比来理解**（今天 1786/5197 = 34.4%，它给的是 34.33%）。\n" +
          "大盘宽度按涨跌家数机械分档：上涨不足 600 家为冰点，其余看 上涨÷下跌 的比值\n" +
          "（<0.7 偏弱 / 0.7-1.2 中性 / 1.2-2.5 偏强 / ≥2.5 普涨）。"
        } />
        {sentiment?.date && <span className="text-[11px] text-muted-foreground/50">{sentiment.date}</span>}
      </div>
      <GlassCard className="mb-6">
        {!sentiment?.breadth ? (
          pending(ovDone, ovError)
        ) : (
          <>
            <div className="grid gap-3">
              {[
                { k: "大盘宽度", v: sentiment.breadth, hint: "冰点 / 偏弱 / 中性 / 偏强 / 普涨" },
              ].map((m) => (
                <div key={m.k} className="rounded-lg bg-muted/25 p-4">
                  <p className="text-xs text-muted-foreground">{m.k}</p>
                  <p className="mt-1 text-2xl font-bold text-primary">{m.v}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground/60">{m.hint}</p>
                </div>
              ))}
            </div>
            <div className="mt-3 grid grid-cols-4 gap-2">
              {sentCells.map((c) => (
                <div key={c.k} className="rounded-lg bg-muted/20 p-2 text-center">
                  <p className="truncate text-[11px] text-muted-foreground">{c.k}</p>
                  <p className={cn("mt-0.5 font-mono text-sm font-bold", c.up === null ? "text-foreground" : c.up ? "text-danger" : "text-success")}>{c.v}</p>
                </div>
              ))}
            </div>
          </>
        )}
      </GlassCard>

      {/* 4a. 今日实时打板情绪 —— 这页是盘面数据，得有今天的 */}
      {!liveEmo && liveError && <GlassCard className="mb-6">{pending(true, liveError)}</GlassCard>}
      {liveEmo && (
        <>
          <div className="mb-3 flex flex-wrap items-baseline gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground">
              <Flame className="h-4 w-4" /> {liveTitle}
              <Caliber text={
                "封板率 = 最终封住家数 ÷ 摸板家数；炸板率 = 炸板未回封家数 ÷ 摸板家数。\n" +
                "摸板家数 = 涨停 + 炸板，**按家数算，不按炸板次数算**。\n" +
                "实时梯队按当日曾触及的连板高度统计：当前封板与触板后炸板都计入。"
              } />
            </h3>
            {liveEmo.available ? (
              <span className="text-[11px] text-warning">
                {liveEmo.date} {liveEmo.as_of} · {liveEmo.phase}（随盘变化）
              </span>
            ) : (
              <span className="text-[11px] text-muted-foreground/50">{liveEmo.reason}</span>
            )}
          </div>
          <GlassCard className="mb-6">
            {!liveEmo.available ? (
              <p className="py-4 text-center text-sm text-muted-foreground/60">
                {liveEmo.reason || "今日暂无数据"}
              </p>
            ) : (
              <>
              <EmotionRegimePanel regime={liveEmo.emotion_regime} />
              <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-4">
                {[
                  { k: "涨停", v: liveEmo.zt_count, unit: "", up: true },
                  { k: "跌停", v: liveEmo.dt_count, unit: "", up: false },
                  { k: "最高触及连板", v: liveEmo.max_boards, unit: " 板", up: true },
                  { k: "触及二板", v: liveEmo.two_board_count ?? liveStocks.length, unit: " 家", up: true },
                ].map((c) => (
                  <div key={c.k} className="rounded-lg bg-muted/30 p-3 text-center">
                    <p className="text-xs text-muted-foreground">{c.k}</p>
                    <p className={cn("mt-1 font-mono text-xl font-bold",
                                     c.v == null ? "text-muted-foreground/40"
                                                 : c.up ? "text-danger" : "text-success")}>
                      {c.v ?? "—"}{c.v != null && c.unit}
                    </p>
                  </div>
                ))}
                {[
                  { k: "封板率", v: liveEmo.seal_rate, sub: "封住 / 尝试涨停", up: true },
                  { k: "炸板率", v: liveEmo.break_rate, sub: "炸板 / 尝试涨停", up: false },
                  {
                    k: "晋级率", v: liveEmo.promotion_rate, up: true,
                    sub: liveEmo.promotion_base != null
                      ? `${liveEmo.promotion_base_date || "上一场"} 涨停 ${liveEmo.promotion_base} 家，今天又封住`
                      : "上一场涨停的票，今天又封住",
                  },
                  { k: "炸板家数", v: null as number | null, raw: liveEmo.zb_count, sub: "炸板未回封", up: false },
                ].map((c) => (
                  <div key={c.k} className="rounded-lg bg-muted/30 p-3 text-center">
                    <p className="text-xs text-muted-foreground">{c.k}</p>
                    <p className={cn("mt-1 font-mono text-xl font-bold",
                                     (c.raw ?? c.v) == null ? "text-muted-foreground/40"
                                                            : c.up ? "text-danger" : "text-success")}>
                      {c.raw != null ? c.raw
                        : c.v != null ? `${(c.v * 100).toFixed(1)}%` : "—"}
                    </p>
                    <p className="mt-0.5 text-[10px] text-muted-foreground/50">{c.sub}</p>
                  </div>
                ))}
              </div>
              <div className="mt-4 border-t border-border/50 pt-3">
                <div className="mb-2 flex flex-wrap items-center gap-2">
                  <p className="text-[11px] text-muted-foreground">
                    今日恰好二板（含触及二板后炸板）· 不含3板及以上，{liveNow ? "盘中实时变化" : "收盘后收敛"}
                  </p>
                  <span className="rounded border border-primary/30 bg-primary/5 px-2 py-1 text-[10px] font-semibold text-primary">策略：2板→3板</span>
                  <button
                    type="button"
                    onClick={() => setLiveDetailsExpanded((expanded) => !expanded)}
                    aria-expanded={liveDetailsExpanded}
                    aria-label={liveDetailsExpanded ? "折叠今日触及连板明细" : "展开今日触及连板明细"}
                    className="inline-flex h-8 items-center gap-1 rounded border border-border px-2 text-xs text-muted-foreground transition-colors hover:text-primary"
                  >
                    {liveDetailsExpanded ? "折叠明细" : "展开明细"}
                    <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", !liveDetailsExpanded && "-rotate-90")} />
                  </button>
                  <button
                    type="button"
                    onClick={toggleLimitAlerts}
                    disabled={!liveNow || !liveStocks.length}
                    aria-label={limitAlertsEnabled ? "关闭炸板预警" : "开启炸板预警"}
                    className={cn("inline-flex h-8 items-center gap-1 rounded border px-2 text-xs transition-colors disabled:cursor-not-allowed disabled:opacity-40",
                      limitAlertsEnabled ? "border-danger/50 bg-danger/10 text-danger" : "border-border text-muted-foreground hover:text-primary")}
                  >
                    {limitAlertsEnabled ? <BellRing className="h-3.5 w-3.5" /> : <Bell className="h-3.5 w-3.5" />}
                    {limitAlertsEnabled ? "今日二板炸板预警中" : "今日二板炸板预警"}
                  </button>
                  <span className="ml-auto">
                    <RunAllButton dd={liveDd} items={liveStocks.map(liveLianbanItem)} showAll={false}
                      selectedItems={liveStocks.filter((stock) => selectedLiveStocks.has(stock.code)).map(liveLianbanItem)}
                      nameOf={(key) => liveStocks.find((stock) => stock.code === key)?.name || key} />
                  </span>
                  <span className="text-[11px] text-muted-foreground">已选 {selectedLiveStocks.size} 只</span>
                  <button
                    type="button"
                    onClick={() => selectedLiveEmotionExport && downloadEmotionExport(selectedLiveEmotionExport)}
                    disabled={!selectedLiveStocks.size || !selectedLiveEmotionExport}
                    aria-label="导出已选今日二板情绪"
                    title={selectedLiveEmotionExport ? `导出已选 ${selectedLiveEmotionExport.count} 只` : "请先勾选标的"}
                    className="inline-flex h-8 items-center gap-1 rounded border border-border px-2 text-xs text-muted-foreground hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Download className="h-3.5 w-3.5" /> 导出已选
                  </button>
                  <button
                    type="button"
                    onClick={() => liveEmotionExport && downloadEmotionExport(liveEmotionExport)}
                    disabled={!liveEmotionExport}
                    aria-label="导出今日二板情绪"
                    title={liveEmotionExport ? `导出当日二板情绪（${liveEmotionExport.count} 只）` : "实时情绪数据加载后可导出"}
                    className="inline-flex h-8 items-center gap-1 rounded border border-border px-2 text-xs text-muted-foreground hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    <Download className="h-3.5 w-3.5" /> 导出全部
                  </button>
                </div>
                {!liveDetailsExpanded ? null : !liveStocks.length ? (
                  <p className="py-3 text-center text-xs text-muted-foreground/50">今日暂无二板或触及二板后炸板标的</p>
                ) : (
                  <div className={BOARD_TABLE_WRAP}>
                    <table className={BOARD_TABLE}>
                      <colgroup>{LIVE_BOARD_COLUMN_WIDTHS.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
                      <thead className={BOARD_TABLE_HEAD}>
                        <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                          <th className="w-9 px-2 py-2">
                            <input
                              type="checkbox"
                              aria-label="选择全部今日二板标的"
                              checked={liveStocks.length > 0 && selectedLiveStocks.size === liveStocks.length}
                              onChange={(event) => setSelectedLiveStocks(event.target.checked ? new Set(liveStocks.map((stock) => stock.code)) : new Set())}
                              className="h-4 w-4 align-middle accent-primary"
                            />
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">名称</th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">
                            <button onClick={() => sortLive("boards")} className="inline-flex items-center gap-1 hover:text-primary">
                              连板<ArrowDownUp className="h-3 w-3" />
                            </button>
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">个股灯</th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">现价</th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">
                            <button onClick={() => sortLive("pct")} className="inline-flex items-center gap-1 hover:text-primary">今日涨幅<ArrowDownUp className="h-3 w-3" /></button>
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">封板 / 炸板</th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">
                            <button onClick={() => sortLive("amount")} className="inline-flex items-center gap-1 hover:text-primary">成交额<ArrowDownUp className="h-3 w-3" /></button>
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">
                            <button onClick={() => sortLive("float_cap")} className="inline-flex items-center gap-1 hover:text-primary">流通市值<ArrowDownUp className="h-3 w-3" /></button>
                          </th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium">涨停原因</th>
                          <th className="whitespace-nowrap px-2 py-2 font-medium" aria-sort={liveSort.key === "industry" ? (liveSort.direction === "asc" ? "ascending" : "descending") : "none"}>
                            <button onClick={() => sortLive("industry")} aria-label="概念排序" className="inline-flex items-center gap-1 hover:text-primary">概念<ArrowDownUp className="h-3 w-3" /></button>
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {liveStocks.map((s) => {
                          const alert = limitAlerts?.states[s.code];
                          return (
                            <Fragment key={s.code}>
                              <tr className="border-b border-border/30">
                                <td className="w-9 px-2 py-2">
                                  <input
                                    type="checkbox"
                                    aria-label={`选择今日二板 ${s.name} ${s.code}`}
                                    checked={selectedLiveStocks.has(s.code)}
                                    onChange={(event) => setSelectedLiveStocks((current) => {
                                      const next = new Set(current);
                                      if (event.target.checked) next.add(s.code); else next.delete(s.code);
                                      return next;
                                    })}
                                    className="h-4 w-4 align-middle accent-primary"
                                  />
                                </td>
                                <td className="px-2 py-2">
                                  <div className="whitespace-nowrap"><StockLink code={s.code} className="font-medium">{s.name}</StockLink>{" "}<StockLink code={s.code} className="text-xs text-muted-foreground/50">{s.code}</StockLink></div>
                                  <button onClick={() => liveDd.toggle(liveLianbanItem(s))}
                                    disabled={liveDd.busy && !liveDd.analysis[s.code] && !liveDd.runningKeys.includes(s.code) && !liveDd.queuedKeys.includes(s.code)}
                                    className={cn("mt-1 inline-flex items-center gap-1 rounded border border-primary/40 px-1.5 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40", liveDd.completed[s.code] && "border-success/50 bg-success/10 text-success hover:bg-success/20")}>
                                    {liveDd.runningKeys.includes(s.code) ? <Loader2 className="h-3 w-3 animate-spin" /> : liveDd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                                    {liveDd.open === s.code ? "收起分析" : liveDd.completed[s.code] ? "查看分析" : "深入分析"}
                                  </button>
                                </td>
                                <td className="whitespace-nowrap px-2 py-2 font-mono font-bold text-primary">
                                  <span className="block">{s.boards} 板</span>
                                  {s.is_sealed === false && <span className="mt-0.5 block text-[10px] font-normal text-warning">触及未封板</span>}
                                </td>
                                <td className="whitespace-nowrap px-2 py-2"><StockTrafficBadge light={traffic?.stocks?.[s.code] || s.traffic_light} /></td>
                                <td className="whitespace-nowrap px-2 py-2 font-mono">{s.price || "—"}</td>
                                <td className={cn("whitespace-nowrap px-2 py-2 font-mono", pctColor(finite(s.pct)))}>{finite(s.pct) == null ? "—" : `${s.pct > 0 ? "+" : ""}${s.pct}%`}</td>
                                <td className="whitespace-nowrap px-2 py-2 text-xs">
                                  {!liveNow ? <span className={s.is_sealed ? "font-medium text-success" : "font-medium text-danger"}>{closeBoardResult(s.is_sealed, s.break_count)}</span>
                                    : <span className={alert?.status === "break" || s.is_sealed === false ? "font-medium text-danger" : s.is_sealed ? "font-medium text-success" : "text-muted-foreground/70"}>{liveBoardResult(s.is_sealed, s.break_count, alert)}</span>}
                                </td>
                                <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.amount)}</td>
                                <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.float_cap)}</td>
                                <td className="max-w-56 px-2 py-2 text-xs">{s.reason || <span className="text-muted-foreground/50">—</span>}</td>
                                <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{s.industry || "—"}</td>
                              </tr>
                              {liveDd.open === s.code && <DeepDivePanel dd={liveDd} stockKey={s.code} colSpan={11}
                                noteTitle={`实时连板深析 · ${s.name} ${s.boards}板`} onRerun={() => liveDd.rerun(liveLianbanItem(s))} />}
                            </Fragment>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
              </>
            )}
          </GlassCard>
        </>
      )}

      {/* 4b. 前一/最近交易日收盘定稿；绝不叠加今天的实时状态 */}
        <div className="mb-3 flex flex-wrap items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><Flame className="h-4 w-4" /> {settledTitle}</h3>
        <Caliber text={
          "封板率 / 炸板率与上面那张实时卡同一个算法：分母是摸板家数（涨停 + 炸板），按家数不按次数。\n" +
          "表里的「行业 / 概念」经常只有四个字（像「互联网电」「汽车零部」）——是上游把名字截到四字，\n" +
          "不是这里显示不全；怕猜错所以不替它补全称。"
        } />
        <span className="text-[11px] text-muted-foreground/50">前一/最近交易日收盘定稿 · 连板股 · 客观公开榜单</span>
        {/* 这块锚在**已收盘那一场**（复盘口径），不跟上面的实时行情一起刷 ——
            不标出来会和上面的实时块混在一起，让人以为它也在跳 */}
        {emotion?.date && (
          <span className={cn("text-[11px] sm:ml-auto", emotion.data_status === "stale" ? "text-warning" : "text-muted-foreground/50")}>
            数据日期 {emotion.source_date || emotion.date} · 本次抓取 {emotion.updated || "—"}
          </span>
        )}
        <button onClick={() => loadSettled(true)} title="重新确认收盘榜单" aria-label="刷新收盘短线情绪"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary">
          <RefreshCw className="h-3.5 w-3.5" />
        </button>
        <button
          type="button"
          onClick={() => selectedEmotionExport && downloadEmotionExport(selectedEmotionExport)}
          disabled={!selectedSettledStocks.size || !selectedEmotionExport}
          aria-label="导出已选短线情绪复盘"
          title={selectedEmotionExport ? `导出已选 ${selectedEmotionExport.count} 只` : "请先勾选标的"}
          className="inline-flex h-8 items-center gap-1 rounded border border-border px-2 text-xs text-muted-foreground transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Download className="h-3.5 w-3.5" /> 已选
        </button>
        <button
          type="button"
          onClick={() => emotionExport && downloadEmotionExport(emotionExport)}
          disabled={!emotionExport}
          aria-label="导出短线情绪复盘"
          title={emotionExport ? `导出 ${emotionExport.filename}（${emotionExport.count} 只，含 ${emotionExport.analysisCount} 只已完成分析）` : "短线情绪数据加载后可导出"}
          className="inline-flex h-8 items-center gap-1 rounded border border-border px-2 text-xs text-muted-foreground transition-colors hover:text-primary disabled:cursor-not-allowed disabled:opacity-40"
        >
          <Download className="h-3.5 w-3.5" /> 全部
        </button>
      </div>
      <GlassCard className="mb-6">
        {!emotion || emotion.zt_count === undefined ? (
          emotion?.data_note ? <p className="py-4 text-center text-sm text-muted-foreground/70">{emotion.data_note}</p> : pending(emoDone, emoError)
        ) : (
          <>
            {/* 关键计数 */}
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {[
                { k: "涨停", v: emotion.zt_count, cls: "text-danger" },
                { k: "跌停", v: emotion.dt_count, cls: "text-success" },
                { k: "最高连板", v: `${emotion.max_boards} 板`, cls: "text-primary" },
                { k: "连板（2板+）", v: `${emotion.lianban_count} 家`, cls: "text-primary" },
              ].map((c) => (
                <div key={c.k} className="rounded-lg bg-muted/25 p-3 text-center">
                  <p className="text-[11px] text-muted-foreground">{c.k}</p>
                  <p className={cn("mt-0.5 font-mono text-xl font-bold", c.cls)}>{c.v ?? "—"}</p>
                </div>
              ))}
            </div>
            {/* 打板情绪比率 */}
            <div className="mt-2 grid grid-cols-3 gap-2">
              {[
                { k: "封板率", v: emotion.seal_rate, hint: "封住 / 尝试涨停", strong: true },
                { k: "炸板率", v: emotion.break_rate, hint: "炸板 / 尝试涨停", strong: false },
                { k: "晋级率", v: emotion.promotion_rate,
                  hint: `前一交易日涨停的票，${emotion.date} 又封住`, strong: true },
              ].map((c) => (
                <div key={c.k} className="rounded-lg bg-muted/20 p-2.5 text-center">
                  <p className="text-[11px] text-muted-foreground">{c.k}</p>
                  <p className={cn("mt-0.5 font-mono text-sm font-bold", c.strong ? "text-danger" : "text-success")}>
                    {c.v == null ? "—" : `${(c.v * 100).toFixed(1)}%`}
                  </p>
                  <p className="mt-0.5 text-[10px] text-muted-foreground/50">{c.hint}</p>
                </div>
              ))}
            </div>
            {/* N 日收盘名单在 N+1 的跟踪表：统计仍是 N 日定稿，行情/个股灯随当前交易日变动。 */}
            <div className="mt-3">
              <div className="mb-1.5 flex flex-wrap items-center gap-2">
                <p className="text-[11px] text-muted-foreground">
                  {emotion.date} 收盘连板股（2板以上）· 观察 {feedbackAsOf} 表现；客观公开榜单，非推荐 / 非预测
                </p>
                <button
                  onClick={toggleFeedbackLimitAlerts}
                  disabled={!liveNow || emotion.lianban_stocks.length === 0}
                  aria-label={feedbackLimitAlertsEnabled ? "关闭上一交易日连板炸板预警" : "开启上一交易日连板炸板预警"}
                  className={cn("inline-flex items-center gap-1 rounded-md border px-2 py-1 text-[11px] transition-colors disabled:cursor-not-allowed disabled:opacity-45",
                    feedbackLimitAlertsEnabled ? "border-danger/50 bg-danger/10 text-danger" : "border-border text-muted-foreground hover:text-primary")}
                  title={liveNow ? "仅盘中监测上一交易日连板股在 N+1 的炸板" : "仅盘中可开启，收盘后自动停止"}
                >
                  {feedbackLimitAlertsEnabled ? <BellRing className="h-3.5 w-3.5" /> : <Bell className="h-3.5 w-3.5" />}
                  {feedbackLimitAlertsEnabled ? "上一交易日连板炸板预警中" : "上一交易日连板炸板预警"}
                </button>
                <span className="ml-auto">
                  <RunAllButton
                    dd={dd}
                    items={emotion.lianban_stocks.map(settledLianbanItem)}
                    selectedItems={emotion.lianban_stocks.filter((stock) => selectedSettledStocks.has(stock.code)).map(settledLianbanItem)}
                    showAll={false}
                    nameOf={(k) => emotion.lianban_stocks.find((s) => s.code === k)?.name || k}
                  />
                </span>
              </div>
              {emotion.lianban_stocks.length === 0 ? (
                <p className="text-xs text-muted-foreground/50">{emotion.date} 收盘无 2 板以上个股</p>
              ) : (
                <div className={BOARD_TABLE_WRAP}>
                  <table className={BOARD_TABLE}>
                    <colgroup>{SETTLED_BOARD_COLUMN_WIDTHS.map((width, index) => <col key={index} style={{ width }} />)}</colgroup>
                    <thead className={BOARD_TABLE_HEAD}>
                      <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                        <th className="w-9 px-2 py-2">
                          <input
                            type="checkbox"
                            aria-label="选择全部上一交易日连板标的"
                            checked={emotion.lianban_stocks.length > 0 && selectedSettledStocks.size === emotion.lianban_stocks.length}
                            onChange={(event) => setSelectedSettledStocks(event.target.checked ? new Set(emotion.lianban_stocks.map((stock) => stock.code)) : new Set())}
                            className="h-4 w-4 align-middle accent-primary"
                          />
                        </th>
                        {["名称", "连板", feedbackLightHeading, "当前价格", "涨幅", "封板 / 炸板", "成交额", "流通市值", "涨停原因"].map((h) => (
                          <th key={h} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>
                        ))}
                        <th className="whitespace-nowrap px-2 py-2 font-medium" aria-sort={settledSort === "asc" ? "ascending" : "descending"}>
                          <button onClick={sortSettledIndustry} aria-label="概念排序" className="inline-flex items-center gap-1 hover:text-primary">概念<ArrowDownUp className="h-3 w-3" /></button>
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {settledStocks.map((s) => {
                        const quote = feedbackQuotes[s.code];
                        // 周末、节假日和盘前没有 N+1 实时场次，直接展示随 N 日复盘
                        // 归档的收盘灯。不能因为 session 不是“盘中/已收盘”就把已有结论
                        // 丢成“待计算”。进入 N+1 场次后才切换为本场实时/收盘灯。
                        const currentLight = feedbackSession ? feedbackTraffic?.stocks?.[s.code] : s.traffic_light;
                        const feedbackAlert = feedbackLimitAlerts?.states[s.code];
                        return <Fragment key={s.code}>
                          <tr className="border-b border-border/30">
                            <td className="w-9 px-2 py-2">
                              <input
                                type="checkbox"
                                aria-label={`选择上一交易日连板 ${s.name} ${s.code}`}
                                checked={selectedSettledStocks.has(s.code)}
                                onChange={(event) => setSelectedSettledStocks((current) => {
                                  const next = new Set(current);
                                  if (event.target.checked) next.add(s.code); else next.delete(s.code);
                                  return next;
                                })}
                                className="h-4 w-4 align-middle accent-primary"
                              />
                            </td>
                            <td className="px-2 py-2">
                              <div className="whitespace-nowrap"><StockLink code={s.code} className="font-medium">{s.name}</StockLink>{" "}
                              <StockLink code={s.code} className="text-xs text-muted-foreground/50">{s.code}</StockLink></div>
                              <button
                                onClick={() => dd.toggle(settledLianbanItem(s))}
                                disabled={dd.busy && !dd.analysis[s.code] && !dd.runningKeys.includes(s.code) && !dd.queuedKeys.includes(s.code)}
                                className={cn("mt-1 inline-flex items-center gap-1 rounded border border-primary/40 px-1.5 py-0.5 text-[10px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-not-allowed disabled:opacity-40", dd.completed[s.code] && "border-success/50 bg-success/10 text-success hover:bg-success/20")}
                              >
                                {dd.runningKeys.includes(s.code) ? <Loader2 className="h-3 w-3 animate-spin" /> : dd.open === s.code ? <X className="h-3 w-3" /> : <Sparkles className="h-3 w-3" />}
                                {dd.open === s.code ? "收起分析" : dd.errors[s.code] ? "重试分析" : dd.completed[s.code] ? "查看分析" : "深入分析"}
                              </button>
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono font-bold text-primary">{s.boards} 板</td>
                            <td className="whitespace-nowrap px-2 py-2">
                              <StockTrafficBadge light={currentLight} />
                              <span className="mt-0.5 block text-[10px] text-muted-foreground/60">
                                {feedbackSession
                                  ? (currentLight ? `N+1 ${session?.phase === "已收盘" ? "收盘灯" : "实时灯"}` : "当前灯加载中")
                                  : (currentLight ? "N 日收盘灯" : `N 日：${s.close_status || "收盘状态待确认"}`)}
                              </span>
                            </td>
                            <td className="px-2 py-2 font-mono">
                              {quote?.price ?? <span className="text-muted-foreground/50">—</span>}
                            </td>
                            <td className={cn("whitespace-nowrap px-2 py-2 font-mono", pctColor(finite(quote?.change_pct)))}>
                              {finite(quote?.change_pct) == null ? "—" : `${quote!.change_pct > 0 ? "+" : ""}${quote!.change_pct}%`}
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 text-xs">
                              {!liveNow ? <span className={currentLight?.stage === "sealed" || (!feedbackSession && s.close_status?.includes("封板")) ? "font-medium text-success" : "font-medium text-danger"}>{currentLight
                                ? trackedCloseResult(currentLight.stage, feedbackSession ? feedbackAlert?.break_count : s.break_count)
                                : (!feedbackSession ? s.close_status || "状态待确认" : "状态待确认")}</span>
                                : <span className={feedbackAlert?.status === "break" || currentLight?.stage === "not_ready" ? "font-medium text-danger" : currentLight?.stage === "sealed" ? "font-medium text-success" : "text-muted-foreground"}>{feedbackAlert?.status === "break" ? `炸板（${feedbackAlert.break_count} 次）` : feedbackAlert?.status === "sealed" ? "封板" : trackedCloseResult(currentLight?.stage)}</span>}
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.amount)}</td>
                            <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.float_cap)}</td>
                            <td className="max-w-56 px-2 py-2 text-xs">
                              {s.reason ? <span className="text-foreground">{s.reason}</span> : <span className="text-muted-foreground/50">—</span>}
                            </td>
                            <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{s.industry}</td>
                          </tr>
                          {dd.open === s.code && (
                            <DeepDivePanel
                              dd={dd}
                              stockKey={s.code}
                              colSpan={11}
                              noteTitle={`连板深析 · ${s.name} ${s.boards}板`}
                              onRerun={() => dd.rerun(settledLianbanItem(s))}
                            />
                          )}
                        </Fragment>;
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </GlassCard>

      {}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><BarChart3 className="h-4 w-4" /> 全市场成交额 TOP20</h3>
        <Caliber text={
          "沪深京 A 股按当日累计成交额从大到小排。\n" +
          "盘中看到的成交额是「到刷新那一刻为止」的累计值，不是收盘值；总市值按当前价算。\n" +
          "名字前面带 C 的是上市不满一年的次新股标记，不是名字的一部分。"
        } />
        <span className="text-[11px] text-muted-foreground/50">客观公开榜单，非推荐 / 非预测 / 不构成投资建议</span>
        {turnover?.updated && <span className="ml-auto text-[11px] text-muted-foreground/50">参考行情日 {turnover.quote_date || "未确认"} · 抓取于 {turnover.updated}</span>}
      </div>
      <GlassCard className="mb-6">
        {!turnover || turnover.stocks.length === 0 ? (
          pending(toDone, toError || turnover?.reason || "")
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  {["#", "名称", "现价", "涨跌%", "成交额", "总市值", "行业"].map((h) => (
                    <th key={h} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {turnover.stocks.map((s, i) => (
                  <tr key={s.code} className="border-b border-border/30">
                    <td className="px-2 py-2 font-mono text-xs text-muted-foreground/50">{i + 1}</td>
                    <td className="px-2 py-2">
                      <StockLink code={s.code} className="font-medium">{s.name}</StockLink>{" "}
                      <StockLink code={s.code} className="text-xs text-muted-foreground/50">{s.code}</StockLink>
                    </td>
                    <td className="px-2 py-2 font-mono">{s.price ?? "—"}</td>
                    <td className={cn("px-2 py-2 font-mono", s.pct == null ? "text-muted-foreground" : pctColor(s.pct))}>
                      {s.pct == null ? "—" : `${s.pct > 0 ? "+" : ""}${s.pct}%`}
                    </td>
                    <td className="whitespace-nowrap px-2 py-2 font-mono">{yi(s.amount)}</td>
                    <td className="whitespace-nowrap px-2 py-2 font-mono text-muted-foreground">{yi(s.mcap)}</td>
                    <td className="whitespace-nowrap px-2 py-2 text-xs text-muted-foreground">{s.industry}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* 5. 板块资金趋势榜（行业） */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><TrendingUp className="h-4 w-4" /> 板块资金趋势榜</h3>
        <Caliber text={
          "净流入 / 流入 / 流出取自同花顺行业资金流的**盘中即时值**，单位亿元，净流入 = 流入 − 流出。\n" +
          "⚠️ 那边没说明这是主力资金还是全部成交资金，所以**不能当作主力净流入**来读。\n" +
          "涨跌% 是行业整体涨幅；成分股数是这个行业的公司总数，不是上涨家数、也不是涨停家数。"
        } />
        <span className="text-[11px] text-muted-foreground/50">行业 · 按今日净流入排序</span>
      </div>
      <GlassCard className="mb-6">
        {sectors.length === 0 ? (
          pending(ovDone, ovError)
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border/50 text-left text-xs text-muted-foreground">
                  {["行业", "涨跌%", "今日净流入", "流入(亿)", "流出(亿)", "成分股数"].map((h) => (
                    <th key={h} className="whitespace-nowrap px-2 py-2 font-medium">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {sectors.slice(0, 15).map((s) => (
                  <tr key={s.name} className="border-b border-border/30">
                    <td className="px-2 py-2 font-medium">{s.name}</td>
                    <td className={cn("px-2 py-2 font-mono", pctColor(s.pct))}>{s.pct > 0 ? "+" : ""}{s.pct}%</td>
                    <td className={cn("px-2 py-2 font-mono", pctColor(s.net))}>{s.net > 0 ? "+" : ""}{fmt(s.net)} 亿</td>
                    <td className="px-2 py-2 font-mono text-muted-foreground">{fmt(s.inflow)}</td>
                    <td className="px-2 py-2 font-mono text-muted-foreground">{fmt(s.outflow)}</td>
                    <td className="px-2 py-2 font-mono text-muted-foreground">{s.firms}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* 6. 资金轮动 */}
      <div className="mb-3 flex items-center gap-2">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-muted-foreground"><ArrowDownUp className="h-4 w-4" /> 资金轮动</h3>
        <Caliber text={
          "就是上面那张榜的两头：流入榜只放净流入为正的、流出榜只放为负的，各取前六。\n" +
          "某一边不足六个就只列够格的那几个 —— 普跌日往往只有两三个行业真净流入。\n" +
          "口径同上：同花顺行业资金流盘中即时值，不能当主力净流入读。"
        } />
        <span className="text-[11px] text-muted-foreground/50">板块级净流入 / 流出</span>
      </div>
      <div className="mb-2 grid gap-4 md:grid-cols-2">
        {[
          {
            title: "流入 Top", icon: TrendingUp, color: "text-danger",
            rows: sectors.filter((s) => s.net > 0).slice(0, 6),
            empty: "今日没有行业净流入",
          },
          {
            title: "流出 Top", icon: TrendingDown, color: "text-success",
            rows: sectors.filter((s) => s.net < 0)
              .sort((a, b) => a.net - b.net).slice(0, 6),
            empty: "今日没有行业净流出",
          },
        ].map((col) => (
          <GlassCard key={col.title}>
            <h4 className={cn("mb-3 flex items-center gap-1.5 text-sm font-semibold", col.color)}><col.icon className="h-4 w-4" /> {col.title}</h4>
            {col.rows.length === 0 ? (
              ovDone && sectors.length > 0
                ? <p className="py-4 text-center text-sm text-muted-foreground/60">{col.empty}</p>
                : pending(ovDone, ovError)
            ) : (
              <div className="space-y-1.5">
                {col.rows.map((s, i) => (
                  <div key={s.name} className="flex items-center gap-3 border-b border-border/30 pb-1.5 text-sm last:border-0">
                    <span className="w-5 text-xs text-muted-foreground/50">{i + 1}</span>
                    <span className="flex-1 truncate">{s.name}</span>
                    <span className={cn("font-mono text-xs", pctColor(s.pct))}>{s.pct > 0 ? "+" : ""}{s.pct}%</span>
                    <span className={cn("w-20 text-right font-mono text-xs", pctColor(s.net))}>{s.net > 0 ? "+" : ""}{fmt(s.net)} 亿</span>
                  </div>
                ))}
              </div>
            )}
          </GlassCard>
        ))}
      </div>

      <Disclaimer />
    </div>
  );
}
