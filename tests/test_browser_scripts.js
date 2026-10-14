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
    classList: { toggle(name, value) { this[name] = value; }, add() {}, remove() {} },
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
  console.log("Browser scripts OK: shared scope, notice rendering, manual sync and automatic pause.");
}

main().catch(error => { console.error(error); process.exitCode = 1; });
