import { useEffect, useMemo, useState, type MouseEvent } from "react";
import {
  ArrowDown, ArrowUp, Building2, CalendarDays, ChevronDown, ChevronUp,
  ExternalLink, Info, Loader2, RefreshCw, RotateCcw, Search, SlidersHorizontal, TrendingDown,
  TrendingUp, X,
} from "lucide-react";
import { PageHeader } from "@/components/ui/PageHeader";
import { GlassCard } from "@/components/ui/GlassCard";
import { Disclaimer } from "@/components/ui/Disclaimer";
import { Dialog } from "@/components/workspace/Dialog";
import { StockModal } from "@/components/LadderExplorer";
import {
  api, type Announcement, type DragonTigerBoardData, type DragonTigerBoardStock,
  type DragonTigerBranch,
} from "@/lib/api";
import { cn } from "@/lib/utils";

type FlowFilter = "all" | "inflow" | "outflow";

const inputClass = "h-10 w-full rounded-xl border border-border/70 bg-background/55 px-3 text-sm outline-none transition focus:border-primary/70 focus:ring-2 focus:ring-primary/15";

function formatAmount(value: number) {
  const absolute = Math.abs(value);
  if (absolute >= 10000) return `${(absolute / 10000).toFixed(2)}亿`;
  return `${Math.round(absolute).toLocaleString("zh-CN")}万`;
}

function signedAmount(value: number) {
  if (value === 0) return "0万";
  return `${value > 0 ? "+" : "-"}${formatAmount(value)}`;
}

function shortBranchName(name: string) {
  return name
    .replace(/股份有限公司/g, "")
    .replace(/有限责任公司/g, "")
    .replace(/有限公司/g, "")
    .replace(/证券营业部$/g, "")
    .replace(/营业部$/g, "") || name;
}

