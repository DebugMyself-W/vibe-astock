// NODE_PATH=<playwright installation> node tests/firstboard_browser.cjs [base URL] [output dir]
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const ts = require('../frontend/node_modules/typescript');
const filename = path.resolve(__dirname, '../frontend/src/lib/firstboard-sort.ts');
const compiled = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const mod = new Module(filename, module);
mod._compile(compiled.outputText, filename);
const { sortFirstBoard, toggleSort, DEFAULT_SORT } = mod.exports;
const stocks = [
  { code: '600001', name: '测试甲', seal_time: '09:30', break_count: 10, price: 2, pct: 9.9, industry: '电网设备' },
  { code: '600002', name: '测试乙', seal_time: '09:30', break_count: 2, price: 10, pct: 10.1, industry: '电网设备' },
  { code: '600003', name: '测试丙', seal_time: '10:01', break_count: 0, price: 3, pct: 20, industry: '中药' },
  { code: '600004', name: '测试丁', seal_time: '09:30', break_count: 2, price: 5, pct: 9.8, industry: '电网设备' },
].map(s => ({ ...s, amount: 1e8, float_cap: 10e8, reason: '回归测试题材，不代表实际行情' }));
const codes = rows => rows.map(row => row.code.slice(-1)).join('');
const rules = (...pairs) => pairs.map(([key, direction]) => ({ key, direction }));
assert.equal(codes(sortFirstBoard(stocks, DEFAULT_SORT)), '1243');
assert.equal(codes(sortFirstBoard(stocks, rules(['break_count', 'asc']))), '3241');
assert.equal(codes(sortFirstBoard(stocks, rules(['price', 'desc']))), '2431');
assert.equal(codes(sortFirstBoard(stocks, rules(['pct', 'asc']))), '4123');
assert.equal(codes(sortFirstBoard(stocks, rules(['seal_time', 'asc'], ['break_count', 'asc'], ['price', 'desc']))), '2413');
assert.equal(codes(sortFirstBoard(stocks, rules(['industry', 'desc'], ['price', 'asc']))), '3142');
assert.equal(codes(stocks), '1234', 'input must not mutate');
for (const direction of ['asc', 'desc']) {
  const missing = { ...stocks[0], code: '600005', price: null, seal_time: '', industry: '' };
  for (const key of ['price', 'seal_time', 'industry']) {
    assert.equal(sortFirstBoard([missing, ...stocks], rules([key, direction])).at(-1).code, missing.code);
  }
}
let cycle = toggleSort(DEFAULT_SORT, 'price');
assert.deepEqual(cycle, [...DEFAULT_SORT, { key: 'price', direction: 'asc' }]);
cycle = toggleSort(cycle, 'price');
assert.equal(cycle[1].direction, 'desc');
assert.deepEqual(toggleSort(cycle, 'price'), DEFAULT_SORT);
console.log('Sorting unit checks passed');
const exportFile = path.resolve(__dirname, '../frontend/src/lib/firstboard-export.ts');
assert(fs.existsSync(exportFile), 'date-bound Markdown export builder must exist');
const exportMod = new Module(exportFile, module);
exportMod._compile(ts.transpileModule(fs.readFileSync(exportFile, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS } }).outputText, exportFile);
const { buildFirstBoardExport } = exportMod.exports;
const exportState = { date: '20260826', names: { '600009': '已存档名称' },
  analysis: { '600001': '# 完整正文\n\n**保留格式**', '600002': '中断输出', '600003': '   ', '600009': '不在当前榜单的已完成正文' },
  completed: { '600001': true, '600002': false, '600003': true, '600009': true } };
