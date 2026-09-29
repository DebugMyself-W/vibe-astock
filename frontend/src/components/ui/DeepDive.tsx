// 「AI 深入分析」共享单元：行内展开的流式分析面板（首板分析 / 每日复盘连板表共用）。
// useDeepDive 管理展开态 + 流式请求 + 本地存档 + 一键全部分析；DeepDivePanel 渲染表格内的展开行。
// 存档只存本地 localStorage（与研究记录同体系，不上传、不进仓库），按「日期|页面|代码」为键，
// 自动清理只保留最近 5 个交易日 —— 分析过的股票刷新页面后按钮变「展开」，不再重复花模型调用。
import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Loader2, RefreshCw, Sparkles, Square } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { SaveNoteButton } from "@/components/ui/SaveNoteButton";
import { hasLlm, chatStream } from "@/lib/llm";
import { loadAgentConnection } from "@/lib/agent-api";

const TOOL_LABEL: Record<string, string> = {
  query_quote: "查行情",
  query_valuation: "查估值",
  query_reports: "查研报",
  query_news: "查新闻",
  query_global_stock: "查外盘",
};

// ---------- 本地存档 ----------
const STORE_KEY = "vr-deepdive";
const KEEP_DAYS = 5; // 只保留最近 5 个不同交易日的分析

interface DiveRecord {
  text: string;
  name?: string;
  tools: string[];
  ts: number;
}

type Store = Record<string, DiveRecord>; // key = `${date}|${ns}|${code}`

function loadStore(): Store {
  try {
    const v = JSON.parse(localStorage.getItem(STORE_KEY) || "{}");
    if (!v || typeof v !== "object" || Array.isArray(v)) return {};
    return Object.fromEntries(Object.entries(v).filter(([, record]) => {
      const r = record as DiveRecord | null;
      return r && typeof r.text === "string" && r.text.trim() && Array.isArray(r.tools);
    })) as Store;
  } catch {
    return {};
  }
}

function persistStore(store: Store) {
  // 清理：按 key 前缀日期，只保留最近 KEEP_DAYS 个交易日
  const dates = [...new Set(Object.keys(store).map((k) => k.split("|")[0]))].sort().reverse();
  const keep = new Set(dates.slice(0, KEEP_DAYS));
  const next: Store = {};
  for (const [k, v] of Object.entries(store)) {
    if (keep.has(k.split("|")[0])) next[k] = v;
  }
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify(next));
    return true;
  } catch {
    return false;
  }
}

const normDate = (d: string) => d.split("-").join(""); // '2026-07-15' / '20260715' → '20260715'

// ---------- hook ----------
export interface DiveItem {
  key: string;      // 一般用股票代码
  name?: string;
  prompt: string;
  context: string;
  /** 在模型调用前补充客观数据；用于 CLI 等不能自主 function-call 的接入方式。 */
  prepareContext?: () => Promise<string>;
}

export interface DeepDiveState {
  date: string;
  names: Record<string, string>;
  open: string | null;
  analysis: Record<string, string>;
  tools: Record<string, string[]>;
  running: string | null;
  runningKeys: string[];
  queuedKeys: string[];
  completed: Record<string, boolean>;
  errors: Record<string, string>;
  busy: boolean;
  storageError: string | null;
  batchSummary: string | null;
  aiErr: string | null;
  needConfig: boolean;
  batch: { done: number; total: number; active: string[]; failed: number } | null;
  toggle: (item: DiveItem) => void;
  rerun: (item: DiveItem) => void;
  runAll: (items: DiveItem[], concurrency?: number) => void;
  stopAll: () => void;
}

