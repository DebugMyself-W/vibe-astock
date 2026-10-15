import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, Images, Loader2, RotateCcw, Trash2, Upload, X } from "lucide-react";
import { agentPost } from "@/lib/agent";
import type { JournalImportDraft, JournalImportPreview } from "@/lib/agent";
import { cn } from "@/lib/utils";
import { toast } from "sonner";

const TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const MAX_FILES = 20;
const MAX_BYTES = 10 * 1024 * 1024;
const PLAYBOOKS = ["打板", "低吸", "接力", "半路", "套利", "其它"];

type FileItem = {
  id: string; file: File; url: string;
  status: "pending" | "running" | "done" | "error"; error?: string;
};
type ReviewDraft = JournalImportDraft & {
  client_id: string; source_hashes: string[]; selected: boolean; action: "" | "create" | "append" | "skip";
  trade_id: string; allow_duplicate: boolean; expanded: boolean;
};

function dataURL(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("读取图片失败"));
    reader.readAsDataURL(file);
  });
}

function validDraft(draft: ReviewDraft): boolean {
  if (!draft.selected || draft.action === "skip") return true;
  if (!/^\d{6}$/.test(draft.code) || !draft.fills.length || !draft.action) return false;
  if (draft.action === "append" && !draft.trade_id) return false;
  if (draft.action === "create" && !draft.fills.some(fill => fill.side === "buy")) return false;
  return draft.fills.every(fill => /^20\d{2}-\d{2}-\d{2}$/.test(fill.date)
    && Number.isFinite(fill.price) && fill.price > 0
    && Number.isFinite(fill.shares) && fill.shares > 0
    && (fill.fee == null || Number.isFinite(fill.fee) && fill.fee >= 0));
}

