let monitorWarning: string | undefined;
import type { BoardTrafficLight, EmotionRegime, StockTrafficLight } from "./agent";
import { apiUrl } from "./base";
import { randomId } from "./random-id";
let monitorId: string | undefined;
let registeredWatch = "";
let registeredAt = 0;
// 后端 API 客户端。/api → vite 代理到本仓库的 FastAPI（server.py，默认 8910）。
// 后端未启动或数据源异常时抛 ApiError，页面据此优雅降级。

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
  }
}

// 后端访问密钥（对应后端部署时的 VR_API_KEY，公网部署防蹭用）。只存本地浏览器。
const ACCESS_KEY = "vr-access-key";

export function loadAccessKey(): string {
  try {
    return localStorage.getItem(ACCESS_KEY) || "";
  } catch {
    return "";
  }
}

export function saveAccessKey(key: string) {
  try {
    if (key) localStorage.setItem(ACCESS_KEY, key);
    else localStorage.removeItem(ACCESS_KEY);
  } catch {
    throw new Error("未保存：浏览器禁止存储或空间不足，请检查权限后重试");
  }
}

export function authHeaders(): Record<string, string> {
  const k = loadAccessKey();
  return k ? { Authorization: `Bearer ${k}` } : {};
}

export interface MyReport {
  id: string; name: string; industry: string; size: number; ext: string; ts: number;
}

