import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, ChevronDown, Circle, CircleHelp, Info, ShieldAlert, TrafficCone, Users, X } from "lucide-react";
import { Caliber } from "@/components/ui/Caliber";
import { type LimitGene } from "@/lib/api";
import { safeArray, type BoardTrafficLight, type StockTrafficLight, type TrafficLightColor, type TrafficSignal } from "@/lib/agent";
import { cn } from "@/lib/utils";

const tones: Record<TrafficLightColor, string> = {
  green: "border-success/40 bg-success/10 text-success",
  yellow: "border-warning/40 bg-warning/10 text-warning",
  red: "border-danger/40 bg-danger/10 text-danger",
  gray: "border-border bg-muted/40 text-muted-foreground",
};
const dots: Record<TrafficLightColor, string> = {
  green: "fill-success text-success", yellow: "fill-warning text-warning",
  red: "fill-danger text-danger", gray: "fill-muted-foreground/40 text-muted-foreground/40",
};
const textTones: Record<TrafficLightColor, string> = {
  green: "text-success", yellow: "text-warning", red: "text-danger", gray: "text-muted-foreground",
};

// 后端规则码不能直接暴露给用户：既说明当前为什么不能放行，也说明缺数据会影响什么判断。
const stockMissingLabels: Record<string, string> = {
  seal_time: "首次封板时间（无法判断是否早封）",
  last_seal_time: "最后回封时间（无法判断是否尾盘回封）",
  turnover_pct: "换手率（无法判断换手是否适中）",
  theme: "题材归属（无法判断是否有同题材助攻）",
  break_count: "开板次数（无法判断封板过程）",
  limit_pool: "公开涨停池记录（无法确认是否封板）",
  quote_date: "行情日期（无法确认数据是否对应当前交易日）",
  price: "当前价格（无法判断是否触及涨停）",
  limit_up: "涨停价格（无法判断是否触及涨停）",
  open: "开盘价格（无法判断开盘形态）",
  vol_ratio: "量比（无法判断当日量能）",
};
const containsChinese = (value: string) => /[\u3400-\u9fff]/.test(value);
const describeStockBlocker = (key: string) => containsChinese(key) ? key : "数据状态异常（未识别规则）";
const describeStockMissing = (key: string) => stockMissingLabels[key] || "数据字段暂未取得";

const ENVIRONMENT_RULES =
  "【环境总灯】大盘环境、情绪周期、打板生态三项合成：至少两项绿且没有红项才为绿；任一核心维度缺失则数据不足；两个红项或硬否决为红，其余为黄。\n\n" +
  "【大盘环境】上涨/下跌比 ≥ 1.2、上涨家数 ≥ 600，且四大指数至少 3 个上涨才为绿；比值 ≤ 0.7 或上涨不足 600 家为宽度风险，四大指数至少 3 个跌超 1%为指数风险。宽度与指数同时转弱才判红，否则未同向确认时为黄。\n\n" +
  "【情绪周期｜数据充足】六组信号至少 4 组有效，同时必须取得情绪温度；不足时不猜测，直接显示数据不足。\n" +
  "【情绪周期｜阶段】退潮确认优先判退潮；否则温度 ≤ 25%为冰点、≥ 75%为亢奋；已有退潮预警且动量 < -3个百分点时判退潮；动量 > +3个百分点且温度 < 50%为修复，温度 ≥ 45%或动量 > +3个百分点为发酵；其余动量 ≥ -3个百分点为修复，低于 -3个百分点为退潮。\n" +
  "【情绪周期｜六组风险阈值】①赚钱反馈：昨日涨停股中位数 ≤ -1%或连板股中位数 ≤ -1%（严重值分别为 -2%/-3%）；②1进2晋级率 ≤ 10%或较前值下降 ≥ 8个百分点；③炸板率 ≥ 40%或较前值上升 ≥ 8个百分点（≥ 50%为严重）；④昨日强势股跌超5%占比 ≥ 20%或跌停 ≥ 10家（严重值 30%/20家）；⑤涨停家数减少至少8家且降幅 ≥ 15%、最高板下降或梯队缺档；⑥上涨占比 < 45%，且下跌家数超过上涨家数1.2倍或大跌家数不少于大涨家数。\n" +
  "【情绪周期｜退潮合成】单组风险或温度 ≥ 75%先列高位观察；至少两组风险且含核心组进入预警；至少三组风险、严重风险叠加至少两组风险，或预警连续出现时确认退潮。修复/发酵且退潮状态正常为绿；亢奋、高位观察或负动量为黄；冰点、退潮、退潮预警/确认为红。\n\n" +
  "【打板生态｜收盘】五项读数分别评测：昨日涨停股收益中位数（偏多 ≥ 1%，偏空 ≤ -1%，≤ -2%硬否决）、昨日连板股收益中位数（偏多 ≥ 1%，偏空 ≤ -1%，≤ -3%硬否决）、1进2晋级率（偏多 ≥ 25%，偏空 ≤ 10%）、炸板未回封率（偏多 ≤ 25%，偏空 ≥ 40%，≥ 50%硬否决）、昨日强势股跌超5%占比（偏多 ≤ 10%，偏空 ≥ 20%，≥ 30%硬否决）。至少4项偏多、没有偏空，且赚钱反馈/晋级/炸板三项均偏多才进入备选；至少两项偏空则红，其余观察。\n" +
  "【打板生态｜盘中】炸板率 ≥ 40%、晋级率 ≤ 10%、跌停 ≥ 10家各计一项风险；一项风险为黄，两项及以上为红，炸板率 ≥ 50%直接硬否决，三项均通过为绿。封板率可由1-炸板率观察，不重复计票。\n\n" +
  "【反馈校准】若分灯与盘面体感不一致，请附交易日、盘中/收盘时点、你认为失真的具体读数和预期颜色；这样可按历史样本复核阈值，而不是只凭结论调灯。";