const markdown = buildFirstBoardExport('2026-08-26', exportState, stocks);
assert.equal(markdown.filename, '首板分析_2026-08-26.md');
assert.equal(markdown.count, 2);
assert(markdown.content.includes('测试甲（600001）'));
assert(markdown.content.includes('已存档名称（600009）'));
assert(markdown.content.includes('# 完整正文\n\n**保留格式**'));
assert(!markdown.content.includes('中断输出'));
assert.equal(buildFirstBoardExport('20260825', exportState, stocks), null, 'stale hook state must not export under another date');
assert.equal(buildFirstBoardExport('', exportState, stocks), null);
console.log('Date-bound export unit checks passed');
if (process.argv.includes('--unit')) process.exit(0);

const { chromium } = require('playwright');
const base = process.argv[2] || 'http://localhost:8912';
const output = path.resolve(process.argv[3] || 'frontend/dist/firstboard-qa');
fs.mkdirSync(output, { recursive: true });

async function waitUntil(check, label) {
  const until = Date.now() + 10000;
  while (!(await check())) {
    if (Date.now() > until) throw new Error('Timed out: ' + label);
    await new Promise(resolve => setTimeout(resolve, 30));
  }
}

async function batchPage(browser, width = 1393, provider = 'openai') {
  const page = await browser.newPage({ viewport: { width, height: width < 500 ? 844 : 1272 } });
  const calls = [], errors = [];
  const data = { date: '20260826', total_zt: 4, first_count: 4, stocks, reason_note: null };
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', async route => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === '/api/chat') {
      const body = route.request().postDataJSON();
      calls.push({ code: body.context.match(/600\d{3}/)[0], route, body });
      return;
    }
    const body = pathname === '/api/cli/available' ? { clis: [{ kind: 'claude', allowed: true, installed: true, reason: null }] }
      : { data: pathname === '/api/market/first-board' ? data : {} };
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.addInitScript(provider => {
    localStorage.setItem('vr-llm', JSON.stringify({ provider, baseURL: 'https://example.invalid/v1', apiKey: 'test-only', model: 'test' }));
    if (!localStorage.getItem('vr-deepdive')) {
      localStorage.setItem('vr-deepdive', JSON.stringify(Object.fromEntries(['20260819', '20260820', '20260821', '20260824', '20260825'].map(day =>
        [day + '|lianban|600009', { text: '旧日测试记录', tools: [], ts: 1 }]))));
    }
  }, provider);
  await page.goto(base + '/first-board');
  const table = page.getByRole('table', { name: '首板名单', exact: true });
  await table.getByRole('link', { name: '600001', exact: true }).waitFor();
  const row = code => table.locator('tbody > tr').filter({ has: page.getByRole('link', { name: code, exact: true }) });
  const choose = async code => row(code).getByRole('checkbox').check();
  const finish = async (call, mode = 'done') => {
    const events = mode === 'empty' ? [] : [{ type: 'delta', text: '测试报告 ' + call.code }];
    if (mode === 'done' || mode === 'empty') events.push({ type: 'done', trace: [], rounds: 1 });
    if (mode === 'error') events.push({ type: 'error', message: '模拟模型失败 ' + call.code });
    await call.route.fulfill({ status: 200, contentType: 'application/x-ndjson', body: events.map(e => JSON.stringify(e)).join('\n') }).catch(() => {});
  };
  const saved = () => page.evaluate(() => JSON.parse(localStorage.getItem('vr-deepdive') || '{}'));
  const current = async () => Object.keys(await saved()).filter(key => key.startsWith('20260826|firstboard|'));
  return { page, calls, data, errors, row, choose, finish, saved, current };
}

