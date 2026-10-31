// Run with Node. Exercises the VPN test picker, progress state and cleanup result.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

class Element {
  constructor() {
    this.value = ''; this.disabled = false; this.hidden = true; this.open = false;
    this.textContent = ''; this.className = ''; this.listeners = {};
  }
  addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
  replaceChildren(...children) { this.options = children; this.value = children[0]?.value || ''; }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

const ids = ['Open','Dialog','Close','Tenant','Connection','Site','Device','Port','Run','Message',
  'Activity','Result','ResultTitle','Status','Tunnel','Target','Address','Cleanup','Detail'];
const elements = {};
for (const id of ids) elements['vpnTest' + id] = new Element();
elements.vpnTestPort.value = '22';
const state = {vpnTestOptions:null,vpnTestBusy:false};
let calls = [];
const options = {
  connections:[{connection_id:'customer-vpn',name:'Customer VPN'}],
  sites:[{id:20,name:'DC',connection_id:'customer-vpn'}],
  devices:[{id:7,name:'SW01',site_id:20}],
};
const context = vm.createContext({
  elements, state, Option:function(label,value){return {label,value};},
  requireOperatorToken:()=>true, operatorHeaders:()=>({}), encodeURIComponent,
  apiRequest:async (url, request) => {
    calls.push([url,request]);
    if (url.includes('/assignments/options')) return options;
    if (url.endsWith('/test')) return {
      status:'passed',tunnel_established:true,target_reachable:true,
      target_ip:'10.0.0.10',target_port:22,assigned_address:'100.64.0.2',
      disconnected:true,duration_ms:1200,message:'Tunnel and target passed.',
    };
    return {tenants:[{id:1,name:'Customer'}]};
  },
});
const app = fs.readFileSync(path.join(__dirname, '../atlas/static/app.js'), 'utf8');
const start = app.indexOf('function resetVPNTestResult()');
const end = app.indexOf('for (const input of [', start);
assert.ok(start >= 0 && end > start);
vm.runInContext(app.slice(start, end), context);

(async () => {
  await vm.runInContext('openVPNTest()', context);
  assert.equal(elements.vpnTestDialog.open, true);
  elements.vpnTestTenant.value = '1';
  await vm.runInContext('loadVPNTestOptions()', context);
  elements.vpnTestConnection.value = 'customer-vpn';
  vm.runInContext('renderVPNTestSites()', context);
  assert.equal(elements.vpnTestSite.options.length, 2);
  elements.vpnTestSite.value = '20';
  vm.runInContext('renderVPNTestDevices()', context);
  assert.equal(elements.vpnTestDevice.options[1].label, 'SW01');
  elements.vpnTestDevice.value = '7';
  vm.runInContext('updateVPNTestRun()', context);
  assert.equal(elements.vpnTestRun.disabled, false);
  await vm.runInContext('runVPNTest()', context);
  assert.equal(elements.vpnTestActivity.hidden, true);
  assert.equal(elements.vpnTestStatus.textContent, 'Passed');
  assert.match(elements.vpnTestTunnel.textContent, /established/);
  assert.match(elements.vpnTestCleanup.textContent, /cleaned up/);
  assert.match(calls.at(-1)[0], /customer-vpn\/test$/);
  assert.equal(JSON.stringify(calls.at(-1)[1].body), JSON.stringify({device_id:7,target_port:22}));
  console.log('VPN test picker, progress, result and cleanup UI passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