export function JournalImageImport({ onImported }: { onImported: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [files, setFiles] = useState<FileItem[]>([]);
  const [drafts, setDrafts] = useState<ReviewDraft[]>([]);
  const [dragging, setDragging] = useState(false);
  const [running, setRunning] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const generation = useRef(0);

  useEffect(() => {
    if (!open) return;
    const dialog = dialogRef.current;
    dialog?.showModal();
    return () => { if (dialog?.open) dialog.close(); };
  }, [open]);

  const patchDraft = (id: string, patch: Partial<ReviewDraft>) =>
    setDrafts(current => current.map(draft => draft.draft_id === id ? { ...draft, ...patch } : draft));

  const patchFill = (id: string, index: number, patch: Partial<ReviewDraft["fills"][number]>) =>
    setDrafts(current => current.map(draft => draft.draft_id !== id ? draft : {
      ...draft, fills: draft.fills.map((fill, i) => i === index ? { ...fill, ...patch } : fill),
    }));

  const moveFill = (id: string, index: number, direction: -1 | 1) =>
    setDrafts(current => current.map(draft => {
      if (draft.draft_id !== id) return draft;
      const target = index + direction;
      if (target < 0 || target >= draft.fills.length) return draft;
      const fills = [...draft.fills];
      [fills[index], fills[target]] = [fills[target], fills[index]];
      return { ...draft, fills };
    }));

  const close = () => {
    generation.current += 1;
    files.forEach(item => URL.revokeObjectURL(item.url));
    setFiles([]); setDrafts([]); setMessage(""); setRunning(false); setOpen(false);
  };

  const recognize = async (added: FileItem[]) => {
    if (!added.length) return;
    const run = ++generation.current;
    setRunning(true); setMessage("");
    for (const item of added) {
      if (run !== generation.current) return;
      setFiles(current => current.map(row => row.id === item.id ? { ...row, status: "running", error: undefined } : row));
      try {
        const content_b64 = await dataURL(item.file);
        const result = await agentPost<JournalImportPreview>("/api/journal/import/preview", {
          client_id: item.id, mime_type: item.file.type, content_b64,
        });
        if (run !== generation.current) return;
        setDrafts(current => [...current, ...result.drafts.map(draft => ({
          ...draft, client_id: item.id, source_hashes: [draft.source_hash], selected: !draft.duplicate,
          action: draft.existing_candidates.length ? "" as const : "create" as const,
          trade_id: "", allow_duplicate: false, expanded: true,
        }))]);
        setFiles(current => current.map(row => row.id === item.id ? { ...row, status: "done" } : row));
      } catch (error) {
        if (run !== generation.current) return;
        const text = error instanceof Error ? error.message : "识别失败";
        setFiles(current => current.map(row => row.id === item.id ? { ...row, status: "error", error: text } : row));
      }
    }
    if (run === generation.current) setRunning(false);
  };

  const addFiles = (incoming: File[]) => {
    if (running) { setMessage("请等待当前图片识别完成后再继续添加"); return; }
    const remaining = MAX_FILES - files.length;
    const accepted = incoming.filter(file => TYPES.has(file.type) && file.size <= MAX_BYTES).slice(0, remaining);
    const rejected = incoming.length - accepted.length;
    if (rejected) setMessage(`有 ${rejected} 张图片因格式、大小或 20 张上限未加入`);
    const added = accepted.map((file, index): FileItem => ({
      id: `${Date.now()}-${index}-${Math.random().toString(36).slice(2)}`,
      file, url: URL.createObjectURL(file), status: "pending",
    }));
    setFiles(current => [...current, ...added]);
    void recognize(added);
  };

  const removeFile = (id: string) => {
    const found = files.find(item => item.id === id);
    if (found) URL.revokeObjectURL(found.url);
    setFiles(current => current.filter(item => item.id !== id));
    setDrafts(current => current.filter(draft => draft.client_id !== id));
  };

  const retry = (item: FileItem) => {
    if (running) { setMessage("请等待当前识别任务完成后再重试"); return; }
    setDrafts(current => current.filter(draft => draft.client_id !== item.id));
    void recognize([{ ...item, status: "pending", error: undefined }]);
  };

  const mergeSameCode = () => {
    const active = drafts.filter(draft => draft.selected && draft.action !== "skip" && /^\d{6}$/.test(draft.code));
    const groups = new Map<string, ReviewDraft[]>();
    active.forEach(draft => groups.set(draft.code, [...(groups.get(draft.code) || []), draft]));
    const mergedIds = new Set<string>();
    const replacements = new Map<string, ReviewDraft>();
    groups.forEach(group => {
      if (group.length < 2) return;
      const [first, ...rest] = group;
      rest.forEach(draft => mergedIds.add(draft.draft_id));
      replacements.set(first.draft_id, {
        ...first,
        fills: group.flatMap(draft => draft.fills),
        source_hashes: [...new Set(group.flatMap(draft => draft.source_hashes))],
        warnings: group.flatMap(draft => draft.warnings),
        duplicate: group.some(draft => draft.duplicate),
      });
    });
    if (!mergedIds.size) { setMessage("没有可合并的同代码草稿"); return; }
    setDrafts(current => current.filter(draft => !mergedIds.has(draft.draft_id))
      .map(draft => replacements.get(draft.draft_id) || draft));
    setMessage(`已合并 ${mergedIds.size} 条同代码草稿，请重新核对成交顺序`);
  };

  const submit = async () => {
    const selected = drafts.filter(draft => draft.selected && draft.action !== "skip");
    if (!selected.length) { setMessage("请选择至少一条草稿"); return; }
    const invalid = selected.find(draft => !validDraft(draft));
    if (invalid) { setMessage(`“${invalid.name || invalid.code || "未识别证券"}”仍有必填项或入库方式未确认`); return; }
    setSaving(true); setMessage("");
    try {
      const result = await agentPost<{ok: boolean; created: number; updated: number; skipped: number}>(
        "/api/journal/import/commit", { items: drafts.map(draft => ({
          action: draft.selected ? draft.action || "skip" : "skip",
          trade_id: draft.trade_id || undefined, source_hash: draft.source_hash,
          source_hashes: draft.source_hashes, allow_duplicate: draft.allow_duplicate,
          code: draft.code, name: draft.name, playbook: draft.playbook,
          as_planned: draft.as_planned, note: draft.note,
          date: draft.fills.find(fill => fill.side === "buy")?.date || draft.fills[0]?.date,
          fills: draft.fills.map(({side, date, price, shares, fee}) => ({side, date, price, shares, ...(fee == null ? {} : {fee})})),
        })) },
      );
      await onImported();
      const done = `已新建 ${result.created} 条、追加 ${result.updated} 条、跳过 ${result.skipped} 条`;
      close();
      toast.success(done);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "批量入库失败，账本未改变");
    } finally { setSaving(false); }
  };

  if (!open) return (
    <button type="button" onClick={() => setOpen(true)}
      className="flex items-center gap-1.5 rounded-lg border border-primary/40 px-3 py-2 text-sm font-semibold text-primary hover:bg-primary/10">
      <Images className="h-4 w-4" />批量识别交易截图
    </button>
  );

  if (typeof document === "undefined") return null;

  return createPortal(
    <dialog ref={dialogRef} onCancel={event => { event.preventDefault(); if (!saving) close(); }}
      aria-label="批量识别交易截图"
      className="m-auto w-[calc(100%-1.5rem)] max-w-6xl overflow-hidden rounded-2xl border border-border bg-background p-0 text-foreground shadow-2xl backdrop:bg-black/60 backdrop:backdrop-blur-sm sm:w-[calc(100%-3rem)]">
      <div data-testid="journal-image-import-panel" className="flex max-h-[92dvh] min-h-[min(34rem,92dvh)] flex-col">
        <div className="flex shrink-0 items-start justify-between gap-4 border-b border-border px-4 py-4 sm:px-6">
          <div><h2 className="text-lg font-bold">批量识别交易截图</h2><p className="mt-1 text-xs text-muted-foreground">图片只在本机后端内存中 OCR，不调用 AI、不保存原图。识别结果必须校对后才会写账。</p></div>
          <button type="button" onClick={close} disabled={saving} aria-label="关闭" className="rounded-lg p-2 hover:bg-muted disabled:opacity-50"><X className="h-5 w-5" /></button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-5 sm:px-6">
        <div onDragOver={event => { event.preventDefault(); setDragging(true); }} onDragLeave={() => setDragging(false)}
          onDrop={event => { event.preventDefault(); setDragging(false); addFiles(Array.from(event.dataTransfer.files)); }}
          className={cn("mt-4 rounded-xl border-2 border-dashed p-5 text-center transition-colors", dragging ? "border-primary bg-primary/10" : "border-border") }>
          <Upload className="mx-auto h-6 w-6 text-primary" />
          <p className="mt-2 text-sm">拖入截图，或 <button type="button" className="font-semibold text-primary hover:underline" onClick={() => inputRef.current?.click()}>选择图片</button></p>
          <p className="mt-1 text-[11px] text-muted-foreground">PNG / JPEG / WebP · 单张 ≤10 MB · 最多 20 张</p>
          <input ref={inputRef} className="hidden" type="file" multiple accept="image/png,image/jpeg,image/webp"
            onChange={event => { addFiles(Array.from(event.target.files || [])); event.currentTarget.value = ""; }} />
        </div>

        {!!files.length && <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          {files.map(item => <div key={item.id} className="flex items-center gap-2 rounded-lg border border-border p-2">
            <img src={item.url} alt="待识别截图" className="h-14 w-12 rounded object-cover" />
            <div className="min-w-0 flex-1"><p className="truncate text-xs font-medium">{item.file.name}</p>
              <p className={cn("mt-1 text-[11px]", item.status === "error" ? "text-danger" : "text-muted-foreground")}>
                {item.status === "running" ? "正在本地识别…" : item.status === "done" ? "识别完成" : item.status === "error" ? item.error : "等待识别"}
              </p></div>
            {item.status === "running" ? <Loader2 className="h-4 w-4 animate-spin" /> : item.status === "error" ?
              <button type="button" title="重试" onClick={() => retry(item)}><RotateCcw className="h-4 w-4" /></button> : null}
            <button type="button" title="移除" disabled={item.status === "running"} onClick={() => removeFile(item.id)}><Trash2 className="h-4 w-4" /></button>
          </div>)}
        </div>}

        {!!drafts.length && <div className="mt-5 space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="text-sm font-bold">校对草稿</h3>
            <button type="button" onClick={mergeSameCode} className="rounded border border-border px-3 py-1.5 text-xs hover:bg-muted">合并已选同代码草稿</button></div>
          {drafts.map(draft => {
            const hasError = draft.warnings.some(w => w.severity === "error");
            return <div key={draft.draft_id} className={cn("rounded-xl border p-3", draft.duplicate ? "border-warning/60" : "border-border")}>
              <div className="flex flex-wrap items-center gap-2">
                <input type="checkbox" checked={draft.selected} onChange={event => patchDraft(draft.draft_id, { selected: event.target.checked })} aria-label="选择草稿" />
                <input list={`security-${draft.draft_id}`} value={draft.code} onChange={event => { const code = event.target.value.replace(/\D/g, "").slice(0, 6); const candidate = draft.security_candidates.find(item => item.code === code); patchDraft(draft.draft_id, {code, ...(candidate ? {name: candidate.name} : {})}); }} placeholder="6 位代码" className="w-28 rounded border border-border bg-card px-2 py-1.5 text-sm" />
                {!!draft.security_candidates.length && <datalist id={`security-${draft.draft_id}`}>{draft.security_candidates.map(candidate => <option key={candidate.code} value={candidate.code}>{candidate.name}</option>)}</datalist>}
                <input value={draft.name} onChange={event => patchDraft(draft.draft_id, {name: event.target.value})} placeholder="证券简称" className="w-28 rounded border border-border bg-card px-2 py-1.5 text-sm" />
                <select value={draft.playbook} onChange={event => patchDraft(draft.draft_id, {playbook: event.target.value})} className="rounded border border-border bg-card px-2 py-1.5 text-sm">{PLAYBOOKS.map(v => <option key={v}>{v}</option>)}</select>
                <select value={draft.as_planned == null ? "" : draft.as_planned ? "yes" : "no"} onChange={event => patchDraft(draft.draft_id, {as_planned: event.target.value === "" ? null : event.target.value === "yes"})} className="rounded border border-border bg-card px-2 py-1.5 text-sm"><option value="">计划状态未知</option><option value="yes">按计划</option><option value="no">计划外</option></select>
                <select value={draft.action === "append" ? `append:${draft.trade_id}` : draft.action}
                  onChange={event => { const value = event.target.value; patchDraft(draft.draft_id, value.startsWith("append:") ? {action: "append", trade_id: value.slice(7)} : {action: value as ReviewDraft["action"], trade_id: ""}); }}
                  className="min-w-40 rounded border border-border bg-card px-2 py-1.5 text-sm">
                  <option value="">请选择入库方式</option><option value="create">新建交易</option><option value="skip">跳过</option>
                  {draft.existing_candidates.map(candidate => <option key={candidate.trade_id} value={`append:${candidate.trade_id}`}>追加：{candidate.date} · 余 {candidate.open_shares} 股</option>)}
                </select>
                <span className="text-[11px] text-muted-foreground">OCR {Math.round(draft.confidence * 100)}%</span>
                {draft.duplicate && <span className="rounded bg-warning/15 px-2 py-1 text-[11px] text-warning">疑似已导入</span>}
                {hasError && <AlertTriangle className="h-4 w-4 text-warning" />}
                <button type="button" className="ml-auto" onClick={() => patchDraft(draft.draft_id, {expanded: !draft.expanded})}>{draft.expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>
              </div>
              {draft.expanded && <div className="mt-3 space-y-2">
                <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
                  <button type="button" onClick={() => patchDraft(draft.draft_id, {fills: [...draft.fills, {side: "buy", date: "", price: 0, shares: 0}]})} className="rounded border border-success/40 px-2 py-1 text-success">+ 买入</button>
                  <button type="button" onClick={() => patchDraft(draft.draft_id, {fills: [...draft.fills, {side: "sell", date: "", price: 0, shares: 0}]})} className="rounded border border-danger/40 px-2 py-1 text-danger">+ 卖出</button>
                  {draft.reported_pnl != null && <span>截图卖出盈亏合计 {draft.reported_pnl.toLocaleString()} 元（仅供核对，入账按成交重新计算）</span>}
                </div>
                {draft.fills.map((fill, index) => <div key={index} className="flex flex-wrap items-center gap-1.5 rounded-lg bg-muted/40 p-2">
                  <select value={fill.side} onChange={event => patchFill(draft.draft_id, index, {side: event.target.value as "buy" | "sell"})} className="rounded border border-border bg-card px-2 py-1 text-xs"><option value="buy">买入</option><option value="sell">卖出</option></select>
                  <input type="date" value={fill.date} onChange={event => patchFill(draft.draft_id, index, {date: event.target.value})} className="rounded border border-border bg-card px-2 py-1 text-xs" />
                  <input type="number" step="0.001" value={fill.price || ""} onChange={event => patchFill(draft.draft_id, index, {price: Number(event.target.value)})} placeholder="价格" className="w-24 rounded border border-border bg-card px-2 py-1 text-xs" />
                  <input type="number" step="1" value={fill.shares || ""} onChange={event => patchFill(draft.draft_id, index, {shares: Math.abs(Number(event.target.value))})} placeholder="股数" className="w-24 rounded border border-border bg-card px-2 py-1 text-xs" />
                  <input type="number" min="0" step="0.01" value={fill.fee ?? ""} onChange={event => patchFill(draft.draft_id, index, {fee: event.target.value === "" ? undefined : Number(event.target.value)})} placeholder="实际费用" className="w-24 rounded border border-border bg-card px-2 py-1 text-xs" />
                  <span className="text-[10px] text-muted-foreground">置信 {Math.round((fill.confidence || 0) * 100)}%</span>
                  <button type="button" disabled={index === 0} title="上移" onClick={() => moveFill(draft.draft_id, index, -1)} className="disabled:opacity-30">↑</button>
                  <button type="button" disabled={index === draft.fills.length - 1} title="下移" onClick={() => moveFill(draft.draft_id, index, 1)} className="disabled:opacity-30">↓</button>
                  <button type="button" onClick={() => patchDraft(draft.draft_id, {fills: draft.fills.filter((_, i) => i !== index)})}><Trash2 className="h-3.5 w-3.5" /></button>
                </div>)}
                <input value={draft.note} onChange={event => patchDraft(draft.draft_id, {note: event.target.value})} placeholder="导入备注（可选）" className="w-full rounded border border-border bg-card px-2 py-1.5 text-sm" />
                {!!draft.warnings.length && <div className="space-y-1">{draft.warnings.map((warning, index) => <p key={`${warning.code}-${index}`} className={cn("text-[11px]", warning.severity === "error" ? "text-warning" : "text-muted-foreground")}>· {warning.message}</p>)}</div>}
                {draft.duplicate && <label className="flex items-center gap-2 text-xs"><input type="checkbox" checked={draft.allow_duplicate} onChange={event => patchDraft(draft.draft_id, {allow_duplicate: event.target.checked, selected: event.target.checked || draft.selected})} />我确认这不是重复记录，仍然导入</label>}
                <details><summary className="cursor-pointer text-[11px] text-muted-foreground">查看 OCR 原文</summary><pre className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap rounded bg-muted p-2 text-[10px]">{draft.raw_lines.join("\n")}</pre></details>
              </div>}
            </div>;
          })}
        </div>}

        {message && <p role="alert" className="mt-4 text-sm text-warning">{message}</p>}
        </div>
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-border bg-card/80 px-4 py-3 sm:px-6">
          <span className="mr-auto text-xs text-muted-foreground">{running ? "仍在识别图片，请稍候" : `${drafts.length} 条草稿`}</span>
          <button type="button" onClick={close} disabled={saving} className="rounded-lg border border-border px-4 py-2 text-sm">取消</button>
          <button type="button" onClick={submit} disabled={running || saving || !drafts.some(draft => draft.selected)} className="flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50">
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <CheckCircle2 className="h-4 w-4" />}校对完成，一键入库
          </button>
        </div>
      </div>
    </dialog>,
    document.body,
  );
}
