import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Check, Clock3, History, LockKeyhole, RefreshCw, Save, Send, SlidersHorizontal } from "lucide-react";
import { apiUrl } from "@/lib/base";
import { cn } from "@/lib/utils";
import { pctColor } from "@/lib/colors";
import { StockLink } from "@/components/ui/StockLink";
import type { FollowupData, FollowupDirection, FollowupItem, FollowupMember, MemberStats } from "@/lib/followup";

type View = "latest" | "history" | "final";
function dateTime(value?: string | null) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}
function valueText(item: FollowupItem, value?: number | null, delta = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  const sign = value > 0 && (delta || item.unit === "%") ? "+" : "";
  if (!item.unit) return `${sign}${(value * 100).toFixed(1)}${delta ? " 个百分点" : "%"}`;
  if (item.unit === "%") return `${sign}${value.toFixed(2)}${delta ? " 个百分点" : "%"}`;
  return `${sign}${value}${item.unit}`;
}
function stateTone(state?: string) {
  if (state === "成立" || state === "暂时支持") return "text-primary";
  if (state === "不成立" || state === "暂时相反") return "text-warning";
  return "text-muted-foreground";
}
async function post(path: string, body: unknown) {
  const response = await fetch(apiUrl(path), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function Members({ members }: { members: FollowupMember[] }) {
  return <div className="overflow-x-auto">
    <table className="w-full min-w-[480px] text-left text-xs tabular-nums">
      <thead className="text-muted-foreground"><tr>
        {["N 日成员", "代码", "最新涨跌", "状态", "行情时间"].map((h) => <th key={h} className="py-2 pr-3 font-medium">{h}</th>)}
      </tr></thead>
      <tbody>{members.map((s) => <tr key={s.code} className="border-t border-border/50">
        <td className="py-2 pr-3"><StockLink code={s.code}>{s.name}</StockLink></td>
        <td className="pr-3 text-muted-foreground"><StockLink code={s.code}>{s.code}</StockLink></td>
        <td className={cn("pr-3", pctColor(s.pct))}>{valueText({ unit: "%" } as FollowupItem, s.pct)}</td>
        <td className="pr-3">{s.limit_down ? "跌停" : s.limit_up ? "涨停" : s.reason || (s.limit_up === false ? "未封板" : "未取得行情")}</td>
        <td className="text-muted-foreground">{dateTime(s.quote_time)}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function Stats({ stats }: { stats: MemberStats }) {
  return <div className="my-2 flex flex-wrap gap-x-5 gap-y-1 text-xs tabular-nums">
    <span>中位涨幅 <b className={pctColor(stats.median)}>{valueText({ unit: "%" } as FollowupItem, stats.median)}</b></span>
    <span>上涨 {stats.up_count ?? "—"}</span><span>涨停 {stats.limit_up_count ?? "—"}</span><span>跌停 {stats.limit_down_count ?? "—"}</span>
    <span className="text-muted-foreground">有效行情 {stats.valid}/{stats.sample}（{stats.coverage == null ? "—" : `${Math.round(stats.coverage * 100)}%`}）</span>
  </div>;
}

function Direction({ direction: d, data, date, onSaved }: {
  direction: FollowupDirection; data: FollowupData; date: string; onSaved: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [tags, setTags] = useState(d.tags);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setTags(d.tags); }, [d.tags.join("|")]);
  const save = async () => {
    setSaving(true); setError("");
    try {
      await post(`/api/review/followup/bindings?date=${date}`, { bindings: { [d.id]: tags } });
      setEditing(false); onSaved();
    } catch (e) { setError(e instanceof Error ? e.message : "保存失败"); }
    finally { setSaving(false); }
  };
  return <section className="min-w-0 border-t border-border py-4">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h5 className="break-words text-sm font-semibold">{d.direction}</h5>
      <button type="button" disabled={!data.editable} onClick={() => setEditing(!editing)} title={data.editable ? "确认 N 日题材关联" : "原名单已锁定"}
        aria-label={`确认 ${d.direction} 题材关联`} className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border text-muted-foreground hover:text-primary disabled:opacity-40">
        {data.editable ? <SlidersHorizontal className="h-4 w-4" /> : <LockKeyhole className="h-3.5 w-3.5" />}
      </button>
    </div>
    <p className="mt-1 break-words text-xs text-muted-foreground">N 日关联：{d.tags.join("、") || "未关联"}{d.binding_source && d.tags.length > 0 ? ` · ${d.binding_source}` : ""}{!d.complete && d.tags.length > 0 ? " · 原名单不完整" : ""}</p>
    {!d.tags.length && d.suggested_tags?.length ? <p className="mt-1 break-words text-xs text-warning">可核查题材：{d.suggested_tags.join("、")} · {d.suggestion_source}；历史名单已锁定，未计入正式跟踪。</p> : null}
    <details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">N 日原始依据与风险</summary>
      <p className="mt-1 break-words">{d.logic}</p><p className="mt-1 break-words">{d.risk}</p>
    </details>
    {d.binding_reason && <p className="mt-1 text-xs text-warning">{data.editable ? d.binding_reason : "未关联 N 日题材；原名单已锁定"}</p>}
    {editing && data.editable && <div className="my-3 border-y border-dashed border-border py-3">
      <div className="flex max-h-48 flex-wrap gap-x-5 gap-y-2 overflow-y-auto">
        {(data.prediction?.universe.themes || []).map((t) => <label key={t.tag} className="inline-flex items-center gap-2 text-xs">
          <input type="checkbox" checked={tags.includes(t.tag)} onChange={(e) => setTags(e.target.checked ? [...tags, t.tag] : tags.filter((x) => x !== t.tag))} />
          {t.tag}（{t.members.length}）
        </label>)}
        {!data.prediction?.universe.themes.length && <span className="text-xs text-muted-foreground">N 日没有可关联题材</span>}
      </div>
      <button type="button" onClick={save} disabled={saving} className="mt-3 inline-flex items-center gap-1.5 rounded border border-border px-3 py-1.5 text-xs disabled:opacity-50">
        <Save className="h-3.5 w-3.5" />{saving ? "保存中" : "确认关联"}
      </button>
    </div>}
    {error && <p role="alert" className="my-2 text-xs text-warning">{error}</p>}
    {d.stats && d.stats.sample > 0 && <Stats stats={d.stats} />}
    {d.members.length > 0 && <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground">固定成员明细（{d.members.length}）</summary>
      <Members members={d.stats?.members || d.members} />
    </details>}
    {d.explicit_members.length > 0 && <details className="mt-2 text-xs">
      <summary className="cursor-pointer text-muted-foreground">原报告观察股（{d.explicit_members.length}）</summary>
      {d.explicit && <Stats stats={d.explicit} />}<Members members={d.explicit?.members || d.explicit_members} />
    </details>}
    {d.unresolved.length > 0 && <p className="mt-2 text-xs text-warning">未能唯一解析：{d.unresolved.join("、")}</p>}
  </section>;
}

export function FollowupPanel({ date, revision = 0, onEditableChange }: {
  date: string; revision?: number; onEditableChange: (editable: boolean) => void;
}) {
  const [view, setView] = useState<View>("latest");
  const [at, setAt] = useState("");
  const [data, setData] = useState<FollowupData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [saving, setSaving] = useState(false);
  const [noteError, setNoteError] = useState("");
  const request = useRef<AbortController | null>(null);
  useEffect(() => { onEditableChange(false); }, [date, onEditableChange]);
  const load = useCallback(async () => {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    setBusy(true);
    try {
      const selected = view === "final" ? "final" : view === "history" ? at : "";
      const response = await fetch(apiUrl(`/api/review/followup?date=${date}${selected ? `&at=${encodeURIComponent(selected)}` : ""}`), { signal: controller.signal });
      const body = await response.json();
      if (!response.ok) throw new Error(body.error || "读取跟踪数据失败");
      if (controller.signal.aborted) return;
      setData(body); setError(""); onEditableChange(body.editable && body.available);
    } catch (e) {
      if (!controller.signal.aborted) { setError(e instanceof Error ? e.message : "读取失败"); onEditableChange(false); }
    } finally { if (!controller.signal.aborted) setBusy(false); }
  }, [date, at, view, onEditableChange]);
  useEffect(() => {
    void load();
    return () => { request.current?.abort(); };
  }, [load, revision]);
  useEffect(() => {
    if (view === "history" || (view === "final" && data?.final)) return;
    const timer = window.setInterval(() => { if (!document.hidden) void load(); }, 30_000);
    const visible = () => { if (!document.hidden) void load(); };
    document.addEventListener("visibilitychange", visible);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", visible); };
  }, [load, view, Boolean(data?.final)]);
  const addNote = async () => {
    setSaving(true); setNoteError("");
    try { await post(`/api/review/followup/notes?date=${date}`, { text: note }); setNote(""); await load(); }
    catch (e) { setNoteError(e instanceof Error ? e.message : "备注保存失败"); }
    finally { setSaving(false); }
  };
  const snapshot = view === "final" ? data?.final || (data?.snapshot?.stage === "closing" ? data.snapshot : null) : data?.snapshot;
  const items = snapshot?.items || data?.prediction?.items || [];
  const directions = snapshot?.directions || data?.prediction?.directions || [];
  const counts = snapshot?.items.reduce<Record<string, number>>((acc, r) => { const key = r.state || "数据不足"; acc[key] = (acc[key] || 0) + 1; return acc; }, {}) || {};
  return <section className="mt-6 min-w-0 border-t-2 border-border pt-4" aria-label="次日跟踪">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <h4 className="flex items-center gap-2 text-sm font-semibold"><History className="h-4 w-4 text-primary" />次日跟踪</h4>
      <button type="button" onClick={() => void load()} disabled={busy} aria-label="刷新跟踪记录" title="读取后台最新记录"
        className="inline-flex h-8 w-8 items-center justify-center rounded border border-border hover:text-primary disabled:opacity-50">
        <RefreshCw className={cn("h-4 w-4", busy && "animate-spin")} />
      </button>
    </div>
    <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span>预测 {date}</span><span>跟踪 {data?.eval_date || "下一交易日待确认"}</span>
      <span className="inline-flex items-center gap-1"><Clock3 className="h-3 w-3" />采集于 {dateTime(snapshot?.observed_at)}</span>
      <span>{data?.status.phase || "读取中"}</span>
    </div>
    {data?.cutoff_at && <p className="mt-1 text-xs text-muted-foreground">条件锁定：{dateTime(data.cutoff_at)} · 北京时间</p>}
    {data?.prediction && !data.cutoff_at && <p className="mt-1 text-xs text-muted-foreground">条件锁定：下一交易日 09:15 · 北京时间</p>}
    {data && !data.status.enabled && <p className="mt-2 text-xs text-warning">后台采集已关闭</p>}
    {(error || data?.status.error || data?.status.delayed) && <p role="alert" className="mt-2 flex items-start gap-1.5 text-xs text-warning">
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" /><span className="min-w-0 break-words">{error || data?.status.error || "采集延迟超过 180 秒"} · 最后成功：{dateTime(data?.status.last_success_at)}</span>
    </p>}
    {data?.available && data.eval_date && !data.eligible && <p className="mt-2 text-xs text-warning">缺少盘前保存的预测版本，仅供历史对照，不计入正式命中率。</p>}
    {data?.prediction && <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">原始判断 · 记录于 {dateTime(data.prediction.recorded_at)}</summary>
      <p className="mt-2 break-words">{data.prediction.focus?.market_oneliner}</p>
      {data.prediction.focus?.risk_alerts?.map((risk, i) => <p key={i} className="mt-1 break-words">{risk}</p>)}
    </details>}
    <div className="my-4 flex flex-wrap items-center gap-3">
      <div className="inline-flex rounded border border-border p-0.5" role="group" aria-label="跟踪视图">
        {([["latest", "最新"], ["history", "盘中回看"], ["final", "收盘结果"]] as const).map(([key, label]) => <button key={key} type="button"
          aria-pressed={view === key} onClick={() => { setView(key); setData((old) => old ? { ...old, snapshot: null } : old); if (key === "history" && !at) setAt(data?.times.slice(-1)[0] || ""); }}
          className={cn("min-h-8 rounded px-3 text-xs", view === key ? "bg-muted font-semibold text-foreground" : "text-muted-foreground hover:text-primary")}>{label}</button>)}
      </div>
      {view === "history" && <select aria-label="盘中记录时间" value={at} onChange={(e) => setAt(e.target.value)} className="min-h-9 max-w-full rounded border border-border bg-card px-2 text-xs">
        {!data?.times.length && <option value="">无已记录快照</option>}
        {data?.times.map((t) => <option key={t} value={t}>{`${t.slice(0, 4)}-${t.slice(4, 6)}-${t.slice(6, 8)} ${t.slice(9, 11)}:${t.slice(11, 13)}`}</option>)}
      </select>}
    </div>
    {!data?.available && !busy && <p className="py-3 text-sm text-muted-foreground">跟踪档案尚未建立，后台将在下一轮检查复盘存档。</p>}
    {view === "final" && !snapshot && <p className="mb-3 text-xs text-muted-foreground">尚无收盘数据</p>}
    {view === "history" && !data?.times.length && <p className="mb-3 text-xs text-muted-foreground">没有保存过盘中快照，未补造历史。</p>}
    {snapshot?.stage === "final" ? <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
      <span className="inline-flex items-center gap-1.5"><Check className="h-4 w-4 text-primary" />成立 {snapshot.summary.hit}/{snapshot.summary.decided} 条可判定条件</span>
      <span className="text-xs text-muted-foreground">无法判定 {snapshot.summary.undecidable} 条</span>
      {snapshot.summary.rate != null && <span className="text-xs">条件命中率 {(snapshot.summary.rate * 100).toFixed(0)}%</span>}
    </div> : snapshot?.stage === "closing" ? <p className="mb-3 text-xs text-warning">收盘数据待补齐，当前读数尚未定稿：{Object.entries(counts).map(([key, n]) => `${key} ${n} 条`).join(" · ")}</p>
      : snapshot && <p className="mb-3 text-xs text-muted-foreground">盘中暂评：{Object.entries(counts).map(([key, n]) => `${key} ${n} 条`).join(" · ")}</p>}
    {items.length > 0 && <div className="md:hidden">
      {items.map((it) => <div key={it.metric} className="border-t border-border py-3 text-xs">
        <div className="flex flex-wrap items-baseline justify-between gap-2"><b className="break-words">{it.label || it.metric}</b><span className={stateTone(it.state)}>{it.state || "待观察"}</span></div>
        <p className="my-2 break-words leading-relaxed text-muted-foreground">{it.reason}</p>
        <dl className="grid grid-cols-2 gap-x-3 gap-y-2 tabular-nums">
          {[["预期", it.direction], ["有效样本", it.sample == null ? "—" : `${it.valid ?? 0}/${it.sample}`],
            ["N 日基准", valueText(it, it.base_value)], ["N+1 读数", valueText(it, it.current_value)],
            ["变化阈值", valueText(it, it.eps, true).replace(/^\+/, "")], ["差值", valueText(it, it.delta, true)]].map(([label, value]) => <div key={label} className="min-w-0"><dt className="text-[11px] text-muted-foreground">{label}</dt><dd className="mt-0.5 break-words font-medium">{value}</dd></div>)}
        </dl>
        {it.reason_detail && <p className="mt-2 break-words text-[11px] text-muted-foreground">{it.reason_detail}</p>}
      </div>)}
    </div>}
    {items.length > 0 && <div className="hidden overflow-x-auto md:block">
      <table className="w-full min-w-[780px] text-left text-xs tabular-nums">
        <thead className="text-muted-foreground"><tr>{["验证条件", "预期", "N 日基准", "变化阈值", "N+1 读数", "差值", "反馈", "有效样本"].map((h) => <th key={h} className="py-2 pr-3 font-medium">{h}</th>)}</tr></thead>
        <tbody>{items.map((it) => <tr key={it.metric} className="border-t border-border/60">
          <td className="max-w-52 py-3 pr-3"><div className="font-medium">{it.label || it.metric}</div><div className="mt-1 text-[11px] text-muted-foreground">{it.reason}</div></td>
          <td className="pr-3">{it.direction}</td><td className="pr-3">{valueText(it, it.base_value)}</td>
          <td className="pr-3">{valueText(it, it.eps, true).replace(/^\+/, "")}</td><td className="pr-3 font-semibold">{valueText(it, it.current_value)}</td>
          <td className="pr-3">{valueText(it, it.delta, true)}</td>
          <td className={cn("max-w-48 pr-3", stateTone(it.state))}><div>{it.state || "待观察"}</div>{it.reason_detail && <div className="mt-1 text-[11px] text-muted-foreground">{it.reason_detail}</div>}</td>
          <td>{it.sample == null ? "—" : `${it.valid ?? 0}/${it.sample}`}</td>
        </tr>)}</tbody>
      </table>
    </div>}
    {data?.prediction && <p className="my-3 text-[11px] leading-relaxed text-muted-foreground">市场反馈基准样本：{data.prediction.baseline_sample_date || "N-1"} 涨停股；次日反馈样本：{date} 涨停股。方向名单固定于 N 日，各方向可能有重叠成员。</p>}
    {directions.map((d) => <Direction key={`${date}-${d.id}`} direction={d} data={data!} date={date} onSaved={() => void load()} />)}
    {data?.available && <div className="border-t border-border pt-3">
      <h5 className="mb-2 text-xs font-semibold">跟踪备注</h5>
      <div className="space-y-2">{data.notes.map((n) => <div key={n.id} className="border-l-2 border-border pl-3 text-xs"><div className="text-[11px] text-muted-foreground">{dateTime(n.created_at)}</div><p className="mt-0.5 whitespace-pre-wrap break-words">{n.text}</p></div>)}</div>
      <div className="mt-3 flex items-end gap-2">
        <textarea aria-label="追加跟踪备注" value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} rows={2} placeholder="追加备注"
          className="min-w-0 flex-1 resize-y rounded border border-border bg-card px-3 py-2 text-xs" />
        <button type="button" onClick={addNote} disabled={saving || !note.trim()} aria-label="保存跟踪备注" title="保存备注"
          className="inline-flex h-9 w-9 shrink-0 items-center justify-center rounded border border-border text-primary disabled:opacity-40"><Send className="h-4 w-4" /></button>
      </div>
      {noteError && <p role="alert" className="mt-2 text-xs text-warning">{noteError}</p>}
    </div>}
  </section>;
}