const signalStateLabels: Record<TrafficSignal["state"], string> = {
  healthy: "通过", risk: "风险", neutral: "中性", missing: "缺失",
  observed: "观察值", informational: "仅展示",
};

const hardVetoRules: Record<string, { signalKey: string; condition: string }> = {
  "board:money": { signalKey: "entry:money", condition: "昨日涨停股收益中位数 ≤ -2%" },
  "board:premium": { signalKey: "entry:premium", condition: "昨日连板股收益中位数 ≤ -3%" },
  "board:broken": { signalKey: "entry:broken", condition: "炸板未回封率 ≥ 50%" },
  "board:loss": { signalKey: "entry:loss", condition: "昨日涨停股跌超5%占比 ≥ 30%" },
  "board:live_broken": { signalKey: "broken", condition: "盘中炸板率 ≥ 50%" },
};

function describeHardVeto(light: BoardTrafficLight, veto: string) {
  const rule = hardVetoRules[veto];
  if (!rule) return `触发风控规则（${veto}）`;
  const signal = safeArray<TrafficSignal>(light.dimensions?.board?.signals)
    .find((item) => item.key === rule.signalKey);
  return `${rule.condition}${signal?.detail ? `；${signal.detail}` : ""}`;
}

function environmentRules(light?: BoardTrafficLight | null) {
  if (!light) return ENVIRONMENT_RULES;
  const current = [light.dimensions?.market, light.dimensions?.emotion, light.dimensions?.board]
    .filter(Boolean)
    .flatMap((dimension) => [
      `${dimension.label}：${dimension.light_label}｜${dimension.summary}`,
      ...safeArray<TrafficSignal>(dimension.signals).map(
        (signal) => `  - ${signal.label}：${signal.detail}（${signalStateLabels[signal.state] ?? signal.state}）`,
      ),
    ]);
  return `${ENVIRONMENT_RULES}\n\n【本次计算输入｜${light.mode === "live" ? "盘中" : "收盘"} ${light.date || "日期缺失"}】\n${current.join("\n")}`;
}

const SETTLED_QUALITY_RULES =
  "收盘口径：四项核心因子各计 1 分，分母是 4。\n" +
  "①首次封板不晚于 10:30；②换手率 3%–25%（含边界）；\n" +
  "③方向支持：同一产业题材或同行业至少 2 只封板，二者满足其一，合计只计 1 分；④封板过程：未开板通过，1–2 次开板需 14:30 前回封且稳定 ≥ 30 个交易分钟。\n" +
  "3–5 次开板且稳定满足时标记高分歧回封，该项不计分但不单独判红；6 次以上、尾盘回封或稳定不足会产生风险项。\n" +
  "封板资金及封板资金/流通市值目前仅展示，不计入分数。质量绿灯 = 至少 3/4 且无风险项。";