async function dateExportPage(browser, width) {
  const page = await browser.newPage({ viewport: { width, height: width === 926 ? 700 : 844 }, acceptDownloads: true });
  const errors = [], requests = [], chats = [], delayed = [];
  page.on('pageerror', err => errors.push(err.message));
  await page.addInitScript(() => {
    localStorage.setItem('vr-llm', JSON.stringify({ provider: 'openai', baseURL: 'https://example.invalid/v1', apiKey: 'test-only', model: 'test' }));
    localStorage.setItem('vr-deepdive', JSON.stringify({
      '20260826|firstboard|600001': { text: '26日完整正文 **格式**', tools: [], ts: 1 },
      '20260825|firstboard|600001': { text: '25日完整正文', tools: [], ts: 1 },
      '20260825|firstboard|600009': { text: '25日旧名单正文', name: '历史九', tools: [], ts: 1 },
      '20260824|firstboard|600009': { text: '24日离线正文', name: '离线九', tools: [], ts: 1 },
      '20260825|lianban|600005': { text: '另一页面记录', tools: [], ts: 1 },
    }));
  });
  const payload = (day, rows = stocks, note = null) => ({ data: {
    date: day, stocks: rows, total_zt: note ? null : rows.length, first_count: note ? null : rows.length, data_note: note, reason_note: null,
  } });
  await page.route('**/api/**', async route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/api/chat') { chats.push({ route, body: route.request().postDataJSON() }); return; }
    let body = { data: {} };
    if (url.pathname === '/api/market/first-board') {
      const day = url.searchParams.get('date');
      requests.push(day);
      if (day === '2026-08-23') { delayed.push(route); return; }
      if (day === '2026-08-22') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: '模拟历史请求失败' }) });
      body = day === '2026-08-24' ? payload('20260824', [], '所选日期涨停池暂不可用')
        : day === '2026-08-25' ? payload('20260825', stocks.slice(0, 2)) : payload('20260826');
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  });
  await page.goto(base + '/first-board');
  const dateInput = page.getByLabel('交易日', { exact: true });
  await dateInput.waitFor();
  await waitUntil(async () => await dateInput.inputValue() === '2026-08-26', 'automatic resolved date');
  assert.equal(requests[0], null, 'default must not send a date');
  const exportButton = page.getByRole('button', { name: '导出当日分析', exact: true });
  const download = async (day) => {
    const pending = page.waitForEvent('download');
    await exportButton.click();
    const file = await pending;
    assert.equal(file.suggestedFilename(), `首板分析_${day}.md`);
    const dest = path.join(output, `export-${width}-${day}.md`);
    await file.saveAs(dest);
    return fs.readFileSync(dest, 'utf8');
  };
  let content = await download('2026-08-26');
  assert(content.includes('26日完整正文 **格式**') && !content.includes('25日'));
  await dateInput.fill('2026-08-25');
  await page.getByRole('button', { name: '一键全部分析（剩 1 只）', exact: true }).waitFor();
  await page.getByRole('checkbox', { name: '选择 测试乙 600002', exact: true }).check();
  content = await download('2026-08-25');
  assert(content.includes('测试甲（600001）') && content.includes('历史九（600009）'));
  assert(!content.includes('26日') && !content.includes('另一页面') && !content.includes('测试乙'));
  await page.getByRole('button', { name: '分析所选（1 只）', exact: true }).click();
  await waitUntil(() => chats.length === 1, 'historical analysis');
  assert(chats[0].body.messages[0].content.includes('2026-08-25'));
  assert(!chats[0].body.messages[0].content.includes('今天'));
  await dateInput.fill('2026-08-23');
  await waitUntil(() => delayed.length === 1, 'pending historical request');
  assert(await exportButton.isDisabled());
  await chats[0].route.fulfill({ contentType: 'application/x-ndjson', body: '{"type":"delta","text":"不应保存的迟到正文"}\n{"type":"done"}\n' }).catch(() => {});
  await dateInput.fill('2026-08-25');
  await page.getByRole('button', { name: '一键全部分析（剩 1 只）', exact: true }).waitFor();
  await delayed[0].fulfill({ contentType: 'application/json', body: JSON.stringify(payload('20260823')) }).catch(() => {});
  await page.getByRole('button', { name: '刷新首板数据', exact: true }).click();
  await waitUntil(() => requests.at(-1) === '2026-08-25', 'refresh preserves explicit day');
  assert.equal(await dateInput.inputValue(), '2026-08-25');
  content = await download('2026-08-25');
  assert(!content.includes('迟到正文'));
  const store = await page.evaluate(() => JSON.parse(localStorage.getItem('vr-deepdive')));
  assert(!store['20260825|firstboard|600002'] && !store['20260823|firstboard|600002']);
  await page.screenshot({ path: path.join(output, `dates-${width}.png`), fullPage: true });
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1));
  await dateInput.fill('2026-08-24');
  await page.getByText('所选日期涨停池暂不可用', { exact: true }).first().waitFor();
  content = await download('2026-08-24');
  assert(content.includes('离线九（600009）') && !content.includes('25日'));
  await dateInput.fill('2026-08-22');
  await page.getByText('模拟历史请求失败', { exact: true }).first().waitFor();
  assert(await exportButton.isDisabled());
  assert.equal(await page.getByRole('link', { name: '600001', exact: true }).count(), 0);
  // A server running an old API must not silently return another day's rows.
  await dateInput.fill('2026-08-21');
  await page.getByText('返回交易日与所选日期不符，请刷新或检查后端版本', { exact: true }).first().waitFor();
  assert(await exportButton.isDisabled());
  await page.getByRole('button', { name: '恢复自动日期', exact: true }).click();
  await waitUntil(async () => await dateInput.inputValue() === '2026-08-26', 'reset to auto');
  assert.equal(requests.at(-1), null);
  assert.deepEqual(errors, []);
  await page.close();
  console.log(`${width}: date picker, exact-day requests, cache exports, missing history, request races and cancellation passed`);
}

