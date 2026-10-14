// Classic browser scripts share a global scope. Check that scope and render
// real notices without providing the row helper from the test itself.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { spawnSync } = require("node:child_process");

const root = path.resolve(__dirname, "..");
const html = fs.readFileSync(path.join(root, "static/index.html"), "utf8");
const scripts = [...html.matchAll(/<script src="\/([^\"]+)" defer><\/script>/g)]
  .map(match => path.join(root, "static", match[1]));
assert.ok(scripts.length > 0);
const syntax = spawnSync(process.execPath, ["--check"], {
  input: scripts.map(file => fs.readFileSync(file, "utf8")).join("\n;\n"),
  encoding: "utf8",
});
assert.equal(syntax.status, 0, syntax.stderr);

function element() {
  return {
    textContent: "", children: [], disabled: false,
    classList: { toggle(name, value) { this[name] = value; },
      add(...names) { names.forEach(name => { this[name] = true; }); },
      remove(...names) { names.forEach(name => { this[name] = false; }); } },
    addEventListener() {},
    append(...children) { this.children.push(...children); },
    replaceChildren(...children) { this.children = children; },
    elements: { url: { value: "" }, read_token: { value: "" },
      enabled: { checked: false }, file: { addEventListener() {} }, client_id: {}, client_secret: {} },
  };
}
const nodes = new Map();
function select(selector) {
  if (!nodes.has(selector)) nodes.set(selector, element());
  return nodes.get(selector);
}
const calls = [];
const notices = [{ source: "XP", notice: "Dados atualizados pela Pluggy.",
  processed_at: "2026-01-02T12:00:00Z" }];
let financeRefreshes = 0;
const events = [];
const context = vm.createContext({
  document: { querySelector: select, createElement: element, hidden: false,
    addEventListener() {}, dispatchEvent(event) { events.push(event.type); } },
  window: { setInterval() {} },
  localAuth: { token: () => "fixture", ready: () => new Promise(() => {}) },
  Event: class { constructor(type) { this.type = type; } },
  refresh: async () => { financeRefreshes += 1; },
  request: async (route, options) => {
    calls.push({ route, options });
    if (route === "/investments/pluggy/status") return { configured: true, credentials_saved: true };
    if (route === "/pluggy/webhooks/status") return {
      configured: true, enabled: false, url: "https://fixture.up.railway.app", notices,
    };
    if (route === "/pluggy/webhooks/check") return {
      synchronized: ["XP"], processed: 1, message: "Avisos conferidos.",
    };
    throw new Error("Unexpected request: " + route);
  },
});

async function main() {
  for (const name of ["xp-import.js", "pluggy-xp.js", "webhooks.js"]) {
    vm.runInContext(fs.readFileSync(path.join(root, "static", name), "utf8"), context, { filename: name });
  }
  await vm.runInContext("refreshWebhookStatus()", context);
  const rows = select("#webhook-notices").children;
  assert.equal(rows.length, 1);
  assert.equal(rows[0].children[0].textContent, "XP");
  assert.match(rows[0].children[1].textContent, /Dados atualizados/);
  assert.equal(select("#webhook-check").disabled, false);
  await vm.runInContext("checkWebhooks(true)", context);
  const manual = calls.filter(call => call.route === "/pluggy/webhooks/check");
  assert.equal(manual.length, 1);
  assert.deepEqual(JSON.parse(manual[0].options.body), { manual: true });
  assert.equal(financeRefreshes, 1);
  assert.deepEqual(events, ["investment-data-change"]);
  assert.equal(select("#webhook-status").classList.error, false);
  assert.match(select("#webhook-status").textContent, /Atualizado pelos avisos: XP/);
  await vm.runInContext("checkWebhooks()", context);
  assert.equal(calls.filter(call => call.route === "/pluggy/webhooks/check").length, 1);
  // A valid zero is different from a missing result; failed/incomplete data
  // must clear previously rendered earnings rather than invent a return.
  context.money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  context.formatDate = value => value.split("-").reverse().join("/");
  context.investmentsView = { hidden: true };
  vm.runInContext(fs.readFileSync(path.join(root, "static/performance.js"), "utf8"), context);
  const result = { basis: "pluggy", status: "ready", gain_cents: 2450, portfolio_percent: 1.25,
    cdi_percent: null, baseline: { as_of: "2026-01-01" }, latest_snapshot: { as_of: "2026-01-31" } };
  const render = data => vm.runInContext(`renderPortfolioResult(${JSON.stringify(data)})`, context);
  render(result);
  assert.match(select("#portfolio-result-amount").textContent, /^\+R\$\s24,50$/);
  assert.equal(select("#portfolio-result-percent").textContent, "+1,25%");
  assert.match(select("#portfolio-result-period").textContent, /01\/01\/2026 a 31\/01\/2026/);
  assert.match(select("#portfolio-result-benchmark").textContent, /aguardando/);
  render({ ...result, gain_cents: -2450, portfolio_percent: -1.25, cdi_percent: .8 });
  assert.match(select("#portfolio-result-amount").textContent, /^−R\$\s24,50$/);
  assert.equal(select("#portfolio-result-percent").textContent, "−1,25%");
  assert.equal(select("#portfolio-result-amount").classList.negative, true);
  assert.match(select("#portfolio-result-benchmark").textContent, /0,80%/);
  render({ ...result, gain_cents: 0, portfolio_percent: 0 });
  assert.equal(select("#portfolio-result-percent").textContent, "0,00%");
  render({ ...result, status: "review_flows", message: "Revise as transferências." });
  assert.equal(select("#portfolio-result-amount").textContent, "—");
  assert.equal(select("#portfolio-result-percent").textContent, "—");
  assert.equal(select("#portfolio-result-note").textContent, "Revise as transferências.");
  console.log("Browser scripts OK: shared scope, notice rendering, manual sync and automatic pause.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