export function useDeepDive(ns: string, date: string): DeepDiveState {
  const [recordDate, setRecordDate] = useState("");
  const [names, setNames] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<Record<string, string>>({});
  const [tools, setTools] = useState<Record<string, string[]>>({});
  const [runningKeys, setRunningKeys] = useState<string[]>([]);
  const [queuedKeys, setQueuedKeys] = useState<string[]>([]);
  const [completed, setCompleted] = useState<Record<string, boolean>>({});
  const completedRef = useRef<Record<string, boolean>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [storageError, setStorageError] = useState<string | null>(null);
  const unsavedRef = useRef(new Set<string>());
  const [batchSummary, setBatchSummary] = useState<string | null>(null);
  const [needConfig, setNeedConfig] = useState(false);
  const [batch, setBatch] = useState<DeepDiveState["batch"]>(null);
  const controllers = useRef(new Map<string, AbortController>());
  const generationRef = useRef(0);
  const batchRef = useRef(false);
  const day = normDate(date);

  // 日期就绪/变化时：载入该日该页面的存档
  useEffect(() => {
    const store = loadStore();
    const prefix = `${day}|${ns}|`;
    const a: Record<string, string> = {};
    const t: Record<string, string[]> = {};
    const n: Record<string, string> = {};
    for (const [k, v] of Object.entries(store)) {
      if (k.startsWith(prefix)) {
        const code = k.slice(prefix.length);
        a[code] = v.text;
        t[code] = v.tools;
        if (typeof v.name === "string") n[code] = v.name;
      }
    }
    setAnalysis(a);
    setRecordDate(day);
    setNames(n);
    setTools(t);
    completedRef.current = Object.fromEntries(Object.keys(a).map((key) => [key, true]));
    setCompleted(completedRef.current);
    setOpen(null); setErrors({}); setRunningKeys([]); setQueuedKeys([]);
    setBatch(null); setBatchSummary(null); setNeedConfig(false); setStorageError(null);
    unsavedRef.current.clear();
    batchRef.current = false;
    return () => {
      generationRef.current += 1;
      for (const ac of controllers.current.values()) ac.abort();
      controllers.current.clear();
      batchRef.current = false;
    };
  }, [ns, day]);

  const saveRecord = (code: string, text: string, toolList: string[], name?: string) => {
    if (!day || !text.trim()) return;
    const store = loadStore();
    store[`${day}|${ns}|${code}`] = { text, tools: toolList, ts: Date.now(), name };
    if (name) setNames((current) => ({ ...current, [code]: name }));
    if (persistStore(store)) unsavedRef.current.delete(code);
    else unsavedRef.current.add(code);
    setStorageError(unsavedRef.current.size ? "浏览器存储不可写，本次结果仅在当前页面保留" : null);
  };

  const markComplete = (key: string, value: boolean) => {
    completedRef.current = { ...completedRef.current, [key]: value };
    setCompleted(completedRef.current);
  };

  /** 跑一只。expand=true 时展开面板（单只点击）；批量模式传 false 只后台跑。成功返回 true。 */
  const start = async (item: DiveItem, expand: boolean, generation = generationRef.current): Promise<boolean> => {
    if (generation !== generationRef.current || controllers.current.has(item.key)) return false;
    setErrors((m) => ({ ...m, [item.key]: "" }));
    setNeedConfig(false);
    if (expand) setOpen(item.key);
    if (!hasLlm()) {
      setNeedConfig(true);
      if (!expand) setOpen(item.key); // 批量时也把配置提示亮出来
      return false;
    }
    const ac = new AbortController();
    controllers.current.set(item.key, ac);
    const current = () => !ac.signal.aborted && generation === generationRef.current;
    setRunningKeys([...controllers.current.keys()]);
    setQueuedKeys((keys) => keys.filter((key) => key !== item.key));
    markComplete(item.key, false);
    setAnalysis((m) => ({ ...m, [item.key]: "" }));
    setTools((m) => ({ ...m, [item.key]: [] }));
    let text = "";
    let toolList: string[] = [];
    try {
      const prepared = item.prepareContext ? await item.prepareContext() : "";
      if (!current()) return false;
      const context = prepared.trim() ? `${item.context}\n\n调用前预取的客观数据：\n${prepared}` : item.context;
      await chatStream([{ role: "user", content: item.prompt }], context, {
        onDelta: (t) => {
          if (!current()) return;
          text += t;
          setAnalysis((m) => ({ ...m, [item.key]: (m[item.key] || "") + t }));
        },
        onTool: (tool) => {
          if (!current()) return;
          const label = TOOL_LABEL[tool] || tool;
          if (!toolList.includes(label)) toolList = [...toolList, label];
          setTools((m) => ({ ...m, [item.key]: toolList }));
        },
      }, ac.signal);
      if (!current()) return false;
      if (!text.trim()) throw new Error("模型未返回分析正文，请重试");
      saveRecord(item.key, text, toolList, item.name);
      markComplete(item.key, true);
      return true;
    } catch (e) {
      if (current()) setErrors((m) => ({ ...m, [item.key]: e instanceof Error ? e.message : "分析失败" }));
      return false;
    } finally {
      if (generation === generationRef.current && controllers.current.get(item.key) === ac) {
        controllers.current.delete(item.key);
        setRunningKeys([...controllers.current.keys()]);
      }
    }
  };

  const toggle = (item: DiveItem) => {
    if (open === item.key) {
      setOpen(null);
      return;
    }
    setNeedConfig(false);
    if (completedRef.current[item.key] || controllers.current.has(item.key) || batchRef.current) {
      setOpen(item.key);
      return;
    }
    if (controllers.current.size) return;
    setBatchSummary(null);
    void start(item, true);
  };

  /** Bounded API workers; CLI requests stay serial. Only completed results are skipped. */
  const runAll = async (items: DiveItem[], concurrency = 1) => {
    if (batchRef.current || controllers.current.size || !day) return;
    const todo = [...new Map(items.map((it) => [it.key, it])).values()].filter((it) => !completedRef.current[it.key]);
    if (todo.length === 0) return;
    if (!hasLlm()) {
      setNeedConfig(true);
      if (todo[0]) setOpen(todo[0].key);
      return;
    }
    const provider = loadAgentConnection()?.provider;
    const serial = !!provider && ["codex-private", "claude", "codebuddy"].includes(provider);
    const limit = serial ? 1 : Math.max(1, Math.min(3, Math.floor(concurrency) || 1));
    const generation = generationRef.current;
    batchRef.current = true;
    setBatchSummary(null);
    setQueuedKeys(todo.map((it) => it.key));
    let cursor = 0, done = 0, failed = 0;
    const publish = () => setBatch({ done, total: todo.length, active: [...controllers.current.keys()], failed });
    const worker = async () => {
      while (generation === generationRef.current && cursor < todo.length) {
        const item = todo[cursor++];
        const task = start(item, false, generation);
        publish();
        const ok = await task;
        if (generation !== generationRef.current) return;
        done += 1;
        if (!ok) failed += 1;
        publish();
      }
    };
    await Promise.all(Array.from({ length: Math.min(limit, todo.length) }, worker));
    if (generation !== generationRef.current) return;
    batchRef.current = false;
    setBatch(null); setQueuedKeys([]);
    setBatchSummary(`分析完成：成功 ${done - failed} 只${failed ? `，失败 ${failed} 只` : ""}`);
  };

  const stopAll = () => {
    generationRef.current += 1;
    const active = [...controllers.current.keys()];
    for (const ac of controllers.current.values()) ac.abort();
    controllers.current.clear();
    batchRef.current = false;
    setErrors((m) => ({ ...m, ...Object.fromEntries(active.map((key) => [key, "分析已停止，可重试"])) }));
    setBatch(null); setRunningKeys([]); setQueuedKeys([]);
    setBatchSummary("已停止，已完成的结果已保留");
  };

  return {
    date: recordDate, names,
    open, analysis, tools, running: runningKeys[0] || null, runningKeys, queuedKeys, completed, errors,
    aiErr: open ? errors[open] || null : null, needConfig, batch, storageError, batchSummary,
    busy: batch !== null || runningKeys.length > 0,
    toggle,
    rerun: (item) => { if (!batchRef.current && !controllers.current.size) { setBatchSummary(null); void start(item, true); } },
    runAll: (items, concurrency) => void runAll(items, concurrency),
    stopAll,
  };
}