const LIVE_QUALITY_RULES =
  "盘中口径：先确认公开涨停池，再看首封时间、换手率、题材支持和封板稳定性。\n" +
  "10:00 前最高黄灯；需要至少 2 个有效快照。未开板稳定 ≥ 60 秒，开板 1–2 次稳定 ≥ 180 秒，3–5 次稳定 ≥ 300 秒。\n" +
  "首封、换手率 3%–25%、方向支持三项至少通过两项；方向支持由题材支持或行业支持满足其一，合计只计 1 分。当前开板立即红，6 次以上不能亮盘中质量绿。";

export function TrafficLightBadge({ light, className, compact = false }: {
  light?: BoardTrafficLight | null; className?: string; compact?: boolean;
}) {
  if (!light) return <span className={cn("text-xs text-muted-foreground", className)}>红绿灯待计算</span>;
  return (
    <span className={cn("inline-flex min-w-0 items-center gap-1.5 rounded border px-2 py-1 text-xs font-bold", tones[light.light], className)}
      title={`${light.summary}\n${light.validation}`}>
      <Circle className={cn("h-2.5 w-2.5 shrink-0", dots[light.light])} />
      <span className="truncate">{light.label}</span>
      {!compact && <span className="font-normal opacity-75">{light.mode === "live" ? "盘中" : "收盘"}</span>}
    </span>
  );
}

export function StockTrafficBadge({ light }: { light?: StockTrafficLight | null }) {
  if (!light) return <span className="text-xs text-muted-foreground/50">待计算</span>;
  return <StockTrafficDetail light={light} />;
}

const signalMeta: Record<TrafficSignal["state"], { label: string; tone: string; icon: typeof Circle }> = {
  healthy: { label: "通过", tone: "text-success", icon: CheckCircle2 },
  risk: { label: "风险", tone: "text-danger", icon: AlertTriangle },
  neutral: { label: "未通过", tone: "text-warning", icon: CircleHelp },
  missing: { label: "缺失", tone: "text-muted-foreground", icon: CircleHelp },
  observed: { label: "已观察", tone: "text-foreground", icon: Info },
  informational: { label: "仅展示", tone: "text-muted-foreground", icon: Info },
};