// 下载/预览研报：带鉴权头 fetch → blob → 触发浏览器下载（<a download> 无法带 Authorization，故走 blob）。
export async function downloadReport(id: string, name: string): Promise<void> {
  const resp = await fetch(apiUrl(`/api/myreports/file/${id}`), { headers: authHeaders() });
  if (!resp.ok) throw new ApiError(`下载失败 HTTP ${resp.status}`, resp.status);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function request<T>(path: string, method: "GET" | "POST" | "PUT" | "DELETE" = "GET", body?: unknown): Promise<T> {
  let resp: Response;
  const headers: Record<string, string> = { ...authHeaders() };
  const opts: RequestInit = { method };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  if (Object.keys(headers).length > 0) opts.headers = headers;
  try {
    resp = await fetch(apiUrl(`/api${path}`), opts);
  } catch {
    throw new ApiError("连接不到后端，请先在项目根目录启动：.venv/bin/python server.py（默认 8910）", 0);
  }
  let payload: any = null;
  try {
    payload = await resp.json();
  } catch {
    /* 非 JSON 响应 */
  }
  if (!resp.ok) {
    if (resp.status === 401) {
      throw new ApiError("后端开启了访问鉴权（VR_API_KEY）：请在「接入 AI」页底部填写后端访问密钥", 401);
    }
    throw new ApiError(payload?.detail || payload?.error || `HTTP ${resp.status}`, resp.status);
  }
  return (payload?.data ?? payload) as T;
}

const get = <T>(path: string) => request<T>(path, "GET");

export interface Quote {
  quote_time?: string | null; amount_wan?: number | null;
  name: string; price: number; last_close: number; change_pct: number;
  pe_ttm: number; pb: number; mcap_yi: number; float_mcap_yi?: number | null; turnover_pct: number;
  limit_up: number; limit_down: number;
}

export interface Valuation {
  quote_time?: string | null;
  name: string; code: string; price: number; mcap_yi: number;
  pe_ttm: number; pb: number;
  eps_26e: number | null; eps_27e: number | null; pe_26e: number | null;
  cagr_pct: number | null; peg: number | null; digest_years: number | null;
  analyst_count: number; forecast_note?: string;
}

export interface Report {
  title: string; publishDate: string; orgSName: string;
  emRatingName?: string; indvInduName?: string; pdfUrl?: string | null;
}

export interface ValMetric {
  as_of?: string;
  current: number; percentile: number; min: number; max: number;
  p20: number; p50: number; p80: number; n: number;
}
export interface ValPercentile {
  gaps?: string[];
  period: string; metrics: { pe_ttm?: ValMetric; pb?: ValMetric };
}

export interface Announcement {
  date: string; title: string; type: string; url: string;
}

export interface Financials {
  period: string | null;
  revenue: string | null; revenue_yoy: string | null;
  net_profit: string | null; net_profit_yoy: string | null;
  eps: string | null; bvps: string | null; roe: string | null;
  gross_margin: string | null; net_margin: string | null; op_cf_ps: string | null;
}

export interface NewsItem {
  新闻标题?: string; 发布时间?: string; 文章来源?: string; 新闻链接?: string;
}

export interface IndexQuote {
  name: string; price: number; change_pct: number; change_amt: number;
}

export interface MarketSentiment {
  up: number; down: number; flat: number; zt: number; zt_real: number; dt: number; dt_real: number;
  active: string; breadth: string; speculation: string; date: string;
}
export interface SectorFlow {
  name: string; pct: number; net: number; inflow: number; outflow: number; firms: number;
}
export interface MarketOverview {
  sentiment: MarketSentiment; sectors: SectorFlow[]; updated: string;
  warnings?: string[];
}

// 短线情绪：连板梯队 / 最高连板 / 炸板率 / 封板率 / 晋级率 / 涨跌停家数 + 连板股清单（客观公开榜单）
export interface EmotionTier { boards: number; count: number; plus: boolean }
export interface LianbanStock {
  code: string; name: string; boards: number;
  price: number; pct: number; amount: number | null; float_cap: number | null; industry: string;
  reason: string;  // 涨停原因题材串（问财；缺 key/失败为空串）
  close_status?: string;
  last_seal_time?: string;
  break_count?: number | null;
  seal_fund?: number | null;
  is_sealed?: boolean;
  traffic_light?: StockTrafficLight;
}

export interface KlineRow {
  datetime?: string; date?: string;
  open: number; close: number; high: number; low: number;
  vol?: number; volume?: number; amount?: number | null;
}

export interface IndustryPeer {
  code: string; name: string; price: number | null; pct: number | null;
  amount: number | null; mcap: number | null; float_cap: number | null;
  industry: string; is_focus?: boolean;
}
export interface IndustryPeers {
  available: boolean; reason?: string;
  board?: { code: string; name: string; basis: "industry" | "concept" | "review_limit_up_sample" | "csrc_industry" };
  quote_date?: string | null; requested_date?: string | null; classification_date?: string | null; focus_code?: string; total?: number;
  degraded?: boolean; preview?: boolean; note?: string;
  members: IndustryPeer[]; source?: string;
}
export interface IntradayPoint { time: string; date?: string; label?: string; price: number; volume: number; pct?: number; }
export interface IntradayLine {
  code: string; date?: string | null; previous_close?: number | null;
  points: IntradayPoint[]; reason?: string;
}
export interface IntradayBundle {
  requested_date?: string | null; days?: number; source?: string;
  series: Record<string, IntradayLine>;
}
export interface ShortTermEmotion {
  available?: boolean;
  date: string;
  source_date?: string;
  updated?: string;
  data_status?: "current" | "partial" | "stale" | string;
  data_note?: string;
  zt_count: number; dt_count: number | null; zb_count: number | null;
  max_boards: number; lianban_count: number;
  ladder: EmotionTier[];
  lianban_stocks: LianbanStock[];
  seal_rate: number | null; break_rate: number | null; promotion_rate: number | null;
  yzt_count: number | null;
}

// 每日盯盘：3 秒实时快照（持仓/自选/500亿大票/三板+/昨日成交前十 + 异动流）
export interface WatchRow {
  code: string; name: string;
  price: number | null; pct: number | null; amount: number | null;
  cost?: number; shares?: number; pnl_pct?: number;   // 持仓行
  boards?: number; is_limit?: boolean;                 // 三板+行
}
export interface MonitorAlert {
  change_pct?: number | null;
  ts: string; code: string; name: string; kind: string; msg: string; sources: string[];
}
export interface YesterdayStock extends WatchRow { sector?: string; status: string; quote_ok: boolean; quote_time?: string | null; }
export interface YesterdayLadder {
  available: boolean; reason?: string; warning?: string; sample_date?: string | null; quote_date?: string | null;
  covered: number; stocks: YesterdayStock[];
}
export interface MonitorSnapshot {
  watch_warning?: string;
  warming_up?: boolean;
  ts: string; phase: "open" | "break" | "closed"; poll_seconds: number;
  holdings: WatchRow[]; holdings_error?: string; watchlist: WatchRow[];
  bigcap: { total: number; top: WatchRow[] };
  lianban3: WatchRow[];
  yesterday_ladder?: YesterdayLadder;
  turnover: { label: string; stocks: WatchRow[] };
  alerts: MonitorAlert[];
}

// 首板分析：今日首板涨停股（连板数=1）+ 涨停原因题材串（客观公开榜单）
export interface FirstBoardStock {
  code: string; name: string;
  price: number; pct: number; amount: number | null; float_cap: number | null;
  industry: string; seal_time: string; break_count: number; reason: string;
  last_seal_time?: string; sealed_minutes?: number | null; seal_fund?: number | null;
  turnover_pct?: number | null; traffic_light?: StockTrafficLight;
  theme_group?: {
    name: string; type: "theme" | "catalyst_only" | "missing"; basis: "event_tree";
    member_count: number; catalysts: Array<{ name: string; member_count: number }>;
  };
}
export interface FirstBoardData {
  date: string; total_zt: number | null; first_count: number | null;
  data_note?: string; finalized?: boolean; persisted_at?: string; retention_days?: number;
  reason_note: string | null;
  stocks: FirstBoardStock[];
}
export interface LimitGene {
  available: boolean; reason?: string; window_days?: number; from?: string; to?: string;
  limit_up_count?: number; touched_limit_count?: number; seal_success_rate?: number | null;
  recent_60_limit_count?: number; stock_character_score?: number; source?: string; note?: string;
}

// 全市场成交额榜（客观公开榜单）
export interface TurnoverStock {
  code: string; name: string;
  price: number | null; pct: number | null;
  amount: number | null; mcap: number | null; float_cap: number | null; industry: string;
}
export interface TurnoverTop { stocks: TurnoverStock[]; updated: string; quote_date?: string; reason?: string }

export interface RadarItem {
  title: string; url: string; time: string; source: string; summary?: string; zh?: string;
}
export interface Industry {
  key: string; name: string; accent: string; total: number; items: RadarItem[];
}
export interface RadarData {
  generated_at: string | null; recent_days: number; industries: Industry[];
  stats: { industries: number; total_sources: number; failed_sources?: number };
}

export interface Holding {
  code: string; name: string; price: number; shares: number; cost: number;
  market_value: number; pnl: number; pnl_pct: number;
}
export interface ClosedPosition {
  code: string; name: string; date: string; price: number; shares: number; cost: number;
  pnl: number; pnl_pct: number;
}
export interface PortfolioData {
  holdings: Holding[];
  totals: { market_value: number; cost: number; pnl: number; pnl_pct: number };
  closed: ClosedPosition[];
  realized_pnl: number;
  updated: string;
}

// 资金面 / 筹码 / 信号（v3.3 并入，均为「用户查的那只股」的公开数据）
export interface MarginRow { date: string; rzye: number; rzmre: number; rzche: number; rqye: number; rqmcl: number; rzrqye: number }
export interface BlockTradeRow { date: string; price: number; close: number; premium_pct: number; vol: number; amount: number; buyer: string; seller: string }
export interface HolderRow { date: string; holder_num: number; change_ratio: number; avg_shares: number }
export interface DividendRow { date: string; bonus_rmb: number; transfer_ratio: number; bonus_ratio: number | null; plan: string }
export interface FundFlowRow { date: string; main_net: number; small_net: number; mid_net: number; large_net: number; super_net: number }
export interface DtSeat { name: string; buy_amt: number; sell_amt: number; net: number }
export interface DragonTiger {
  records: { date: string; reason: string; net_buy: number; turnover: number }[];
  seats: { buy: DtSeat[]; sell: DtSeat[] };
  institution: { buy_amt: number; sell_amt: number; net_amt: number };
}
export interface DragonTigerBranch {
  seat_id: string; category: "institution" | "northbound" | "quant" | "hot_money" | "retail" | "brokerage";
  name: string; buy_wan: number; sell_wan: number; net_wan: number;
  ratio: number; tags: string[]; description: string;
}
export interface DragonTigerBoardStock {
  code: string; name: string; close: number; change_pct: number; turnover_pct: number;
  deal_wan: number; buy_wan: number; sell_wan: number; net_wan: number;
  reasons: string[]; reason: string; concept: string | null; theme_reason: string | null;
  board_label: string | null;
  buy_branches: DragonTigerBranch[]; sell_branches: DragonTigerBranch[];
}
export interface DragonTigerBoardData {
  available: boolean; date: string; total_records: number; total_stocks: number;
  source: string; reason_note?: string | null; branch_note?: string | null;
  stocks: DragonTigerBoardStock[];
}
export interface GrowthLadderStock {
  code: string; name: string; industry: string; pct_chg: number; level: number;
  eliminated: boolean; stage_high: boolean;
}
export interface GrowthLadderLevel { level: number; items: GrowthLadderStock[]; overflow: number }
export interface GrowthLadderSync {
  run_id?: string; state: "idle" | "queued" | "building" | "ready" | "failed";
  phase: string; completed: number; total: number; percent: number;
  started_at?: string; finished_at?: string; last_ready_date?: string | null;
  available_dates: string[]; missing?: string[]; source_hits?: Record<string, number>; error?: string | null;
}
export interface GrowthLadderQuality {
  universe_count: number; covered_count: number; missing_count: number; suspended_count: number;
  source_hits: Record<string, number>; algorithm_version: string;
}
export interface GrowthLadderData {
  available: boolean; date: string; live: boolean; updated_at: string;
  rule: string; source: string; coverage: "full" | "building" | "unavailable" | "approximate";
  note?: string; available_dates: string[]; levels: GrowthLadderLevel[];
  quality: GrowthLadderQuality; sync: GrowthLadderSync;
}
export type AnomalyCoverage = "full" | "partial" | "unavailable";
export interface AnomalyPrediction {
  code: string; name: string; market: "SH" | "SZ" | "BJ"; board: string; industry: string;
  price: number | null; current_pct: number; window_days: number; deviation: number | null;
  required_pct: number | null; status: string; rule: string; reset_date?: string | null;
  quote_time?: string | null; benchmark?: string; daily_limit?: number;
  direction?: "up" | "down"; threshold?: number; progress_days?: number;
  occurrence?: number; rule_code?: number; source?: string;
}
export interface AnomalyPredictions {
  available: boolean; coverage: AnomalyCoverage; trade_date?: string | null; updated_at: string;
  algorithm_version: string; note?: string | null;
  groups: { next_day: AnomalyPrediction[]; same_day: AnomalyPrediction[]; triggered: AnomalyPrediction[] };
  quality: { universe_count: number; history_covered: number; announcement_covered: number;
    realtime_covered: number; missing_count: number; source_hits: Record<string, number>;
    ineligible_count?: number;
    prediction_source?: string; prediction_source_date?: string; prediction_rows?: number;
    prediction_source_error?: string;
    missing_reasons?: Record<string, number>;
    missing_samples?: Array<{ code: string; name?: string | null; reason: string }> };
}
export interface AnomalyMonitor {
  code: string; name: string; market: "SH" | "SZ" | "BJ"; type: "risk_warning" | "severe_abnormal";
  type_label: string; start?: string; end?: string; remaining_days?: number | null; reason: string;
  announcement_date?: string; link?: string; rule?: string;
}
export interface AnomalyMonitors {
  available: boolean; coverage: AnomalyCoverage; counts: { all: number; risk_warning: number; severe_abnormal: number };
  items: AnomalyMonitor[]; pagination: { page: number; page_size: number; total: number; pages: number };
  errors: string[]; updated_at: string;
}
export interface AnomalySearchItem { code: string; name: string; market: string; industry: string; is_st: number }
export interface AnomalyCalculation {
  available: boolean; trade_date: string;
  stock: { code: string; name: string; market: string; board: string; industry: string; price: number | null; current_pct: number };
  benchmark: { code: string; name: string };
  general_rule: { threshold: number; same_direction_count: number };
  severe_rules: Array<{ window_days: number; threshold: number; direction: "up" | "down"; rule: string;
    deviation: number | null; required_today: number | null; required_next: number | null; triggered: boolean }>;
  events: Array<{ event_date: string; kind: string; rule_code: string; announcement_date: string; reset_date: string; url: string; source: string }>;
  disclaimer: string; algorithm_version: string;
}
export interface AnomalySync {
  state: "idle" | "queued" | "running" | "complete" | "failed"; phase: string; completed: number; total: number;
  progress: number; started_at?: string | null; finished_at?: string | null; error?: string | null;
  trade_date?: string | null; latest_success_date?: string | null; updated_at?: string | null;
}
export type BoardPatternMode = "close" | "intraday";
export type BoardPatternAlgorithm = "v1" | "v2";
export interface BoardPatternEvent {
  event_date: string; code: string; name: string; market: "SH" | "SZ" | "BJ";
  industry: string; theme: string; status: "sealed" | "confirmed_break" | "temporary_break";
  board_height: number; board_type: string; path: string[];
  open?: number | null; high?: number | null; low?: number | null; close?: number | null;
  prev_close?: number | null; pct_chg?: number | null; volume?: number | null; amount?: number | null;
  turnover_pct?: number | null; float_mcap?: number | null; first_seal?: string; break_times?: number;
  matching_eligible?: boolean; matching_note?: string;
}
export interface BoardPatternSync {
  state: "idle" | "queued" | "running" | "ready" | "failed"; phase: string;
  completed: number; total: number; progress: number; missing?: string[];
  source_hits?: Record<string, number>; available_dates: string[];
  latest_success_date?: string | null; error?: string | null;
  quality?: {
    required_dates: number; corpus_required_dates?: number; universe_ready_dates?: number;
    bars_ready_dates?: number; snapshot_ready_dates?: number; unknown_gap_count?: number;
    canonical_instrument_count?: number; invalid_directory_count?: number;
    alias_merged_count?: number; lifecycle_excluded_count?: number; updated_at?: string | null;
    source_hits?: Record<string, number>; algorithm_version?: string;
  };
  failure_summary?: {
    retriable_count: number; deterministic_count: number; categories?: Record<string, number>;
    samples: Array<{ trade_date: string; phase: string; source: string; error_type: string;
      category?: string; message: string; retriable: number | boolean; attempts: number }>;
  };
}
export interface BoardPatternEvents {
  available: boolean; coverage: "full" | "building" | "unavailable"; matching_available: boolean;
  date: string; mode: BoardPatternMode; anchor_time: string; algorithm_version: string;
  events: BoardPatternEvent[];
  groups: Array<{ key: string; label: string; count: number; items: BoardPatternEvent[] }>;
  available_dates: string[]; note?: string;
  intraday_window: {
    available: boolean; date: string; opens_at: string; closes_at: string;
    quote_day?: string | null; reason: string;
  };
  quality: { event_count: number; source_hits: Record<string, number>; history_sessions: number; required_sessions: number;
    required_dates?: number; corpus_required_dates?: number; universe_ready_dates?: number;
    bars_ready_dates?: number; snapshot_ready_dates?: number; unknown_gap_count?: number;
    canonical_instrument_count?: number; invalid_directory_count?: number;
    alias_merged_count?: number; lifecycle_excluded_count?: number; updated_at?: string | null;
    algorithm_version?: string };
  sync: BoardPatternSync;
}
export interface BoardPatternSearchItem {
  code: string; name: string; industry: string; status: BoardPatternEvent["status"];
  board_height: number; board_type: string;
}
export interface BoardPatternBar {
  date: string; open: number; high: number; low: number; close: number;
  pct_chg?: number | null; volume?: number | null; amount?: number | null; turnover_pct?: number | null;
}
export interface BoardPatternCase {
  code: string; name: string; event_date: string; industry: string; theme: string;
  status: BoardPatternEvent["status"]; board_height: number; board_type: string; path: string[];
  scores: { identity: number; path: number; volume_price: number; liquidity: number; environment: number; total: number; path_suffix: number; exact_path: boolean };
  result: { t1: number | null; t3: number | null; t1_high: number | null; t1_pct: number | null; label: string };
  chart: BoardPatternBar[];
}
export interface BoardPatternResult {
  available: boolean; coverage: string; matching_available: boolean; date: string;
  mode: BoardPatternMode; anchor_time: string; algorithm: BoardPatternAlgorithm; algorithm_version: string;
  target: BoardPatternEvent & { chart: BoardPatternBar[] };
  stats: { sample_count: number; complete_path_count: number; next_red_rate: number | null;
    next_avg_high: number | null; next_close_median: number | null; next_close_mean: number | null;
    next_limit_up: number; next_limit_down: number };
  cases: BoardPatternCase[]; candidate_count: number;
  scripts: Array<{ title: string; detail: string }>; warnings: string[];
  quality: BoardPatternEvents["quality"]; disclaimer: string;
}
export interface LockupRow { date: string; type: string; shares: number | null; able_shares: number | null; ratio: number | null }
export interface UnlockCalendar { start: string; end: string; fetched_at: string; truncated: boolean; events: (LockupRow & { code: string; name: string })[] }
export interface Lockup { history: LockupRow[]; upcoming: LockupRow[] }
export interface Board { name: string; code: string; change_pct: number | string; lead_stock: string }
export interface Blocks { total: number; boards: Board[]; concept_tags: string[] }
export interface HotConcept { concept: string; bk: string; hit: number }
export interface QaRow { company: string; question: string; answer: string | null; answerer: string; ask_time: string }
export interface IndustryRow { rank: number; name: string; change_pct: number; code: string; up_count: number; down_count: number }
export interface IndustryData { top: IndustryRow[]; bottom: IndustryRow[]; total: number }

// 全球市场（美股 / 港股，移植自 global-stock-data · 东财域内源）
export interface GlobalIndex {
  key: string; name: string; region: string;
  price: number | null; change_pct: number | null;
}
export interface GlobalQuote {
  code: string; name: string;
  price: number | null; open: number | null; high: number | null; low: number | null;
  prev_close: number | null; amount: number | null; mcap: number | null; change_pct: number | null;
}
export interface GlobalMetrics {
  report_date: string;
  revenue: number | null; revenue_yoy: number | null; net_profit: number | null;
  eps: number | null; roe: number | null; gross_margin: number | null;
  net_margin: number | null; debt_ratio: number | null;
}
export interface GlobalStock {
  code: string; name: string; market: string;
  quote: GlobalQuote; metrics: GlobalMetrics | null;
}

/** 此刻的「实时行情」属于哪一场 —— 盘前行情返回的是上一场收盘，UI 要如实标注 */
export interface MarketSession {
  now: string;
  today: string;
  /** 实时行情代表的交易日；取不到时为 null */
  quotes_of: string | null;
  /** 最近一个已经收盘、可用于复盘页面的真实交易日 */
  latest_closed: string | null;
  is_today: boolean;
  poll?: boolean;
  phase_key?: string;
  phase: string;
  /** 直接可展示的一句话，如「盘前 · 显示 2026-07-29 收盘」 */
  label: string;
}

/** 隔夜外围快照：指数 + 美股七姐妹，各自带「属于哪一场」 */
export interface OverseasRow {
  name: string;
  price: number;
  change_pct: number;
  /** 该行行情所属交易日；取不到为 null（**不拿今天顶替**） */
  session: string | null;
  region?: string;
  ticker?: string;
}
export interface OverseasSnapshot {
  available: boolean;
  reason?: string;
  indices?: OverseasRow[];
  mag7?: OverseasRow[];
  us_session?: string | null;
  hk_session?: string | null;
  /** 可直接展示的一句话，如「美股 2026-07-29 收盘」「港股 2026-07-30 盘前」。
   *  别用 us_session 自己拼「XX 收盘」—— 港股在北京白天可能正在交易。 */
  us_label?: string | null;
  hk_label?: string | null;
}

/** 今日实时打板情绪（盘面数据页）—— 与 ShortTermEmotion（已收盘那一场）分开 */
export interface LiveEmotion {
  available: boolean;
  reason?: string;
  date?: string;
  /** 快照时刻 HH:MM */
  as_of?: string;
  phase?: string;
  zt_count?: number;
  dt_count?: number | null;
  zb_count?: number | null;
  max_boards?: number;
  lianban_count?: number;
  seal_rate?: number | null;
  break_rate?: number | null;
  promotion_rate?: number | null;
  /** 晋级率的分母：上一场的涨停家数 */
  promotion_base?: number | null;
  two_board_count?: number;
  /** 分母是哪一场。两张卡都叫「晋级率」，而各自的「昨」不是同一天，所以把日期给出来写死 */
  promotion_base_date?: string | null;
  lianban_stocks?: LianbanStock[];
  emotion_regime?: EmotionRegime;
}

export interface LimitBreakEvent {
  id: string; kind: "break" | "reseal"; code: string; name: string; at: string;
  price: number; limit_up: number; break_count: number;
}
export interface LimitBreakState {
  name: string; status: "watching" | "sealed" | "break";
  break_count: number; last_event_at: string | null; price: number; limit_up: number;
}
export interface LimitBreakAlerts {
  available: boolean; reason?: string; date?: string; as_of?: string;
  states: Record<string, LimitBreakState>; events: LimitBreakEvent[];
}

export interface IwencaiStatus {
  configured: boolean; source_priority: string[]; intraday_refresh_minutes: 0 | 30 | 60 | 120;
  source?: "settings" | "environment";
  day: string; requests_today: number; successes_today: number; failures_today: number;
  last_attempt_at: string | null; circuit_state: string; blocked_until: string | null;
  circuit_reason: string | null;
}
export interface AppSettings {
  iwencai_intraday_refresh_minutes: 0 | 30 | 60 | 120;
  market_network: { mode: "auto" | "direct" | "proxy"; proxy_url: string };
  market_network_status: {
    configured_mode: "auto" | "direct" | "proxy"; effective_mode: "auto" | "direct" | "proxy";
    override_source: string | null; proxy_available: boolean; proxy_source: string | null;
    proxy_label: string; last_route: "direct" | "proxy" | null; last_route_at: string | null; ai_isolated: boolean;
  };
  review_schedule: {
    enabled: boolean; time: string; force: boolean; last_date: string | null;
    source: { provider: "codex-private" | "claude" | "codebuddy"; model: string } | null;
    last_triggered_at: string | null; last_status: "running" | "succeeded" | "failed" | null;
    last_finished_at: string | null; last_error: string | null; last_elapsed: number | null;
  };
  iwencai: IwencaiStatus;
}
export interface MarketNetworkDiagnostic {
  generated_at: string;
  status: AppSettings["market_network_status"];
  routes: Array<{ route: "direct" | "proxy"; ok: boolean; skipped?: boolean; detail?: string; sources: Array<{
    source: string; label: string; ok: boolean; latency_ms: number; detail: string;
  }> }>;
  tcp_note: string;
}

export type ValidationState = "pending" | "watching" | "satisfied" | "invalidated" | "insufficient_data" | "expired";
export interface TodayCheckpoint {
  key: string; time: string; label: string; state: "completed" | "missing" | "upcoming";
  observed_at: string | null; history_key: string | null;
}
export interface TodayValidationItem {
  id: string; metric: string; label: string; judgment: string; expected_direction: string;
  baseline: number | null; threshold: number | null; unit: string; current_value: number | null;
  actual_direction: string | null; status: ValidationState; status_label: string; evidence: string;
  updated_at: string | null;
  history: Array<{ status: ValidationState; at: string; snapshot: string; value: number | null; evidence: string }>;
}
export interface TodayTheme {
  id: string; name: string; lifecycle: string; strength: number | null; coverage: number | null;
  members: Array<{ code: string; name: string; pct?: number | null; boards?: number }>;
  logic: string; risk: string; sample_size: number; complete: boolean;
  data_status: "complete" | "partial" | "missing"; binding_basis: string[]; missing_reason: string;
}
export interface TodayValidationData {
  available: boolean; reason?: string; schema_version?: number; prediction_date?: string; eval_date?: string;
  observed_at?: string | null; stage?: string; editable?: boolean; eligible?: boolean; delayed?: boolean;
  checkpoints?: TodayCheckpoint[]; summary?: Record<ValidationState | "total", number>;
  validations?: TodayValidationItem[];
  regime?: { phase?: string | null; phase_label: string; confidence: string; coverage: number; summary: string;
    dimensions: Record<string, number | null>;
    dimension_evidence?: Record<string, { key: string; score: number | null; raw_value: number | null; state: string; detail: string }>;
    issues: string[] };
  themes?: TodayTheme[];
  feedback?: { total: number; classified?: number; unclassified?: number; complete?: boolean;
    missing_sources?: string[]; reason?: string;
    buckets: Array<{ label: string; count: number | null; ratio: number | null }>;
    members: Array<{ code: string; name: string; pct?: number | null; boards?: number }> };
  events?: Array<{ id: string; kind: string; title: string; occurred_at: string; severity: "info" | "watch" | "risk";
    before: unknown; after: unknown; threshold: number | null; scope: string[]; evidence: string }>;
  risks?: string[]; sources?: Record<string, { available?: boolean; reason?: string }>;
  available_times?: string[];
}

export const api = {
  health: () => get<{ ok: boolean }>("/health"),
  settings: () => get<AppSettings>("/settings"),
  saveSettings: (settings: { iwencai_intraday_refresh_minutes: 0 | 30 | 60 | 120; review_schedule?: AppSettings["review_schedule"]; market_network?: AppSettings["market_network"] }) =>
    request<AppSettings>("/settings", "PUT", settings),
  diagnoseMarketNetwork: () => request<MarketNetworkDiagnostic>("/settings/market-network/diagnose", "POST"),
  saveIwencaiKey: (api_key: string) => request<AppSettings>("/settings/iwencai-key", "PUT", { api_key }),
  clearIwencaiKey: () => request<AppSettings>("/settings/iwencai-key", "DELETE"),
  retryScheduledReview: () => request<{ running: boolean; date: string; job_id?: string; error?: string }>("/review/retry-scheduled", "POST"),
  indices: () => get<IndexQuote[]>("/indices"),
  marketSession: () => get<MarketSession>("/market/session"),
  todayValidation: (date?: string, at?: string) => {
    const params = new URLSearchParams();
    if (date) params.set("date", date);
    if (at) params.set("at", at);
    const query = params.toString();
    return get<TodayValidationData>(`/today-validation${query ? `?${query}` : ""}`);
  },
  overseas: () => get<OverseasSnapshot>("/market/overseas"),
  liveEmotion: () => get<LiveEmotion>("/market/live-emotion"),
  marketOverview: () => get<MarketOverview>("/market/overview"),
  emotion: (refresh = false) => get<ShortTermEmotion>(`/market/emotion${refresh ? "?refresh=true" : ""}`),
  boardTrafficLight: (codes: string[], stockMode: "live" | "settled" = "live", date?: string) => {
    const params = new URLSearchParams();
    if (codes.length) params.set("codes", codes.join(","));
    if (stockMode !== "live") params.set("stock_mode", stockMode);
    if (date) params.set("date", date);
    const query = params.toString();
    return get<BoardTrafficLight>(`/market/board-traffic-light${query ? `?${query}` : ""}`);
  },
  limitBreakAlerts: (codes: string[]) => get<LimitBreakAlerts>(`/market/limit-break-alerts?codes=${encodeURIComponent(codes.join(","))}`),
  limitGene: (code: string) => get<LimitGene>(`/market/limit-gene?code=${encodeURIComponent(code)}`),
  monitorSnapshot: async (watch: string) => {
    const codes = watch.split(',').filter(Boolean);
    const monitored = codes.slice(0, 100).join(',');
    if (!monitorId || monitored !== registeredWatch || Date.now() - registeredAt > 60000) {
      monitorId ??= randomId().replace(/-/g, "");
      registeredWatch = monitored; registeredAt = Date.now();
      try {
        await request('/monitor/watch', 'POST', {client_id: monitorId, codes: codes.slice(0, 100)});
        monitorWarning = undefined;
      } catch (e) {
        monitorWarning = `自选监控名单未更新：${e instanceof Error ? e.message : '注册失败'}；一分钟后重试，其余行情继续展示`;
      }
    }
    const snapshot = await get<MonitorSnapshot>(`/monitor/snapshot?watch=${encodeURIComponent(monitored)}`);
    return {...snapshot, watch_warning: monitorWarning || (codes.length > 100 ? '已有自选超过100只，本页仅监控前100只；原列表保留，可在持仓自选中整理' : undefined)};
  },
  firstBoard: (date?: string) => get<FirstBoardData>(`/market/first-board${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  dragonTigerBoard: (date?: string) => get<DragonTigerBoardData>(`/market/dragon-tiger-board${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  growthLadderYang: (date?: string) => get<GrowthLadderData>(`/market/growth-ladders/yang${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  growthLadderStatus: () => get<GrowthLadderSync>("/market/growth-ladders/yang/status"),
  syncGrowthLadder: (date?: string) => request<GrowthLadderSync>(`/market/growth-ladders/yang/sync${date ? `?date=${encodeURIComponent(date)}` : ""}`, "POST"),
  anomalyPredictions: (date?: string) => get<AnomalyPredictions>(`/market/anomaly-center/predictions${date ? `?date=${encodeURIComponent(date)}` : ""}`),
  anomalyMonitors: (type: "all" | "risk_warning" | "severe_abnormal" = "all", page = 1, pageSize = 20) =>
    get<AnomalyMonitors>(`/market/anomaly-center/monitors?type=${type}&page=${page}&page_size=${pageSize}`),
  anomalySearch: (query: string) => get<AnomalySearchItem[]>(`/market/anomaly-center/search?q=${encodeURIComponent(query)}`),
  anomalyCalculate: (code: string) => get<AnomalyCalculation>(`/market/anomaly-center/calculate?code=${encodeURIComponent(code)}`),
  anomalyStatus: () => get<AnomalySync>("/market/anomaly-center/status"),
  syncAnomalyCenter: (date?: string) => request<AnomalySync>(`/market/anomaly-center/sync${date ? `?date=${encodeURIComponent(date)}` : ""}`, "POST"),
  boardPatternEvents: (mode: BoardPatternMode, date?: string) => {
    const params = new URLSearchParams({ mode });
    if (date) params.set("date", date);
    return get<BoardPatternEvents>(`/market/board-pattern-research/events?${params}`);
  },
  boardPatternSearch: (query: string, mode: BoardPatternMode, date?: string) => {
    const params = new URLSearchParams({ q: query, mode });
    if (date) params.set("date", date);
    return get<BoardPatternSearchItem[]>(`/market/board-pattern-research/search?${params}`);
  },
  boardPatternCalculate: (body: { code: string; mode: BoardPatternMode; algorithm: BoardPatternAlgorithm; date?: string }) =>
    request<BoardPatternResult>("/market/board-pattern-research/calculate", "POST", body),
  boardPatternStatus: () => get<BoardPatternSync>("/market/board-pattern-research/status"),
  syncBoardPattern: (date?: string) => request<BoardPatternSync>(`/market/board-pattern-research/sync${date ? `?date=${encodeURIComponent(date)}` : ""}`, "POST"),
  turnoverTop: () => get<TurnoverTop>("/market/turnover-top"),
  globalIndices: () => get<GlobalIndex[]>("/global/indices"),
  globalStock: (symbol: string) => get<GlobalStock>(`/global/stock?symbol=${encodeURIComponent(symbol)}`),
  radar: () => get<RadarData>("/radar"),
  radarRefresh: () => request<RadarData>("/radar/refresh", "POST"),
  portfolio: () => get<PortfolioData>("/portfolio"),
  addHolding: (code: string, shares: number, cost: number) => request<PortfolioData>("/portfolio/holding", "POST", { code, shares, cost }),
  removeHolding: (code: string) => request<PortfolioData>(`/portfolio/holding?code=${code}`, "DELETE"),
  refreshPortfolio: () => request<PortfolioData>("/portfolio/refresh", "POST"),
  closePosition: (code: string, date: string, price: number, shares: number, cost: number) =>
    request<PortfolioData>("/portfolio/close", "POST", { code, date, price, shares, cost }),
  removeClosed: (index: number) => request<PortfolioData>(`/portfolio/close?index=${index}`, "DELETE"),
  valuation: (code: string) => get<Valuation>(`/valuation?code=${code}`),
  percentile: (code: string) => get<ValPercentile>(`/valuation/percentile?code=${code}`),
  financials: (code: string) => get<Financials>(`/financials?code=${code}`),
  announcements: (code: string) => get<Announcement[]>(`/announcements?code=${code}`),
  quote: (codes: string) => get<Record<string, Quote>>(`/quote?codes=${codes}`),
  kline: (code: string, category = 4, offset = 120) => get<KlineRow[]>(
    `/kline?code=${encodeURIComponent(code)}&category=${category}&offset=${offset}`,
  ),
  industryPeers: (industry: string, code: string, limit = 300, date?: string | null) => get<IndustryPeers>(
    `/market/industry-peers?industry=${encodeURIComponent(industry)}&code=${encodeURIComponent(code)}&limit=${limit}${date ? `&date=${encodeURIComponent(date)}` : ""}`,
  ),
  conceptPeers: (concept: string, code = "", limit = 30) => get<IndustryPeers>(
    `/market/concept-peers?concept=${encodeURIComponent(concept)}&code=${encodeURIComponent(code)}&limit=${limit}`,
  ),
  intraday: (codes: string[], date?: string | null, days = 1) => get<IntradayBundle>(
    `/market/intraday?codes=${encodeURIComponent(codes.join(","))}${date ? `&date=${encodeURIComponent(date)}` : ""}&days=${Math.max(1, Math.min(5, days))}`,
  ),
  reports: (code: string) => get<Report[]>(`/reports?code=${code}`),
  news: (code: string) => get<NewsItem[]>(`/news?code=${code}`),
  margin: (code: string) => get<MarginRow[]>(`/margin?code=${code}`),
  blockTrade: (code: string) => get<BlockTradeRow[]>(`/block-trade?code=${code}`),
  holders: (code: string) => get<HolderRow[]>(`/holders?code=${code}`),
  dividend: (code: string) => get<DividendRow[]>(`/dividend?code=${code}`),
  fundFlow: (code: string) => get<FundFlowRow[]>(`/fund-flow?code=${code}`),
  dragonTiger: (code: string, date?: string) => get<DragonTiger>(`/dragon-tiger?code=${code}${date ? `&date=${encodeURIComponent(date)}` : ""}`),
  unlockCalendar: (window: "upcoming" | "recent") => get<UnlockCalendar>(`/lockup-calendar?window=${window}`),
  lockup: (code: string) => get<Lockup>(`/lockup?code=${code}`),
  blocks: (code: string) => get<Blocks>(`/blocks?code=${code}`),
  hotConcepts: (code: string) => get<HotConcept[]>(`/hot-concepts?code=${code}`),
  investorQa: (code: string) => get<QaRow[]>(`/investor-qa?code=${code}`),
  industry: (top = 20) => get<IndustryData>(`/industry?top=${top}`),
  myReports: () => get<MyReport[]>("/myreports"),
  uploadReport: (name: string, contentB64: string) =>
    request<MyReport>("/myreports", "POST", { name, content_b64: contentB64 }),
  deleteReport: (id: string) => request<{ ok: boolean }>(`/myreports/${id}`, "DELETE"),
};