// ---------- 表格内展开行 ----------
interface PanelProps {
  dd: DeepDiveState;
  stockKey: string;
  colSpan: number;
  noteTitle: string; // 存研究记录的标题
  onRerun: () => void;
}

export function DeepDivePanel({ dd, stockKey, colSpan, noteTitle, onRerun }: PanelProps) {
  const text = dd.analysis[stockKey] || "";
  const isRunning = dd.runningKeys.includes(stockKey);
  return (
    <tr className="border-b border-border/30 bg-primary/[0.03]">
      <td colSpan={colSpan} className="px-3 py-3">
        {dd.needConfig ? (
          <p className="text-sm text-muted-foreground">
            还没接入 AI —— 先去 <Link to="/settings" className="text-primary underline">接入 AI</Link> 页配置一次（订阅 CLI 或 API key），回来即可一键深入分析。
          </p>
        ) : (
          <div>
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
              <Sparkles className="h-3.5 w-3.5 text-primary" />
              AI 深入分析（由你配置的模型给出，非本产品观点，不构成投资建议）
              {(dd.tools[stockKey] || []).map((t) => (
                <span key={t} className="rounded-full border border-secondary/40 bg-secondary/10 px-2 py-0.5">{t}</span>
              ))}
              {isRunning && <Loader2 className="h-3 w-3 animate-spin" />}
              {!isRunning && (text || dd.errors[stockKey]) && (
                <>
                  <button
                    onClick={onRerun}
                    disabled={dd.busy}
                    className="inline-flex items-center gap-1 rounded border border-border/60 px-1.5 py-0.5 hover:text-foreground disabled:opacity-40"
                  >
                    <RefreshCw className="h-3 w-3" /> 重新分析
                  </button>
                  {dd.completed[stockKey] && <SaveNoteButton kind="问AI" title={noteTitle} content={text} />}
                </>
              )}
            </div>
            {dd.aiErr && dd.open === stockKey && <p className="mb-1 text-xs text-danger">{dd.aiErr}</p>}
            <div className="prose prose-sm prose-invert max-w-none text-foreground">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {text || (isRunning ? "正在调工具、组织分析…" : dd.queuedKeys.includes(stockKey) ? "等待分析" : "")}
              </ReactMarkdown>
            </div>
          </div>
        )}
      </td>
    </tr>
  );
}

