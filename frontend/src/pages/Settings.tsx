import { useEffect, useState } from 'react';
import { Clock3, KeyRound, Loader2, RefreshCw, Router, ShieldCheck } from 'lucide-react';
import { PageHeader } from '@/components/ui/PageHeader';
import { AgentAccess } from '@/components/AgentAccess';
import { AgentToggle } from '@/components/workspace/AgentToggle';
import { useWorkspace } from '@/lib/workspace/state';
import { api, loadAccessKey, saveAccessKey, type AppSettings, type MarketNetworkDiagnostic } from '@/lib/api';
import { loadAgentConnection } from '@/lib/agent-api';
import { isSubscription } from '@/lib/ai-access';
import { toast } from 'sonner';
const normalizeSettings = (value: AppSettings): AppSettings => ({
  ...value,
  market_network: value.market_network || { mode: 'auto', proxy_url: '' },
  market_network_status: value.market_network_status || {
    configured_mode: 'auto', effective_mode: 'auto', override_source: null,
    proxy_available: false, proxy_source: null, proxy_label: '未检测到',
    last_route: null, last_route_at: null, ai_isolated: true,
  },
});
export function Settings() {
  const workspace = useWorkspace();
  const [accessKey, setAccessKey] = useState(loadAccessKey());
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [scheduleEnabled, setScheduleEnabled] = useState(false);
  const [scheduleTime, setScheduleTime] = useState('15:30');
  const [scheduleForce, setScheduleForce] = useState(false);
  const [scheduleBusy, setScheduleBusy] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [networkMode, setNetworkMode] = useState<'auto' | 'direct' | 'proxy'>('auto');
  const [proxyUrl, setProxyUrl] = useState('');
  const [networkBusy, setNetworkBusy] = useState(false);
  const [diagnostic, setDiagnostic] = useState<MarketNetworkDiagnostic | null>(null);
  const [iwencaiKey, setIwencaiKey] = useState('');
  const [iwencaiBusy, setIwencaiBusy] = useState(false);
  const loadSettings = () => api.settings().then(value => {
    const normalized = normalizeSettings(value);
    setSettings(normalized); setScheduleEnabled(normalized.review_schedule.enabled);
    setScheduleTime(normalized.review_schedule.time); setScheduleForce(normalized.review_schedule.force);
    setNetworkMode(normalized.market_network.mode); setProxyUrl(normalized.market_network.proxy_url);
  }).catch(error => toast.error(error instanceof Error ? error.message : '定时复盘设置读取失败'));
  useEffect(() => { void loadSettings(); }, []);
  const saveSchedule = async () => {
    if (!settings) return;
    const connection = loadAgentConnection();
    if (scheduleEnabled && (!connection || !isSubscription(connection.provider))) {
      toast.error('定时复盘仅支持已测试的 Codex、Claude 或 WorkBuddy 订阅来源；API 密钥不会保存到服务器');
      return;
    }
    setScheduleBusy(true);
    try {
      const source = scheduleEnabled && connection
        ? { provider: connection.provider as 'codex-private' | 'claude' | 'codebuddy', model: connection.model }
        : settings.review_schedule.source;
      const value = await api.saveSettings({
        iwencai_intraday_refresh_minutes: settings.iwencai_intraday_refresh_minutes,
        review_schedule: { ...settings.review_schedule, enabled: scheduleEnabled, time: scheduleTime, force: scheduleForce, source },
      });
      setSettings(normalizeSettings(value));
      toast.success(scheduleEnabled ? '定时复盘已启用' : '定时复盘已关闭');
    } catch (error) { toast.error(error instanceof Error ? error.message : '定时复盘设置保存失败'); }
    finally { setScheduleBusy(false); }
  };
  const retrySchedule = async () => {
    setRetrying(true);
    try { const result = await api.retryScheduledReview(); toast.success(`${result.date} 已开始重试`); await loadSettings(); }
    catch (error) { toast.error(error instanceof Error ? error.message : '重试失败'); }
    finally { setRetrying(false); }
  };
  const saveNetwork = async () => {
    if (!settings) return;
    setNetworkBusy(true);
    try {
      const value = await api.saveSettings({
        iwencai_intraday_refresh_minutes: settings.iwencai_intraday_refresh_minutes,
        market_network: { mode: networkMode, proxy_url: proxyUrl.trim() },
      });
      const normalized = normalizeSettings(value);
      setSettings(normalized); setNetworkMode(normalized.market_network.mode); setProxyUrl(normalized.market_network.proxy_url);
      setDiagnostic(null);
      toast.success('市场数据网络设置已保存，立即对后续请求生效');
    } catch (error) { toast.error(error instanceof Error ? error.message : '市场数据网络设置保存失败'); }
    finally { setNetworkBusy(false); }
  };
  const diagnoseNetwork = async () => {
    setNetworkBusy(true);
    try { setDiagnostic(await api.diagnoseMarketNetwork()); }
    catch (error) { toast.error(error instanceof Error ? error.message : '网络诊断失败'); }
    finally { setNetworkBusy(false); }
  };
  const saveIwencaiKey = async () => {
    if (!iwencaiKey.trim()) { toast.error('请输入问财 API Key'); return; }
    setIwencaiBusy(true);
    try {
      const value = normalizeSettings(await api.saveIwencaiKey(iwencaiKey.trim()));
      setSettings(value); setIwencaiKey('');
      toast.success('问财密钥已安全保存，并立即用于后续复盘与深挖');
    } catch (error) { toast.error(error instanceof Error ? error.message : '问财密钥保存失败'); }
    finally { setIwencaiBusy(false); }
  };
  const clearIwencaiKey = async () => {
    if (!window.confirm('确认停用并清除设置页保存的问财密钥？旧 .env 中的密钥也会被本设置覆盖停用。')) return;
    setIwencaiBusy(true);
    try {
      const value = normalizeSettings(await api.clearIwencaiKey());
      setSettings(value); setIwencaiKey('');
      toast.success('问财密钥已清除并停用');
    } catch (error) { toast.error(error instanceof Error ? error.message : '问财密钥清除失败'); }
    finally { setIwencaiBusy(false); }
  };
  return <div className="space-y-6">
    <PageHeader title="接入 AI" subtitle="选择一个来源，首页、复盘、深挖与页面问答共用。" />
    <section className="glass rounded-2xl p-5"><div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">AI 来源</h2><AgentToggle /></div><AgentAccess onSaved={workspace.refresh} /></section>
    <section className="glass rounded-2xl p-5">
      <div className="mb-4 flex items-start gap-3"><KeyRound className="mt-0.5 h-5 w-5 text-primary" /><div><h2 className="font-semibold">问财数据密钥</h2><p className="mt-1 text-xs text-muted-foreground">用于涨停原因、题材补充及个股深挖。密钥只保存在 WSL 当前用户的私有文件中，不会回传到浏览器、报告或日志；板块归属与热门概念仍优先复用现有公开数据算法。</p></div></div>
      {!settings ? <p className="text-sm text-muted-foreground">正在读取设置…</p> : <div className="space-y-3">
        <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs"><span className={settings.iwencai.configured ? 'text-success' : 'text-warning'}>{settings.iwencai.configured ? '已配置' : '未配置'}</span>{settings.iwencai.configured && <span className="ml-2 text-muted-foreground">来源：{settings.iwencai.source === 'settings' ? '设置页私有存储' : '.env / 进程环境'}</span>}<span className="ml-2 text-muted-foreground">状态：{settings.iwencai.circuit_state === 'ready' ? '可用' : settings.iwencai.circuit_reason || settings.iwencai.circuit_state}</span></div>
        <label className="block text-sm">新密钥<input type="password" autoComplete="new-password" value={iwencaiKey} onChange={e=>setIwencaiKey(e.target.value)} placeholder={settings.iwencai.configured ? '已保存；输入新值可替换' : '粘贴 IWENCAI_API_KEY'} aria-label="问财 API Key" className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2" /></label>
        <div className="flex flex-wrap gap-2"><button type="button" disabled={iwencaiBusy || !iwencaiKey.trim()} onClick={()=>void saveIwencaiKey()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">{iwencaiBusy && <Loader2 className="h-4 w-4 animate-spin" />}保存 / 替换密钥</button>{settings.iwencai.configured && <button type="button" disabled={iwencaiBusy} onClick={()=>void clearIwencaiKey()} className="rounded-lg border border-destructive/40 px-4 py-2 text-sm text-destructive disabled:opacity-50">清除并停用</button>}</div>
      </div>}
    </section>
    <section className="glass rounded-2xl p-5">
      <div className="mb-4 flex items-start gap-3"><Router className="mt-0.5 h-5 w-5 text-primary" /><div><h2 className="font-semibold">市场数据网络</h2><p className="mt-1 text-xs text-muted-foreground">统一控制腾讯、东财、新浪、巨潮、问财与研究数据的 HTTP 出口。只作用于市场数据，不改系统环境，也不影响 Codex、Claude 或其他 AI 连接。</p></div></div>
      {!settings ? <p className="text-sm text-muted-foreground">正在读取设置…</p> : <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          {([
            ['auto', '自动（推荐）', '先从 WSL 直连，连接异常或受限时再走 Clash。'],
            ['direct', '固定直连', '所有市场 HTTP 请求绕过代理。'],
            ['proxy', '固定代理', '所有市场 HTTP 请求只走下方代理。'],
          ] as const).map(([mode, title, description]) => <label key={mode} className={`cursor-pointer rounded-xl border p-3 text-sm ${networkMode === mode ? 'border-primary bg-primary/5' : 'border-border'}`}><span className="flex items-center gap-2 font-medium"><input type="radio" name="market-network-mode" checked={networkMode === mode} onChange={()=>setNetworkMode(mode)} />{title}</span><span className="mt-1 block pl-5 text-xs leading-5 text-muted-foreground">{description}</span></label>)}
        </div>
        <label className="block text-sm">Clash / HTTP 代理地址（可选）<input type="url" value={proxyUrl} onChange={e=>setProxyUrl(e.target.value)} placeholder="例如 http://172.20.32.1:7890" className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2" /><span className="mt-1 block text-xs leading-5 text-muted-foreground">留空时读取 WSL 进程已有的 HTTP(S)_PROXY。WSL 非镜像网络通常不能用 Windows 的 127.0.0.1，请填 Windows 主机网关地址，并在 Clash 开启 Allow LAN。为避免泄密，这里不接受含账号密码的代理 URL。</span></label>
        <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-6">
          <div className="flex flex-wrap gap-x-4"><span>当前生效：<b>{settings.market_network_status.effective_mode === 'auto' ? '自动' : settings.market_network_status.effective_mode === 'direct' ? '直连' : '代理'}</b></span><span>检测代理：<b>{settings.market_network_status.proxy_label}</b></span>{settings.market_network_status.last_route && <span>最近成功：<b>{settings.market_network_status.last_route === 'direct' ? '直连' : '代理'}</b></span>}</div>
          {settings.market_network_status.override_source && <p className="text-warning">环境变量 {settings.market_network_status.override_source} 正在覆盖页面选择；移除该变量并重启服务后，页面设置才会成为最终模式。</p>}
          <p className="flex items-center gap-1 text-success"><ShieldCheck className="h-3.5 w-3.5" />AI 网络隔离已启用：本设置不会写入 HTTP_PROXY、HTTPS_PROXY 或 NO_PROXY。</p>
        </div>
        <div className="flex flex-wrap gap-2"><button type="button" disabled={networkBusy} onClick={()=>void saveNetwork()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">{networkBusy && <Loader2 className="h-4 w-4 animate-spin" />}保存网络设置</button><button type="button" disabled={networkBusy} onClick={()=>void diagnoseNetwork()} className="inline-flex items-center gap-2 rounded-lg border border-border px-4 py-2 text-sm disabled:opacity-50"><RefreshCw className={`h-4 w-4 ${networkBusy ? 'animate-spin' : ''}`} />一键诊断直连与 Clash</button></div>
        {diagnostic && <div className="space-y-3 rounded-xl border border-border p-3 text-xs"><div className="font-medium">诊断时间 {diagnostic.generated_at}</div>{diagnostic.routes.map(route => <div key={route.route}><div className="mb-1 font-medium">{route.route === 'direct' ? 'WSL 直连' : 'Windows Clash / HTTP 代理'}：<span className={route.ok ? 'text-success' : 'text-warning'}>{route.ok ? '全部通过' : route.skipped ? '未执行' : '存在失败'}</span></div>{route.detail && <p className="mb-1 text-muted-foreground">{route.detail}</p>}<div className="grid gap-1 sm:grid-cols-3">{route.sources.map(source => <div key={source.source} className="rounded-lg bg-muted/30 p-2"><b className={source.ok ? 'text-success' : 'text-warning'}>{source.ok ? '通过' : '失败'} · {source.label}</b><div>{source.latency_ms} ms</div><div className="break-words text-muted-foreground">{source.detail}</div></div>)}</div></div>)}<p className="text-muted-foreground">{diagnostic.tcp_note}</p></div>}
      </div>}
    </section>
    <section className="glass rounded-2xl p-5">
      <div className="mb-4 flex items-start gap-3"><Clock3 className="mt-0.5 h-5 w-5 text-primary" /><div><h2 className="font-semibold">定时复盘</h2><p className="mt-1 text-xs text-muted-foreground">到点后自动生成最近已收盘交易日的复盘。为保持新版密钥边界，仅支持已测试的订阅来源，API 密钥不会保存到后端。</p></div></div>
      {!settings ? <p className="text-sm text-muted-foreground">正在读取设置…</p> : <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={scheduleEnabled} onChange={e=>setScheduleEnabled(e.target.checked)} />启用定时复盘</label>
          <label className="text-sm">执行时间<input type="time" value={scheduleTime} onChange={e=>setScheduleTime(e.target.value)} className="mt-1 block w-full rounded-lg border border-border bg-background px-3 py-2" /></label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={scheduleForce} onChange={e=>setScheduleForce(e.target.checked)} />已有复盘也重新生成</label>
        </div>
        <p className="text-xs text-muted-foreground">当前定时来源：{settings.review_schedule.source ? `${settings.review_schedule.source.provider} / ${settings.review_schedule.source.model}` : '尚未绑定；保存时使用当前已测试订阅'}</p>
        {settings.review_schedule.last_status && <div className="rounded-lg border border-border bg-muted/20 p-3 text-xs leading-6">
          <div className="flex flex-wrap items-center gap-x-3"><b>{settings.review_schedule.last_status === 'failed' ? '上次自动复盘失败' : settings.review_schedule.last_status === 'running' ? '自动复盘进行中' : '上次自动复盘完成'}</b>{settings.review_schedule.last_date && <span>{settings.review_schedule.last_date}</span>}{settings.review_schedule.last_elapsed != null && <span>耗时 {Math.floor(settings.review_schedule.last_elapsed / 60)}分{settings.review_schedule.last_elapsed % 60}秒</span>}</div>
          {settings.review_schedule.last_error && <p className="text-warning">{settings.review_schedule.last_error}</p>}
          {settings.review_schedule.last_status === 'failed' && <button type="button" disabled={retrying} onClick={()=>void retrySchedule()} className="mt-2 inline-flex items-center gap-1 rounded border border-current/35 px-2 py-1 text-primary disabled:opacity-50">{retrying ? <Loader2 className="h-3 w-3 animate-spin" /> : <RefreshCw className="h-3 w-3" />}重试本次复盘</button>}
        </div>}
        <button type="button" disabled={scheduleBusy} onClick={()=>void saveSchedule()} className="inline-flex items-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm text-primary-foreground disabled:opacity-50">{scheduleBusy && <Loader2 className="h-4 w-4 animate-spin" />}保存定时设置</button>
      </div>}
    </section>
    <p className="text-sm text-muted-foreground">旧版分散的 AI 设置不再用于产品调用。原有配置留在本机，没有自动迁移密钥或替换已保存来源。页面问答在 Agent 关闭时仅阅读本次明确提供的材料；开启后可查询公开资料。完整复盘与个股深挖由各自的任务按钮明确发起。</p>
    <details className="glass rounded-2xl p-5"><summary className="cursor-pointer text-sm font-medium">后端访问设置</summary><div className="mt-4 space-y-3"><h3 className="flex items-center gap-2 text-sm"><KeyRound size={16} />后端访问密钥（可选）</h3><p className="text-xs text-muted-foreground">仅当后端设置了访问密钥时填写，本机默认可留空。保存在本浏览器中，清理网站数据会移除；这不是 AI 服务商密钥。</p><div className="flex gap-2"><input type="password" value={accessKey} onChange={e=>setAccessKey(e.target.value)} className="min-w-0 flex-1 rounded-lg border border-border bg-card px-3 py-2" aria-label="后端访问密钥" /><button className="rounded-lg bg-primary px-4 py-2 text-primary-foreground" onClick={()=>{try { saveAccessKey(accessKey.trim()); workspace.refresh(); toast.success('已保存后端访问设置'); } catch(e) { toast.error(e instanceof Error ? e.message : '保存失败'); }}}>保存</button></div></div></details>
  </div>;
}
