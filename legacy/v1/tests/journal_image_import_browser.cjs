const { chromium } = require("playwright");
const assert = require("node:assert/strict");
const fs = require("node:fs");

const baseUrl = process.argv[2] || "http://127.0.0.1:8910";
let committed = null;

function payload(pathname) {
  if (pathname === "/api/journal/list") return { trades: [], total: 0 };
  if (pathname === "/api/journal/stats") return { available: false };
  if (pathname === "/api/journal/fees") return { fees: { commission_rate: .00025, commission_min: 5, stamp_tax_rate: .0005, transfer_fee_rate: .00001, is_default: true }, labels: { commission_rate: "佣金费率（双向）", commission_min: "佣金最低", stamp_tax_rate: "印花税", transfer_fee_rate: "过户费" } };
  if (pathname === "/api/modes") return { cards: [], playbooks_hint: ["打板", "低吸", "接力", "半路", "套利", "其它"], performance: { available: false, reason: "暂无样本" } };
  if (pathname === "/api/risk/rules") return { rules: {}, labels: {} };
  if (pathname === "/api/risk/report") return { equity: { available: false }, violations: { available: false } };
  if (pathname.startsWith("/api/risk/")) return { available: false, items: [] };
  return {};
}

(async () => {
  const executablePath = [process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"]
    .find(candidate => candidate && fs.existsSync(candidate));
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 620 } });
    page.on("pageerror", error => console.error("PAGE_ERROR", error.message));
    page.on("console", message => { if (message.type() === "error") console.error("CONSOLE_ERROR", message.text()); });
    await page.route("**/api/**", async route => {
      const request = route.request();
      const pathname = new URL(request.url()).pathname;
      if (pathname === "/api/journal/import/preview") {
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
          client_id: "browser", source_hash: "a".repeat(64), ocr: { confidence: .98, line_count: 6 },
          drafts: [{ draft_id: "draft-1", source_hash: "a".repeat(64), code: "001366", name: "播恩集团", playbook: "其它", as_planned: null, note: "",
            fills: [{ side: "buy", date: "2026-09-08", price: 17.38, shares: 300, fee: 5, confidence: .99, warnings: [] }],
            reported_pnl: 601.81, confidence: .98, warnings: [], security_candidates: [], existing_candidates: [], duplicate: false,
            raw_lines: ["播恩集团", "建仓 2026-09-08", "价格 17.380", "数量 300", "费用 5.00"] }],
        }) });
      }
      if (pathname === "/api/journal/import/commit") {
        committed = request.postDataJSON();
        return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ ok: true, created: 1, updated: 0, skipped: 0 }) });
      }
      return route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(payload(pathname)) });
    });
    await page.goto(`${baseUrl}/journal`, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "交易日志" }).waitFor();
    await page.getByRole("button", { name: "批量识别交易截图" }).click();
    const dialog = page.getByRole("dialog", { name: "批量识别交易截图" });
    await dialog.waitFor();
    assert.equal(await dialog.evaluate(node => node.parentElement === document.body), true,
      "import dialog must escape the backdrop-filter journal card via a body portal");
    const panel = dialog.locator('[data-testid="journal-image-import-panel"]');
    const panelBox = await panel.boundingBox();
    assert.ok(panelBox && panelBox.y >= 0 && panelBox.y + panelBox.height <= 620,
      "secondary dialog panel must fit inside a short viewport");
    await page.locator('input[type="file"]').setInputFiles({
      name: "trade.png", mimeType: "image/png",
      buffer: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64"),
    });
    await page.locator('input[value="001366"]').waitFor();
    assert.equal(await page.locator('input[value="播恩集团"]').inputValue(), "播恩集团");
    await page.getByText("截图卖出盈亏合计 601.81 元").waitFor();
    await page.getByRole("button", { name: /校对完成，一键入库/ }).click();
    await page.getByRole("button", { name: "批量识别交易截图" }).waitFor();
    assert.equal(committed.items[0].action, "create");
    assert.equal(committed.items[0].fills[0].fee, 5);
    assert.equal(committed.items[0].source_hashes[0], "a".repeat(64));
    console.log(JSON.stringify({ ok: true, action: committed.items[0].action }));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exit(1); });