function StockTrafficDetail({ light }: { light: StockTrafficLight }) {
  const [open, setOpen] = useState(false);
  const [expandedSupport, setExpandedSupport] = useState<string | null>(null);
  const [limitGene, setLimitGene] = useState<LimitGene | null>(null);
  const panelId = useId();
  const quality = light.quality ?? {
    light: light.light, label: light.label, score: 0, max_score: 0,
    summary: light.summary, signals: light.signals, risks: light.blockers, missing: light.missing,
  };
  const action = light.action ?? { light: light.light, label: light.label, summary: light.summary };
  const qualityLabel = containsChinese(quality.label) ? quality.label : "状态待确认";
  const actionShort = light.mode === "settled_promotion" && light.stage === "not_ready"
    ? "封板质量不适用"
    : action.light === "green" ? "可筛选" : action.light === "red" ? "停止新仓" :
      action.light === "gray" ? "结论待确认" : "暂缓新仓";

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => {
    if (!open || !light.code) return;
    setLimitGene({ available: false, reason: "涨停基因暂未接入当前上游版本" });
  }, [open, light.code]);

  return (
    <>
      <span className="inline-flex flex-col items-start gap-1">
        <button type="button" onClick={() => setOpen(true)} aria-expanded={open} aria-controls={open ? panelId : undefined}
          aria-label={`${light.name || light.code} ${qualityLabel}，查看个股因子状况`}
          className={cn("inline-flex items-center gap-1 whitespace-nowrap rounded border px-1.5 py-0.5 text-[11px] font-semibold transition-shadow hover:shadow-sm", tones[quality.light])}>
          <Circle className={cn("h-2 w-2", dots[quality.light])} />{qualityLabel}
          {quality.max_score > 0 && <span className="font-mono opacity-70">{quality.score}/{quality.max_score}</span>}
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
        <span className={cn("inline-flex items-center gap-1 text-[10px] font-medium", textTones[action.light])}>
          <Circle className={cn("h-1.5 w-1.5", dots[action.light])} />{actionShort}
        </span>
      </span>
      {open && createPortal(
        <div className="fixed inset-0 z-[80] flex items-center justify-center p-4" role="presentation">
          <button type="button" className="absolute inset-0 bg-black/25 backdrop-blur-[1px]" aria-label="关闭个股因子状况"
            onClick={() => setOpen(false)} />
          <section id={panelId} role="dialog" aria-modal="true" aria-label="个股因子状况"
            className="relative z-10 max-h-[82dvh] w-full max-w-lg overflow-y-auto rounded-lg border border-border bg-background p-4 shadow-2xl">
            <header className="flex min-w-0 items-start gap-3 border-b border-border pb-3">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  <h3 className="truncate text-sm font-bold">{light.name || light.code}</h3>
                  {light.code && <span className="font-mono text-[11px] text-muted-foreground">{light.code}</span>}
                  <span className={cn("inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-bold", tones[quality.light])}>
                    <Circle className={cn("h-2 w-2", dots[quality.light])} />{qualityLabel}
                  </span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                  {light.mode === "settled_promotion" ? "收盘 · 次日1进2观察" : "盘中 · 打板质量"} · {quality.summary}
                </p>
              </div>
              <button type="button" onClick={() => setOpen(false)} aria-label="关闭"
                className="rounded p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground">
                <X className="h-4 w-4" />
              </button>
            </header>

            <div className="border-b border-border/70 py-3 text-xs" aria-label="环境状态">
              <div className="flex items-center justify-between gap-3">
                <span className="flex items-center gap-1"><b>环境状态</b><Caliber mobileCentered text={environmentRules()} /></span>
                <span className={cn("font-semibold", textTones[light.environment?.light ?? "gray"])}>
                  {light.environment?.label || "环境数据缺失"}
                </span>
              </div>
                <p className="mt-1 text-muted-foreground">
                {light.environment?.source === "live" ? "盘中环境" : light.environment?.source === "settled" ? "收盘环境" : "没有对应环境数据"}
                {light.environment?.as_of ? ` · ${light.environment.as_of}` : ""}
              </p>
            </div>

            <div className="border-b border-border/70 py-3">
              <div className="mb-1.5 flex items-center justify-between gap-3 text-xs">
                <span className="flex items-center gap-1">
                  <b>个股质量</b>
                  <Caliber text={light.mode === "settled_promotion" ? SETTLED_QUALITY_RULES : LIVE_QUALITY_RULES} />
                </span>
                {quality.max_score > 0 && <span className="font-mono text-muted-foreground">{quality.score}/{quality.max_score}</span>}
              </div>
              {light.direction?.type === "theme" && (
                <p className="mb-1 text-[11px] text-muted-foreground">
                  题材归组：<b className="text-foreground">{light.direction.name}</b> · 复用复盘题材事件树
                </p>
              )}
              {light.direction?.type === "catalyst_only" && (
                <p className="mb-1 text-[11px] text-warning">仅有共同催化，未形成可计分的产业题材归组</p>
              )}
              <div className="divide-y divide-border/70" aria-label="质量因子明细">
              {safeArray<TrafficSignal>(quality.signals).map((signal) => {
                const meta = signalMeta[signal.state] || signalMeta.informational;
                const Icon = meta.icon;
                const peers = (signal.members ?? []).filter((member) => member.code !== light.code);
                const isSupport = signal.key === "theme" || signal.key === "industry";
                const canExpandSupport = isSupport && (signal.members?.length ?? 0) > 0;
                const supportExpanded = expandedSupport === signal.key;
                const supportLabel = signal.key === "industry" ? "同行业" : "同题材";
                return (
                  <div key={signal.key} className="py-2.5 text-xs">
                    <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-3">
                      <span className="flex min-w-0 items-center gap-2">
                        <Icon className={cn("h-3.5 w-3.5 shrink-0", meta.tone)} />
                        <span className="truncate font-medium">{signal.label}</span>
                      </span>
                      <span className="flex min-w-0 items-center justify-end gap-2 text-right">
                        {canExpandSupport ? (
                          <button type="button" onClick={() => setExpandedSupport((value) => value === signal.key ? null : signal.key)}
                            aria-expanded={supportExpanded} aria-label={`${supportExpanded ? "收起" : "查看"}${supportLabel}其他封板标的`}
                            className="inline-flex max-w-[15rem] items-center gap-1 break-words text-primary underline-offset-2 hover:underline">
                            <Users className="h-3.5 w-3.5 shrink-0" />{signal.detail}<ChevronDown className={cn("h-3 w-3 shrink-0 transition-transform", supportExpanded && "rotate-180")} />
                          </button>
                        ) : <span className="max-w-[15rem] break-words text-muted-foreground">{signal.detail}</span>}
                        <span className={cn("w-12 shrink-0 text-[11px] font-semibold", meta.tone)}>{meta.label}</span>
                      </span>
                    </div>
                    {signal.catalyst_detail && (
                      <p className="mt-1 pl-5 text-[11px] text-muted-foreground">{signal.catalyst_detail}</p>
                    )}
                    {canExpandSupport && supportExpanded && (
                      <div className="mt-2 rounded border border-border bg-muted/25 p-2.5" aria-label={`${supportLabel}其他封板标的`}>
                        <p className="mb-1.5 text-[11px] font-medium text-muted-foreground">
                          {supportLabel}{signal.group_name ? `「${signal.group_name}」` : signal.key === "theme" && light.direction?.name ? `「${light.direction.name}」` : ""}其他封板标的
                        </p>
                        {peers.length ? <div className="space-y-1.5">{peers.map((member) => (
                          <div key={member.code || member.name} className="flex flex-wrap items-center justify-between gap-x-3 gap-y-0.5 text-xs">
                            <span className="font-medium">{member.name || member.code || "未知标的"} {member.code && <span className="font-mono text-[11px] text-muted-foreground">{member.code}</span>}</span>
                            <span className="text-[11px] text-muted-foreground">首封 {member.seal_time || "未知"} · 开板 {member.break_count ?? "—"} 次</span>
                          </div>
                        ))}</div> : <p className="text-[11px] text-muted-foreground">除当前标的外暂无其他{supportLabel}封板标的。</p>}
                      </div>
                    )}
                  </div>
                );
              })}
              {safeArray<TrafficSignal>(quality.signals).length === 0 && (
                <p className="py-2.5 text-xs text-muted-foreground">该快照暂未生成质量因子明细，请刷新数据后查看。</p>
              )}
              </div>
            </div>

            <div className="border-b border-border/70 py-3 text-xs" aria-label="涨停基因">
              <div className="mb-1.5 flex items-center gap-1"><b>涨停基因</b><Caliber text="数据源：腾讯日K（优先使用不复权序列；数据源未提供时会作明确提示）。\n\n近一年涨停次数仅统计收盘封住涨停；盘中触板但收盘未封不计入。封板成功率 = 收盘封住 ÷ 日内触及涨停。\n\n股性评分（本站）= 年度涨停活跃度 40% + 封板成功率 40% + 近60日活跃度 20%，不等同于同花顺专有评分。" /></div>
              {!limitGene && <p className="text-muted-foreground">正在计算近一年数据…</p>}
              {limitGene && !limitGene.available && <p className="text-muted-foreground">{limitGene.reason || "涨停基因数据暂不可用"}</p>}
              {limitGene?.available && <><div className="grid grid-cols-3 gap-2">
                <div className="rounded border border-border bg-muted/20 p-2"><p className="text-[11px] text-muted-foreground">近一年涨停</p><p className="mt-0.5 font-mono text-sm font-bold">{limitGene.limit_up_count} 次</p></div>
                <div className="rounded border border-border bg-muted/20 p-2"><p className="text-[11px] text-muted-foreground">股性评分</p><p className="mt-0.5 font-mono text-sm font-bold">{limitGene.stock_character_score} / 100</p></div>
                <div className="rounded border border-border bg-muted/20 p-2"><p className="text-[11px] text-muted-foreground">封板成功率</p><p className="mt-0.5 font-mono text-sm font-bold">{limitGene.seal_success_rate == null ? "—" : `${(limitGene.seal_success_rate * 100).toFixed(1)}%`}</p></div>
              </div><p className="mt-2 text-[11px] leading-relaxed text-muted-foreground">{limitGene.note} · 样本 {limitGene.window_days} 个交易日（{limitGene.from} 至 {limitGene.to}）</p></>}
            </div>

            <div className="border-b border-border/70 py-3 text-xs" aria-label="最终出手结论">
              <div className="flex items-center justify-between gap-3">
                <b>最终出手结论</b>
                <span className={cn("font-semibold", textTones[action.light])}>{action.label}</span>
              </div>
              <p className="mt-1 leading-relaxed text-muted-foreground">{action.summary}</p>
            </div>

            {(light.blockers.length > 0 || light.missing.length > 0) && (
              <div className="mt-3 space-y-1 border-t border-border pt-3 text-xs">
                {light.blockers.length > 0 && <p className="text-danger">暂不纳入候选：{light.blockers.map(describeStockBlocker).join("；")}</p>}
                {light.missing.length > 0 && <p className="text-warning">本次未取到：{light.missing.map(describeStockMissing).join("；")}</p>}
              </div>
            )}
            <p className="mt-3 border-t border-border pt-3 text-[11px] leading-relaxed text-muted-foreground">
              未覆盖：{light.uncovered.join("、") || "无"}。个股灯是规则准入参考，不代表成交能力或买入建议。
            </p>
          </section>
        </div>, document.body,
      )}
    </>
  );
}

