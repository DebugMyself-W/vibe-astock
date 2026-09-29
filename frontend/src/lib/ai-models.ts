import { apiUrl } from "./base";
// 接入 AI 的模型清单（移植自 SDesign-opensource / open-design，按本项目适配）。
// 两类：
//   订阅版（provider "cli-*"）= 调本机已登录的 CLI，用订阅额度、免 API key（仅本地自托管可用）。
//   API 版 = 填自己的 key，走 OpenAI 兼容 /chat/completions。
// key 一律只存本地浏览器、随请求发给你自己的后端；不上传、不进仓库。

export type ProviderId =
  | "deepseek"
  | "silicon"
  | "openai"
  | "minimax"
  | "openrouter"
  | "groq"
  | "together"
  | "mimo"
  | "openai-compatible"
  | "cli-claude"
  | "cli-qwen"
  | "cli-deepseek"
  | "cli-codex"
  | "cli-opencode"
  | "cli-cursor"
  | "cli-kimi";

export interface ModelConfig {
  id: string;        // 实际传给接口/CLI 的 model 名
  name: string;      // 下拉里显示的品牌名
  description: string;
  provider: ProviderId;
  comingSoon?: boolean; // true = 列出但暂不可选（开发中）
  autoApprove?: boolean;
  blocked?: string;
}

export const isCliProvider = (p: ProviderId): boolean => p.startsWith("cli-");

// 各 API provider 的默认接口地址（OpenAI 兼容）。选中即自动填 baseURL，用户只需填 key。
export const PROVIDER_BASE: Partial<Record<ProviderId, string>> = {
  deepseek: "https://api.deepseek.com",
  silicon: "https://api.siliconflow.cn/v1",
  openai: "https://api.openai.com/v1",
  minimax: "https://api.minimaxi.com/v1",
  openrouter: "https://openrouter.ai/api/v1",
  groq: "https://api.groq.com/openai/v1",
  together: "https://api.together.xyz/v1",
  mimo: "", // 私有网关，必须自填 baseURL
  "openai-compatible": "", // 任意兼容端点，自填
};

export const aiModels: ModelConfig[] = [
  // —— 订阅版（免 API key，调本机已登录的 CLI）——
  { id: "claude-code", name: "Claude Code", description: "用本机 Claude 订阅（已限制文件/命令工具）", provider: "cli-claude" },
  { id: "qwen-code", name: "Qwen Code", description: "通义 Qwen Code 订阅", provider: "cli-qwen",
    autoApprove: true, blocked: "自动批准（--yolo）" },
  { id: "deepseek-cli", name: "DeepSeek CLI", description: "DeepSeek 本机 CLI 订阅", provider: "cli-deepseek",
    autoApprove: true, blocked: "自动批准（exec --auto）" },
  { id: "codex", name: "Codex", description: "OpenAI Codex 订阅（需 codex login 登录）", provider: "cli-codex",
    autoApprove: true, blocked: "无沙箱限制" },
  { id: "opencode", name: "OpenCode", description: "OpenCode 订阅", provider: "cli-opencode",
    comingSoon: true, autoApprove: true, blocked: "自动批准" },
  { id: "cursor-agent", name: "Cursor Agent", description: "Cursor Agent 订阅", provider: "cli-cursor",
    comingSoon: true, autoApprove: true, blocked: "自动批准" },
  { id: "kimi", name: "Kimi", description: "Kimi 订阅", provider: "cli-kimi",
    comingSoon: true, autoApprove: true, blocked: "自动批准" },
  // —— API 版（填自己的 key）——
  { id: "deepseek-v4-flash", name: "DeepSeek V4 Flash", description: "DeepSeek 官方 · 快而省 · 思考/非思考双模", provider: "deepseek" },
  { id: "deepseek-v4-pro", name: "DeepSeek V4 Pro", description: "DeepSeek 官方 · 旗舰 · 最强推理", provider: "deepseek" },
  { id: "deepseek-ai/DeepSeek-V3", name: "SiliconFlow · DeepSeek V3", description: "硅基流动", provider: "silicon" },
  { id: "gpt-4o", name: "OpenAI GPT-4o", description: "OpenAI", provider: "openai" },
  { id: "MiniMax-M2", name: "MiniMax M2", description: "MiniMax 海螺", provider: "minimax" },
  { id: "doubao-pro", name: "豆包 Pro", description: "火山方舟 · 填推理接入点 ID(ep-…)", provider: "openai-compatible" },
  { id: "openai/gpt-4o", name: "OpenRouter · GPT-4o", description: "OpenRouter 聚合（可改任意模型 id）", provider: "openrouter" },
  { id: "llama-3.3-70b-versatile", name: "Groq · Llama 3.3 70B", description: "Groq 超快推理", provider: "groq" },
  { id: "meta-llama/Llama-3.3-70B-Instruct-Turbo", name: "Together · Llama 3.3 70B", description: "Together AI", provider: "together" },
  { id: "mimo-v2.5-pro", name: "MiMo V2.5 Pro", description: "小米 MiMo（需自有网关）", provider: "mimo" },
  { id: "custom", name: "其它 OpenAI 兼容", description: "任意兼容端点，自填 baseURL/model", provider: "openai-compatible" },
];