// ---------- 一键全部分析按钮（带进度/停止） ----------
interface RunAllProps {
  dd: DeepDiveState;
  items: DiveItem[];
  nameOf?: (key: string) => string; // 进度里显示名称（默认显示 key）
  selectedItems?: DiveItem[];
  /** 有“分析所选”时可隐藏全量入口，避免在同一处给出重复动作。 */
  showAll?: boolean;
  concurrency?: number;
}

export function RunAllButton({ dd, items, nameOf, selectedItems, showAll = true, concurrency = 1 }: RunAllProps) {
  const remaining = items.filter((it) => !dd.completed[it.key]).length;
  const selectedRemaining = selectedItems?.filter((it) => !dd.completed[it.key]).length || 0;
  const buttonClass = "inline-flex min-h-8 items-center gap-1 rounded border border-primary/50 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/20 disabled:cursor-not-allowed disabled:opacity-40";
  return <span className="inline-flex max-w-full flex-wrap items-center gap-2 text-xs font-normal text-muted-foreground">
    {dd.busy ? <>
        <Loader2 className="h-3.5 w-3.5 animate-spin text-primary" />
        <span role="status">{dd.batch ? `批量分析中 ${dd.batch.done}/${dd.batch.total}${dd.batch.failed ? `，失败 ${dd.batch.failed} 只` : ""}` : "分析中"}</span>
        <span className="break-words">{dd.runningKeys.map((key) => nameOf ? nameOf(key) : key).join("、")}</span>
        <button type="button" onClick={dd.stopAll} title="停止分析" aria-label="停止分析"
          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded border border-border hover:text-foreground"><Square className="h-3.5 w-3.5" /></button>
    </> : <>
      {selectedItems && <button type="button" onClick={() => dd.runAll(selectedItems, concurrency)} disabled={!selectedRemaining} className={buttonClass}>
        <Sparkles className="h-3 w-3" />分析所选（{selectedRemaining} 只）
      </button>}
      {showAll && <button type="button" onClick={() => dd.runAll(items, concurrency)} disabled={!remaining} className={buttonClass}>
        <Sparkles className="h-3 w-3" />{remaining === 0 ? "已全部分析" : `一键全部分析（剩 ${remaining} 只）`}
      </button>}
      {dd.batchSummary && <span role="status">{dd.batchSummary}</span>}
    </>}
    {dd.storageError && <span role="alert" className="w-full text-warning">{dd.storageError}</span>}
  </span>;
}