export function BoardTrafficLightPanel({ light, live = false }: {
  light?: BoardTrafficLight | null; live?: boolean;
}) {
  if (!light) return null;
  const dimensions = [light.dimensions?.market, light.dimensions?.emotion, light.dimensions?.board].filter(Boolean);
  return (
    <section aria-label={live ? "盘中打板红绿灯" : "收盘打板红绿灯"}
      className={cn("min-w-0 border-y border-border py-4", light.light === "red" && "border-danger/50 bg-danger/5")}>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <h2 className="flex items-center gap-2 text-sm font-bold"><TrafficCone className="h-4 w-4" />打板红绿灯</h2>
        <TrafficLightBadge light={light} />
        <span className="text-[11px] text-muted-foreground">{light.date || "—"} {light.as_of || ""}</span>
        <Caliber mobileCentered text={
          environmentRules(light) + "\n\n" +
          "个股绿灯还要通过临板或封板质量；它不是胜率、成交能力或下单指令。\n" +
          "当前未覆盖 Level-2 买一封单、撤单变化和排队位置。"
        } />
      </div>
      <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{light.summary}</p>
      <div className="mt-3 grid border-t border-border/60 sm:grid-cols-3">
        {dimensions.map((item, index) => (
          <div key={item.key} className={cn("min-w-0 py-3 sm:px-3", index > 0 && "border-t border-border/50 sm:border-l sm:border-t-0")}>
            <div className="flex items-center gap-2">
              <Circle className={cn("h-3 w-3 shrink-0", dots[item.light])} />
              <b className="text-sm">{item.label}</b>
              <span className={cn("ml-auto text-xs font-semibold", textTones[item.light])}>{item.light_label}</span>
            </div>
            <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{item.summary}</p>
            <div className="mt-2 space-y-1">
              {safeArray<TrafficSignal>(item.signals).map((signal) => (
                <p key={signal.key} className="flex min-w-0 justify-between gap-2 text-[11px]">
                  <span className="text-muted-foreground">{signal.label}</span>
                  <span className={cn("min-w-0 truncate text-right", signal.state === "risk" && "text-danger")}>{signal.detail}</span>
                </p>
              ))}
            </div>
          </div>
        ))}
      </div>
      {light.hard_vetoes.length > 0 && (
        <div className="mt-2 flex items-start gap-1.5 text-xs text-danger">
          <ShieldAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div>
            <span className="font-medium">硬否决（停止新开仓）：</span>
            <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
              {light.hard_vetoes.map((veto) => <li key={veto}>{describeHardVeto(light, veto)}</li>)}
            </ul>
          </div>
        </div>
      )}
      {light.missing.length > 0 && (
        <p className="mt-2 flex items-start gap-1.5 text-xs text-warning">
          <CircleHelp className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          缺少：{light.missing.map(describeStockMissing).join("；")}
        </p>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground/60">{light.validation}</p>
    </section>
  );
}