function StockCard({ stock, openDetail, openMarket, openReason }: {
  stock: DragonTigerBoardStock;
  openDetail: (stock: DragonTigerBoardStock) => void;
  openMarket: (stock: DragonTigerBoardStock) => void;
  openReason: (stock: DragonTigerBoardStock) => void;
}) {
  const inflow = stock.net_wan >= 0;
  const branches = (inflow ? stock.buy_branches : stock.sell_branches).slice(0, 2);
  return (
    <article
      className={cn(
        "group flex h-full cursor-pointer flex-col rounded-2xl border border-border/70 border-l-4 bg-card/55 p-4 shadow-sm transition hover:-translate-y-0.5 hover:border-primary/35 hover:shadow-lg",
        inflow ? "border-l-danger" : "border-l-success",
      )}
      onClick={() => openDetail(stock)}
      aria-label={`查看 ${stock.name} 营业部详情`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate text-base font-bold text-foreground">{stock.name}</h3>
          <p className="mt-0.5 font-mono text-xs text-muted-foreground">{stock.code}</p>
        </div>
        <div className="text-right">
          <p className={cn("font-mono text-sm font-bold", stock.change_pct >= 0 ? "text-danger" : "text-success")}>
            {stock.change_pct > 0 ? "+" : ""}{stock.change_pct.toFixed(2)}%
          </p>
          <p className={cn("mt-1 flex items-center justify-end gap-1 text-sm font-semibold", inflow ? "text-danger" : "text-success")}>
            {inflow ? <ArrowUp className="h-3.5 w-3.5" /> : <ArrowDown className="h-3.5 w-3.5" />}
            {signedAmount(stock.net_wan)}
          </p>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-2 rounded-xl bg-muted/25 px-3 py-2.5 text-xs">
        <div>
          <p className="text-muted-foreground">成交金额</p>
          <p className="mt-1 font-semibold text-foreground">{formatAmount(stock.deal_wan)}</p>
        </div>
        <div>
          <p className="text-muted-foreground">换手率</p>
          <p className="mt-1 font-semibold text-foreground">{stock.turnover_pct.toFixed(2)}%</p>
        </div>
      </div>

      <div className="mt-3 min-h-12">
        <p className="text-[11px] font-medium text-muted-foreground">上榜原因</p>
        <p className="mt-1 line-clamp-2 text-xs leading-5 text-foreground/85" title={stock.reason}>{stock.reason || "—"}</p>
      </div>

      <div className="mt-3 flex-1">
        <p className="text-[11px] font-medium text-muted-foreground">主要{inflow ? "买入" : "卖出"}营业部</p>
        <div className="mt-1.5 space-y-1.5">
          {branches.length ? branches.map((branch) => (
            <div key={branch.name} className="flex items-center justify-between gap-2 rounded-lg border border-border/50 bg-background/30 px-2 py-1.5 text-[11px]">
              <span className="min-w-0 truncate" title={branch.name}>{shortBranchName(branch.name)}</span>
              <span className={cn("shrink-0 font-mono", inflow ? "text-danger" : "text-success")}>
                {formatAmount(inflow ? branch.buy_wan : branch.sell_wan)}
              </span>
            </div>
          )) : <p className="py-2 text-xs text-muted-foreground/65">暂无营业部明细</p>}
        </div>
      </div>

      <div className="mt-4 flex min-h-6 flex-wrap gap-1.5">
        {stock.concept && <span className="rounded-md border border-info/25 bg-info/10 px-2 py-0.5 text-[10px] font-medium text-info">{stock.concept}</span>}
        {stock.board_label && <span className="rounded-md border border-danger/25 bg-danger/10 px-2 py-0.5 text-[10px] font-medium text-danger">{stock.board_label}</span>}
      </div>

      <div className="mt-3 flex gap-2 border-t border-border/55 pt-3">
        <button type="button" onClick={(event: MouseEvent) => { event.stopPropagation(); openMarket(stock); }} className="inline-flex h-8 items-center rounded-lg border border-border/70 px-3 text-xs font-medium transition hover:border-primary/45 hover:text-primary">
          行情
        </button>
        <button type="button" onClick={(event: MouseEvent) => { event.stopPropagation(); openReason(stock); }} className="h-8 rounded-lg border border-border/70 px-3 text-xs font-medium transition hover:border-primary/45 hover:text-primary">
          异动
        </button>
      </div>
    </article>
  );
}

function BranchTable({ title, side, branches }: { title: string; side: "buy" | "sell"; branches: DragonTigerBranch[] }) {
  const amount = (branch: DragonTigerBranch) => side === "buy" ? branch.buy_wan : branch.sell_wan;
  const tone = side === "buy" ? "text-danger" : "text-success";
  const tagTone: Record<DragonTigerBranch["category"], string> = {
    institution: "border-violet-400/35 bg-violet-400/10 text-violet-300",
    northbound: "border-sky-400/35 bg-sky-400/10 text-sky-300",
    quant: "border-amber-400/35 bg-amber-400/10 text-amber-300",
    hot_money: "border-danger/35 bg-danger/10 text-danger",
    retail: "border-info/35 bg-info/10 text-info",
    brokerage: "border-border bg-muted/20 text-muted-foreground",
  };
  return (
    <section className="overflow-hidden rounded-xl border border-border/70">
      <div className="flex items-center gap-2 border-b border-border/65 bg-muted/25 px-4 py-3">
        <Building2 className={cn("h-4 w-4", tone)} />
        <h3 className="font-semibold">{title} <span className="text-xs font-normal text-muted-foreground">（{branches.length}席）</span></h3>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[430px] text-left text-xs">
          <thead className="bg-muted/15 text-muted-foreground">
            <tr><th className="px-4 py-2.5 font-medium">营业部</th><th className="px-3 py-2.5 text-right font-medium">金额</th><th className="px-4 py-2.5 text-right font-medium">占比</th></tr>
          </thead>
          <tbody className="divide-y divide-border/45">
            {branches.map((branch, index) => (
              <tr key={branch.seat_id || `${branch.name}-${index}`} className="hover:bg-muted/15">
                <td className="px-4 py-3">
                  <p className="max-w-[360px] truncate font-medium" title={branch.name}>{branch.name}</p>
                  {!!branch.tags.length && <div className="mt-1 flex flex-wrap gap-1">{branch.tags.map((tag) => <span key={tag} title={branch.description} className={cn("cursor-help rounded border px-1.5 py-0.5 text-[10px]", tagTone[branch.category] || tagTone.brokerage)}>{tag}</span>)}</div>}
                </td>
                <td className={cn("px-3 py-3 text-right font-mono font-semibold", tone)}>{formatAmount(amount(branch))}</td>
                <td className="px-4 py-3 text-right font-mono text-muted-foreground">{branch.ratio.toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!branches.length && <p className="px-4 py-8 text-center text-sm text-muted-foreground">暂无营业部明细</p>}
      </div>
    </section>
  );
}

function DetailDialog({ stock, close }: { stock: DragonTigerBoardStock; close: () => void }) {
  const thsUrl = `https://t.10jqka.com.cn/pc/newgroup/stock/${stock.code}/`;
  return (
    <Dialog titleId="dragon-tiger-detail-title" close={close} className="max-w-5xl">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border/70 bg-card/95 px-5 py-4 backdrop-blur">
        <a href={thsUrl} target="_blank" rel="noopener noreferrer" aria-label={`打开 ${stock.name} 同花顺个股讨论`} className="group min-w-0 flex-1 rounded-lg outline-none transition hover:text-primary focus-visible:ring-2 focus-visible:ring-primary/40">
          <span className="flex items-center gap-2">
            <h2 id="dragon-tiger-detail-title" className="truncate text-lg font-bold">{stock.code} {stock.name} - 营业部详情</h2>
            <ExternalLink className="h-4 w-4 shrink-0 text-muted-foreground transition group-hover:text-primary" />
          </span>
          <p className="mt-0.5 text-xs text-muted-foreground transition group-hover:text-primary/80">公开龙虎榜席位明细 · 点击查看同花顺个股评论</p>
        </a>
        <button type="button" onClick={close} aria-label="关闭" className="rounded-lg p-2 text-muted-foreground hover:bg-muted/50 hover:text-foreground"><X className="h-4 w-4" /></button>
      </div>
      <div className="space-y-5 p-5">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          {[
            ["总买入", formatAmount(stock.buy_wan), "text-danger"],
            ["总卖出", formatAmount(stock.sell_wan), "text-success"],
            ["净买入", signedAmount(stock.net_wan), stock.net_wan >= 0 ? "text-danger" : "text-success"],
            ["涨跌幅", `${stock.change_pct > 0 ? "+" : ""}${stock.change_pct.toFixed(2)}%`, stock.change_pct >= 0 ? "text-danger" : "text-success"],
          ].map(([label, value, tone]) => <div key={label} className="rounded-xl border border-border/60 bg-muted/20 p-3 text-center"><p className="text-xs text-muted-foreground">{label}</p><p className={cn("mt-1 font-mono text-base font-bold", tone)}>{value}</p></div>)}
        </div>
        {!stock.buy_branches.length && !stock.sell_branches.length ? (
          <div className="rounded-xl border border-dashed border-border p-10 text-center text-sm text-muted-foreground">该记录暂无营业部详细数据</div>
        ) : (
          <div className="grid gap-4 lg:grid-cols-2">
            <BranchTable title="买入营业部" side="buy" branches={stock.buy_branches} />
            <BranchTable title="卖出营业部" side="sell" branches={stock.sell_branches} />
          </div>
        )}
        <p className="flex items-start gap-1.5 text-[11px] leading-5 text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          机构专用及沪深股通为交易所披露属性；量化、游资及散户席位为公开市场常见营业部画像，不代表本次交易账户身份已被确认。
        </p>
      </div>
    </Dialog>
  );
}

function ReasonDialog({ stock, quoteDate, close }: { stock: DragonTigerBoardStock; quoteDate?: string | null; close: () => void }) {
  const [announcements, setAnnouncements] = useState<Announcement[]>([]);
  const [announcementLoading, setAnnouncementLoading] = useState(true);
  const [announcementError, setAnnouncementError] = useState("");

  useEffect(() => {
    let active = true;
    setAnnouncementLoading(true);
    setAnnouncementError("");
    api.announcements(stock.code)
      .then((rows) => {
        if (!active) return;
        setAnnouncements(rows.filter((item) => !quoteDate || !item.date || item.date <= quoteDate).slice(0, 5));
      })
      .catch((error) => {
        if (active) setAnnouncementError(error instanceof Error ? error.message : "公告加载失败");
      })
      .finally(() => { if (active) setAnnouncementLoading(false); });
    return () => { active = false; };
  }, [quoteDate, stock.code]);

  const triggerReasons = stock.reasons?.length ? stock.reasons : (stock.reason ? [stock.reason] : []);
  const capitalDirection = stock.net_wan >= 0 ? "净买入" : "净卖出";
  return (
    <Dialog titleId="dragon-tiger-reason-title" close={close} className="max-w-2xl">
      <div className="sticky top-0 z-10 flex items-center justify-between border-b border-border/70 bg-card/95 px-5 py-4 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-info text-sm font-bold text-white">i</span>
          <div className="min-w-0">
            <h2 id="dragon-tiger-reason-title" className="truncate font-bold">{stock.name} ({stock.code}) 异动原因</h2>
            <p className="mt-0.5 text-xs text-muted-foreground">{quoteDate || "最近交易日"} · 公开信息汇总</p>
          </div>
        </div>
        <button type="button" onClick={close} aria-label="关闭" className="rounded-lg p-2 text-muted-foreground hover:bg-muted/50"><X className="h-4 w-4" /></button>
      </div>
      <div className="max-h-[62vh] space-y-5 overflow-y-auto p-5">
        <div className="flex flex-wrap gap-2">
          <span className="rounded-md border border-info/30 bg-info/10 px-2.5 py-1 text-xs font-semibold text-info">{announcements.length ? "公告" : "龙虎榜"}</span>
          {stock.concept && <span className="rounded-md border border-border bg-muted/30 px-2.5 py-1 text-xs text-muted-foreground">{stock.concept}</span>}
          {stock.board_label && <span className="rounded-md border border-danger/25 bg-danger/10 px-2.5 py-1 text-xs text-danger">{stock.board_label}</span>}
        </div>

        {stock.theme_reason && <section>
          <h3 className="text-sm font-semibold">题材线索</h3>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-7 text-foreground/90">{stock.theme_reason}</p>
        </section>}

        <section>
          <h3 className="text-sm font-semibold">交易所触发原因</h3>
          <ol className="mt-2 space-y-2 text-sm leading-6 text-foreground/90">
            {triggerReasons.length ? triggerReasons.map((reason, index) => <li key={`${reason}-${index}`} className="flex gap-2"><span className="font-mono text-muted-foreground">{index + 1}、</span><span>{reason}</span></li>) : <li className="text-muted-foreground">交易所未返回具体触发条款</li>}
          </ol>
        </section>

        <section className="rounded-xl border border-border/70 bg-muted/20 p-4">
          <h3 className="text-sm font-semibold">龙虎榜资金</h3>
          <div className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3 text-xs sm:grid-cols-4">
            <div><p className="text-muted-foreground">买入</p><p className="mt-1 font-mono font-semibold text-danger">{formatAmount(stock.buy_wan)}</p></div>
            <div><p className="text-muted-foreground">卖出</p><p className="mt-1 font-mono font-semibold text-success">{formatAmount(stock.sell_wan)}</p></div>
            <div><p className="text-muted-foreground">{capitalDirection}</p><p className={cn("mt-1 font-mono font-semibold", stock.net_wan >= 0 ? "text-danger" : "text-success")}>{formatAmount(stock.net_wan)}</p></div>
            <div><p className="text-muted-foreground">成交金额</p><p className="mt-1 font-mono font-semibold">{formatAmount(stock.deal_wan)}</p></div>
          </div>
        </section>

        <section>
          <h3 className="text-sm font-semibold">近期公告 <span className="font-normal text-muted-foreground">（截止榜单日）</span></h3>
          {announcementLoading ? <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground"><Loader2 className="h-3.5 w-3.5 animate-spin" />加载公告…</p>
            : announcementError ? <p className="mt-3 text-xs text-muted-foreground">公告补充暂不可用：{announcementError}</p>
              : announcements.length ? <div className="mt-2 divide-y divide-border/55 rounded-xl border border-border/70">{announcements.map((item, index) => (
                <a key={`${item.date}-${item.title}-${index}`} href={item.url || undefined} target={item.url ? "_blank" : undefined} rel="noreferrer" className={cn("block px-3 py-2.5 transition", item.url && "hover:bg-muted/35")}>
                  <div className="flex items-start justify-between gap-3"><span className="text-sm leading-6">{item.title}</span><span className="shrink-0 font-mono text-[11px] text-muted-foreground">{item.date || "—"}</span></div>
                  {item.type && <span className="mt-1 inline-block rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">{item.type}</span>}
                </a>
              ))}</div> : <p className="mt-3 text-xs text-muted-foreground">该日期之前暂无可用公告补充。</p>}
        </section>

        <p className="text-[11px] leading-5 text-muted-foreground">内容来自交易所龙虎榜、公开题材标签与上市公司公告，仅用于还原当日公开信息，不代表因果认定或投资建议。</p>
      </div>
      <div className="sticky bottom-0 flex justify-end border-t border-border/70 bg-card/95 px-5 py-3 backdrop-blur">
        <button type="button" onClick={close} className="rounded-lg bg-primary px-5 py-2 text-sm font-semibold text-primary-foreground">确定</button>
      </div>
    </Dialog>
  );
}

export function DragonTigerBoard() {
  const [data, setData] = useState<DragonTigerBoardData | null>(null);
  const [selectedDate, setSelectedDate] = useState("");
  const [dateReady, setDateReady] = useState(false);
  const [stockQuery, setStockQuery] = useState("");
  const [branchQuery, setBranchQuery] = useState("");
  const [concept, setConcept] = useState("全部");
  const [flow, setFlow] = useState<FlowFilter>("all");
  const [filtersOpen, setFiltersOpen] = useState(false);
  const [buyOpen, setBuyOpen] = useState(true);
  const [sellOpen, setSellOpen] = useState(true);
  const [detail, setDetail] = useState<DragonTigerBoardStock | null>(null);
  const [market, setMarket] = useState<DragonTigerBoardStock | null>(null);
  const [reason, setReason] = useState<DragonTigerBoardStock | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [refreshVersion, setRefreshVersion] = useState(0);
  const today = new Intl.DateTimeFormat("sv-SE", { timeZone: "Asia/Shanghai" }).format(new Date());

  useEffect(() => {
    let active = true;
    api.marketSession()
      .then((session) => {
        if (active && session.latest_closed) {
          setSelectedDate((current) => current || session.latest_closed || "");
        }
      })
      .catch(() => undefined)
      .finally(() => { if (active) setDateReady(true); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!dateReady) return undefined;
    let active = true;
    setLoading(true); setError("");
    api.dragonTigerBoard(selectedDate || undefined)
      .then((next) => {
        if (!active) return;
        if (!next.available || !Array.isArray(next.stocks)) throw new Error("龙虎榜数据暂不可用");
        setData(next);
      })
      .catch((err) => { if (active) setError(err instanceof Error ? err.message : "龙虎榜读取失败"); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [dateReady, selectedDate, refreshVersion]);

  const resetFilters = () => { setStockQuery(""); setBranchQuery(""); setConcept("全部"); setFlow("all"); };
  const source = data?.stocks ?? [];
  const concepts = useMemo(() => {
    const counts = new Map<string, number>();
    source.forEach((stock) => { if (stock.concept) counts.set(stock.concept, (counts.get(stock.concept) || 0) + 1); });
    return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0], "zh-CN"));
  }, [source]);

  const searchFiltered = useMemo(() => {
    const stockNeedle = stockQuery.trim().toLowerCase();
    const branchNeedle = branchQuery.trim().toLowerCase();
    return source.filter((stock) => {
      if (stockNeedle && !`${stock.name} ${stock.code}`.toLowerCase().includes(stockNeedle)) return false;
      if (branchNeedle) {
        const haystack = [...stock.buy_branches, ...stock.sell_branches]
          .flatMap((branch) => [branch.name, ...branch.tags]).join(" ").toLowerCase();
        if (!haystack.includes(branchNeedle)) return false;
      }
      if (concept !== "全部" && stock.concept !== concept) return false;
      return true;
    });
  }, [source, stockQuery, branchQuery, concept]);
  const visible = searchFiltered.filter((stock) => flow === "all" || (flow === "inflow" ? stock.net_wan >= 0 : stock.net_wan < 0));
  const inflows = visible.filter((stock) => stock.net_wan >= 0);
  const outflows = visible.filter((stock) => stock.net_wan < 0);
  const allInflows = source.filter((stock) => stock.net_wan >= 0);
  const allOutflows = source.filter((stock) => stock.net_wan < 0);
  const totalIn = allInflows.reduce((sum, stock) => sum + stock.net_wan, 0);
  const totalOut = allOutflows.reduce((sum, stock) => sum + stock.net_wan, 0);
  const unified = flow !== "all" || concept !== "全部";

  const cards = (stocks: DragonTigerBoardStock[]) => stocks.map((stock) => (
    <StockCard key={stock.code} stock={stock} openDetail={setDetail} openMarket={setMarket} openReason={setReason} />
  ));

  return (
    <div>
      <PageHeader
        title="龙虎榜"
        subtitle="每日公开龙虎榜资金与营业部席位 · 按净额绝对值排序"
        actions={<div className="flex items-center gap-2">
          <label className="relative block">
            <CalendarDays className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
            <input aria-label="交易日期" type="date" max={today} value={selectedDate || data?.date || ""} onChange={(event) => setSelectedDate(event.target.value)} className="h-10 rounded-xl border border-border/70 bg-background/55 pl-8 pr-2 text-sm outline-none focus:border-primary/70" />
          </label>
          <button type="button" onClick={() => setRefreshVersion((value) => value + 1)} disabled={loading} aria-label="刷新龙虎榜" className="inline-flex h-10 w-10 items-center justify-center rounded-xl border border-border/70 bg-background/55 text-muted-foreground hover:text-primary disabled:opacity-50">
            <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />
          </button>
        </div>}
      />

      <div className="mb-4 flex items-start gap-2 rounded-xl border border-info/20 bg-info/5 px-3 py-2.5 text-xs text-muted-foreground">
        <Info className="mt-0.5 h-3.5 w-3.5 shrink-0 text-info" />
        <span>龙虎榜数据从交易日下午 5 点开始陆续更新。金额单位已统一为万元，同股多条上榜原因已合并。</span>
      </div>

      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[
          ["净流入股票", `${allInflows.length} 只`, "text-danger", TrendingUp],
          ["净流出股票", `${allOutflows.length} 只`, "text-success", TrendingDown],
          ["总净流入", formatAmount(totalIn), "text-danger", ArrowUp],
          ["总净流出", formatAmount(totalOut), "text-success", ArrowDown],
        ].map(([label, value, tone, Icon]) => (
          <GlassCard key={label as string} className="flex items-center gap-3 p-4">
            <span className={cn("rounded-xl bg-muted/35 p-2", tone as string)}><Icon className="h-4 w-4" /></span>
            <div><p className="text-xs text-muted-foreground">{label as string}</p><p className={cn("mt-1 font-mono text-lg font-bold", tone as string)}>{loading && !data ? "—" : value as string}</p></div>
          </GlassCard>
        ))}
      </div>

      <GlassCard className="mb-5 p-0">
        <button type="button" onClick={() => setFiltersOpen((open) => !open)} className="flex w-full items-center justify-between px-4 py-3 text-sm font-semibold md:hidden">
          <span className="flex items-center gap-2"><SlidersHorizontal className="h-4 w-4 text-primary" />搜索筛选</span>
          <span className="flex items-center gap-1 text-xs text-muted-foreground">{filtersOpen ? "收起" : "展开"}{filtersOpen ? <ChevronUp className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}</span>
        </button>
        <div className={cn("space-y-4 p-4 pt-0 md:block md:p-5", !filtersOpen && "hidden")}>
          <div className="grid gap-3 md:grid-cols-2">
            <label><span className="mb-1.5 block text-xs font-medium text-muted-foreground">股票搜索</span><span className="relative block"><Search className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input value={stockQuery} onChange={(event) => setStockQuery(event.target.value)} placeholder="股票名称或代码" className={cn(inputClass, "pl-9")} /></span></label>
            <label><span className="mb-1.5 flex items-center gap-1 text-xs font-medium text-muted-foreground">营业部搜索 <span title="可搜索营业部名称或席位标签" className="cursor-help text-info">ⓘ</span></span><span className="relative block"><Building2 className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><input value={branchQuery} onChange={(event) => setBranchQuery(event.target.value)} placeholder="营业部名称、机构或沪深股通" className={cn(inputClass, "pl-9")} /></span></label>
          </div>
          <div>
            <p className="mb-2 text-xs font-medium text-muted-foreground">概念标签</p>
            <div className="flex flex-wrap gap-2">
              {[['全部', source.length] as [string, number], ...concepts].map(([name, count]) => <button key={name} type="button" onClick={() => setConcept(name)} className={cn("rounded-lg border px-2.5 py-1.5 text-xs transition", concept === name ? "border-primary/50 bg-primary/15 text-primary" : "border-border/70 bg-background/30 text-muted-foreground hover:text-foreground")}>{name} <span className="opacity-65">({count})</span></button>)}
            </div>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              {([
                ["all", "全部", searchFiltered.length],
                ["inflow", "净流入", searchFiltered.filter((stock) => stock.net_wan >= 0).length],
                ["outflow", "净流出", searchFiltered.filter((stock) => stock.net_wan < 0).length],
              ] as [FlowFilter, string, number][]).map(([value, label, count]) => <button key={value} type="button" onClick={() => setFlow(value)} className={cn("rounded-lg border px-3 py-1.5 text-xs font-medium transition", flow === value ? "border-primary/50 bg-primary/15 text-primary" : "border-border/70 text-muted-foreground hover:text-foreground")}>{label} ({count})</button>)}
            </div>
            <div className="flex gap-2"><button type="button" onClick={resetFilters} className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-border/70 px-3 text-xs text-muted-foreground hover:text-foreground"><RotateCcw className="h-3.5 w-3.5" />重置</button><button type="button" className="inline-flex h-9 items-center gap-1.5 rounded-lg bg-primary px-4 text-xs font-semibold text-primary-foreground"><Search className="h-3.5 w-3.5" />搜索</button></div>
          </div>
        </div>
      </GlassCard>

      {error && <div className="mb-5 rounded-xl border border-danger/30 bg-danger/5 p-5 text-center"><p className="text-sm text-danger">{error}</p><button type="button" onClick={() => setRefreshVersion((value) => value + 1)} className="mt-3 rounded-lg border border-danger/30 px-3 py-1.5 text-xs text-danger">重试</button></div>}
      {loading && !data && <div className="flex items-center justify-center gap-2 py-24 text-sm text-muted-foreground"><Loader2 className="h-5 w-5 animate-spin text-primary" />加载龙虎榜数据...</div>}

      {!loading && !error && !visible.length && <div className="rounded-2xl border border-dashed border-border py-20 text-center"><Search className="mx-auto h-8 w-8 text-muted-foreground/40" /><p className="mt-3 font-medium">暂无符合条件的数据</p><p className="mt-1 text-sm text-muted-foreground">请尝试调整筛选条件</p></div>}

      {!!visible.length && (unified ? (
        <section><h2 className="mb-3 text-sm font-bold">筛选结果 <span className="font-normal text-muted-foreground">({visible.length}只)</span></h2><div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">{cards(visible)}</div></section>
      ) : (
        <div className="grid items-start gap-5 xl:grid-cols-2">
          <section>
            <button type="button" onClick={() => setBuyOpen((open) => !open)} className="mb-3 flex w-full items-center justify-between"><h2 className="flex items-center gap-2 text-sm font-bold"><span className="rounded-lg bg-danger/10 p-1.5 text-danger"><TrendingUp className="h-4 w-4" /></span>主力在买什么 <span className="font-normal text-muted-foreground">({inflows.length}只)</span></h2>{buyOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground xl:hidden" /> : <ChevronDown className="h-4 w-4 text-muted-foreground xl:hidden" />}</button>
            <div className={cn("grid gap-3 sm:grid-cols-2", !buyOpen && "hidden xl:grid")}>{inflows.length ? cards(inflows) : <p className="col-span-2 rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">暂无净流入股票</p>}</div>
          </section>
          <section>
            <button type="button" onClick={() => setSellOpen((open) => !open)} className="mb-3 flex w-full items-center justify-between"><h2 className="flex items-center gap-2 text-sm font-bold"><span className="rounded-lg bg-success/10 p-1.5 text-success"><TrendingDown className="h-4 w-4" /></span>主力在卖什么 <span className="font-normal text-muted-foreground">({outflows.length}只)</span></h2>{sellOpen ? <ChevronUp className="h-4 w-4 text-muted-foreground xl:hidden" /> : <ChevronDown className="h-4 w-4 text-muted-foreground xl:hidden" />}</button>
            <div className={cn("grid gap-3 sm:grid-cols-2", !sellOpen && "hidden xl:grid")}>{outflows.length ? cards(outflows) : <p className="col-span-2 rounded-xl border border-dashed border-border py-10 text-center text-sm text-muted-foreground">暂无净流出股票</p>}</div>
          </section>
        </div>
      ))}

      {(data?.reason_note || data?.branch_note) && <p className="mt-5 text-xs text-muted-foreground">{[data.reason_note, data.branch_note].filter(Boolean).join("；")}</p>}
      <Disclaimer />
      {detail && <DetailDialog stock={detail} close={() => setDetail(null)} />}
      {market && <StockModal stock={{ code: market.code, name: market.name, industry: market.concept || "" }} quoteDate={data?.date || selectedDate || null} onClose={() => setMarket(null)} initialChartTab="minute" />}
      {reason && <ReasonDialog stock={reason} quoteDate={data?.date || selectedDate || null} close={() => setReason(null)} />}
    </div>
  );
}