/**
 * 该 provider 被禁用的原因；没禁用就返回 null。
 *
 * 按 **provider** 查而不是按 model id —— 存进 localStorage 的是 provider，
 * 而且真闸也是按 provider 拦的（服务端 `_BLOCKED_CLI_KINDS`），口径要一致。
 */
export const blockedReason = (p: ProviderId): string | null =>
  aiModels.find((m) => m.provider === p)?.blocked ?? null;

export const subscriptionModels = aiModels.filter((m) => isCliProvider(m.provider));
export const apiModels = aiModels.filter((m) => !isCliProvider(m.provider));

/** provider "cli-qwen" → 服务端说的 kind "qwen"。 */
export const cliKindOf = (p: ProviderId): string => (isCliProvider(p) ? p.slice(4) : "");

export interface CliStatus {
  kind: string;
  allowed: boolean;    // 服务端放行了吗（默认只有 claude；`VIBE_ALLOW_UNSAFE_CLI` 可放开）
  installed: boolean;  // 本机装了这个命令吗
  reason: string | null;
}

export interface CliAvailability {
  clis: CliStatus[];
  optInEnv: string;
  optedIn: string[];
}

export async function fetchCliAvailability(
  headers: Record<string, string> = {},
): Promise<CliAvailability | null> {
  try {
    const r = await fetch(apiUrl("/api/cli/available"), { headers });
    if (!r.ok) return null;
    return (await r.json()) as CliAvailability;
  } catch {
    return null;
  }
}

export type CliAvailState = "idle" | "loading" | "ready" | "failed";

let _cliAvail: CliAvailability | null = null;
let _cliAvailState: CliAvailState = "idle";

export const cliAvailability = (): CliAvailability | null => _cliAvail;
export const cliAvailState = (): CliAvailState => _cliAvailState;

let _cliAvailSeq = 0;

export async function primeCliAvailability(
  headers: Record<string, string> = {},
): Promise<CliAvailability | null> {
  const seq = ++_cliAvailSeq;
  _cliAvailState = "loading";
  const d = await fetchCliAvailability(headers);
  if (seq !== _cliAvailSeq) return _cliAvail;   // 有更新的请求在飞，别用旧答案覆盖
  _cliAvail = d;
  _cliAvailState = d ? "ready" : "failed";
  return _cliAvail;
}

export function serverAllowsCli(p: ProviderId): boolean | undefined {
  if (!isCliProvider(p)) return true;   // API 接入不受这道闸管
  if (_cliAvailState !== "ready" || !_cliAvail) return undefined;
  const st = _cliAvail.clis.find((c) => c.kind === cliKindOf(p));
  return st ? st.allowed && st.installed : false;   // 服务端不认识 = 不能用
}
