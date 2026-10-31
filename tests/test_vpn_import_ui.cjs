// Run with Node. Exercises import state without a browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.value = ''; this.dataset = {}; this.files = []; this.disabled = false;
    this.hidden = true; this.open = false; this.attributes = {}; this.listeners = {};
    this.classList = { toggle: () => {} };
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  replaceChildren(...children) { this.options = children; this.value = children[0]?.value || ''; }
  setAttribute(k, v) { this.attributes[k] = v; }
  removeAttribute(k) { delete this.attributes[k]; }
  focus() { this.focused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; for (const fn of this.listeners.close || []) fn(); }
}
const nodes = new Map();
const document = { querySelector(selector) { if (!nodes.has(selector)) nodes.set(selector, new Element()); return nodes.get(selector); } };
const select = id => document.querySelector('#vpn-import-' + id);
let settlePlan;
let requestCount = 0;
let payload;
const context = vm.createContext({
  document, Option: function(label, value) { return { label, value }; },
  requireOperatorToken: () => true, operatorHeaders: () => ({}),
  openVPNAssignment: () => {},
  apiRequest: async (url, options) => {
    if (url.endsWith('/plan')) {
      requestCount++; payload = options.body;
      return await new Promise(resolve => { settlePlan = resolve; });
    }
    if (url.endsWith('/apply')) return {connection_id:'vpn-viena',tenant_id:1};
    return {tenants:[],vrfs:[]};
  },
});
const app = fs.readFileSync(path.join(__dirname, '../atlas/static/app.js'), 'utf8');
const start = app.indexOf('// VPN profile import never');
const end = app.indexOf('elements.vpnAssignmentOpen.addEventListener', start);
assert.ok(start >= 0 && end > start);
vm.runInContext(app.slice(start, end), context);

(async () => {
  select('dialog').open = true;
  select('profile').value = 'VPN_Viena';
  vm.runInContext('suggestVPNConnectionDetails()', context);
  assert.equal(select('id').value, 'vpn-viena');
  assert.equal(select('name').value, 'VPN_Viena');
  select('tenant').value = '1'; select('vrf').value = '30';
  select('username').value = 'person'; select('password').value = 'secret';
  vm.runInContext("vpnImport.data = 'ZW5jb2RlZA=='", context);
  select('id').value = 'minedu-VPN';
  await vm.runInContext('planVPNImport()', context);
  assert.equal(requestCount, 0);
  assert.equal(select('advanced').open, true);
  assert.equal(select('id').attributes['aria-invalid'], 'true');
  assert.match(select('message').textContent, /lowercase/);
  select('id').value = 'minedu-vpn';
  const promise = vm.runInContext('planVPNImport()', context);
  assert.equal(select('activity').hidden, false);
  assert.equal(select('plan').disabled, true);
  assert.deepEqual(Array.from(payload.allowed_prefixes), []);
  settlePlan({tenant_id:1,management_vrf_id:30,gateway:'vpn.example',port:500,network_id:11,
    allowed_prefixes:[],registry_action:'create',secret_action:'create',apply_allowed:true,plan_hash:'a'.repeat(64)});
  await promise;
  assert.equal(select('activity').hidden, true);
  assert.equal(select('apply').disabled, false);
  await vm.runInContext('applyVPNImport()', context);
  assert.equal(select('assign').hidden, false);
  assert.equal(select('password').value, '');
  assert.match(select('message').textContent, /not been connected/);
  console.log('VPN import state, validation, progress and Site handoff passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