(async () => {
  const browser = await chromium.launch({ headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || undefined });
  try {
    for (const width of [1393, 926, 390]) await dateExportPage(browser, width);
    for (const [name, width, height] of [['desktop', 1393, 1272], ['mobile', 390, 844]]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const fixture = { date: '20260826', total_zt: 4, first_count: 4, stocks,
        reason_note: '问财鉴权失败（HTTP 401，密钥无效或已过期）；请更新服务端 IWENCAI_API_KEY 并重启服务；已由同花顺涨停揭秘补充原因' };
      await page.route('**/api/**', route => route.fulfill({ status: 200, contentType: 'application/json',
        body: JSON.stringify({ data: route.request().url().includes('/market/first-board') ? fixture : {} }) }));
      await page.addInitScript(() => localStorage.setItem('vr-deepdive', JSON.stringify({
        '20260826|firstboard|600002': { text: '已保存的测试乙分析', tools: [], ts: Date.now() },
      })));
      await page.goto(base + '/first-board');
      const table = page.getByRole('table', { name: '首板名单', exact: true });
      await table.getByRole('link', { name: '600001', exact: true }).waitFor();
      const rowCodes = async () => (await table.locator('tbody > tr').evaluateAll(rows => rows.map(row =>
        Array.from(row.querySelectorAll('a')).find(a => /^\d{6}$/.test(a.textContent.trim()))?.textContent.trim()).filter(Boolean))).map(code => code.slice(-1)).join('');
      assert.equal(await rowCodes(), '1243');
      await page.getByRole('button', { name: '炸板排序', exact: true }).click();
      assert.equal(await rowCodes(), '2413');
      await page.getByRole('button', { name: '炸板排序', exact: true }).click();
      assert.equal(await rowCodes(), '1243');
      await page.getByRole('button', { name: '行业排序', exact: true }).click();
      await page.getByRole('button', { name: '提高行业排序优先级', exact: true }).click();
      await page.getByRole('button', { name: '提高行业排序优先级', exact: true }).click();
      assert.equal(await table.getByRole('columnheader').filter({ has: page.getByRole('button', { name: '行业排序', exact: true }) }).getAttribute('aria-sort'), 'ascending');
      await page.getByRole('button', { name: '切换行业排序方向', exact: true }).click();
      assert.equal(await rowCodes(), '3124');
      await page.getByRole('button', { name: '现价排序', exact: true }).click();
      await page.getByRole('button', { name: '当日涨幅排序', exact: true }).click();
      await page.getByRole('button', { name: '移除首封排序', exact: true }).click();
      const row = table.locator('tbody > tr').filter({ has: page.getByRole('link', { name: '600002', exact: true }) });
      await row.getByRole('button', { name: '展开', exact: true }).click();
      await page.getByText('已保存的测试乙分析', { exact: true }).waitFor();
      await page.getByRole('button', { name: '切换现价排序方向', exact: true }).click();
      await page.getByText('已保存的测试乙分析', { exact: true }).waitFor();
      await page.getByRole('button', { name: '刷新首板数据', exact: true }).click();
      assert.equal(await page.getByRole('button', { name: '移除行业排序', exact: true }).count(), 1);
      await page.screenshot({ path: path.join(output, name + '-combined.png'), fullPage: true });
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1), 'page overflow');
      await page.getByRole('button', { name: '恢复默认排序', exact: true }).click();
      assert.equal(await rowCodes(), '1243');
      await page.getByRole('button', { name: '首封排序', exact: true }).click();
      assert.equal(await rowCodes(), '3124');
      await page.getByRole('button', { name: '首封排序', exact: true }).click();
      assert.equal(await rowCodes(), '1234');
      assert.deepEqual(errors, []);
      console.log(name + ': header cycles, mixed sorting, priorities, reset and analysis identity passed');
      await page.close();
    }
    for (const width of [1393, 390]) {
      const t = await batchPage(browser, width);
      const { page, calls, row, choose, finish } = t;
      for (const code of ['600001', '600002', '600003']) await choose(code);
      assert(await page.getByRole('checkbox', { name: '选择全部首板股票', exact: true }).evaluate(el => el.indeterminate));
      await page.getByRole('button', { name: '现价排序', exact: true }).click();
      assert.equal(await page.getByRole('checkbox', { checked: true }).count(), 3);
      await page.getByLabel('同时分析数量').selectOption('2');
      await page.getByRole('button', { name: '分析所选（3 只）', exact: true }).click();
      await waitUntil(() => calls.length === 2, 'two API workers');
      await row(calls[0].code).getByRole('button', { name: '分析中', exact: true }).click();
      await row(calls[0].code).getByRole('button', { name: '收起', exact: true }).click();
      await row(calls[1].code).getByRole('button', { name: '分析中', exact: true }).click();
      assert.equal(calls.length, 2, 'inspecting rows must not start or cancel jobs');
      await page.screenshot({ path: path.join(output, `batch-${width}.png`), fullPage: true });
      await finish(calls[1], 'error');
      await waitUntil(() => calls.length === 3, 'next queued stock');
      await finish(calls[0]);
      await finish(calls[2], 'interrupted');
      await page.getByText('分析完成：成功 1 只，失败 2 只', { exact: true }).waitFor();
      assert.equal((await t.current()).length, 1, 'partial and failed responses must not be saved');
      assert(!Object.keys(await t.saved()).some(key => key.startsWith('20260819|')), 'retain five recorded dates');
      assert.equal(new Set(Object.keys(await t.saved()).map(key => key.split('|')[0])).size, 5);
      assert(calls.every(call => call.code !== '600004'), 'unselected stock must not be requested');
      await page.getByRole('button', { name: '分析所选（2 只）', exact: true }).click();
      await waitUntil(() => calls.length === 5, 'retry failed stocks only');
      await finish(calls[3]); await finish(calls[4]);
      await page.getByText('分析完成：成功 2 只', { exact: true }).waitFor();
      assert.equal((await t.current()).length, 3);
      for (const key of await t.current()) {
        assert.equal((await t.saved())[key].name, stocks.find(s => key.endsWith(s.code)).name, 'persist names for offline exports');
      }
      await page.reload();
      await page.getByRole('button', { name: '一键全部分析（剩 1 只）', exact: true }).waitFor();
      await page.getByRole('checkbox', { name: '选择全部首板股票', exact: true }).check();
      await page.getByRole('button', { name: '分析所选（1 只）', exact: true }).click();
      await waitUntil(() => calls.length === 6, 'skip cached results');
      assert.equal(calls[5].code, '600004');
      await page.evaluate(() => {
        const set = Storage.prototype.setItem;
        Storage.prototype.setItem = function(key, value) { if (key === 'vr-deepdive') throw new DOMException('quota', 'QuotaExceededError'); return set.call(this, key, value); };
      });
      await finish(calls[5]);
      await page.getByRole('alert').getByText('浏览器存储不可写，本次结果仅在当前页面保留', { exact: true }).waitFor();
      assert.equal((await t.current()).length, 3);
      assert.deepEqual(t.errors, []);
      await page.close();
      console.log(`${width}: selection, bounded API concurrency, independent errors, retries, retention, reload, quota warning passed`);
    }
    {
      const t = await batchPage(browser);
      const { page, calls, finish } = t;
      await page.getByRole('button', { name: '一键全部分析（剩 4 只）', exact: true }).click();
      await waitUntil(() => calls.length === 3, 'API limit of three');
      await page.getByRole('button', { name: '停止分析', exact: true }).click();
      await page.getByRole('button', { name: '一键全部分析（剩 4 只）', exact: true }).click();
      await waitUntil(() => calls.length === 6, 'new batch after stop');
      for (const call of calls.slice(0, 3)) await finish(call);
      assert.equal((await t.current()).length, 0, 'cancelled responses must not save');
      for (const call of calls.slice(3, 6)) await finish(call);
      await waitUntil(() => calls.length === 7, 'last queued stock');
      await finish(calls[6], 'empty');
      await page.getByText('分析完成：成功 3 只，失败 1 只', { exact: true }).waitFor();
      assert.equal((await t.current()).length, 3);
      assert.deepEqual(t.errors, []);
      await page.close();
      console.log('Stop/restart generation isolation and empty responses passed');
    }
    {
      const t = await batchPage(browser, 1393, 'cli-claude');
      const { page, calls, choose, finish, data } = t;
      assert(await page.getByLabel('同时分析数量').isDisabled());
      assert.equal(await page.getByLabel('同时分析数量').inputValue(), '1');
      for (const code of ['600001', '600002', '600003']) await choose(code);
      await page.getByRole('button', { name: '分析所选（3 只）', exact: true }).click();
      await waitUntil(() => calls.length === 1, 'serial CLI first job');
      await finish(calls[0]);
      await waitUntil(() => calls.length === 2, 'serial CLI second job');
      data.date = '20260827';
      await page.getByRole('button', { name: '刷新首板数据', exact: true }).click();
      await waitUntil(async () => await page.getByLabel('交易日', { exact: true }).inputValue() === '2026-08-27', 'automatic day rollover');
      await finish(calls[1]);
      assert.equal(calls.length, 2, 'date change must cancel the old queue');
      assert.equal((await t.current()).length, 1);
      assert(!Object.keys(await t.saved()).some(key => key.startsWith('20260827|')));
      assert.equal(await page.getByRole('checkbox', { checked: true }).count(), 0);
      await page.getByRole('button', { name: '一键全部分析（剩 4 只）', exact: true }).click();
      await waitUntil(() => calls.length === 3, 'new date CLI job');
      await page.goto(base + '/settings');
      await finish(calls[2]);
      assert.equal(calls.length, 3, 'navigation must stop the queue');
      assert.deepEqual(t.errors, []);
      await page.close();
      console.log('CLI serialization, date changes and unmount cancellation passed');
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
