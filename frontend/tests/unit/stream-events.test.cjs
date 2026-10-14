const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('typescript');
const root = path.resolve(__dirname, '../..');

function loadHook(filename, exportName) {
 const events = [], states = [], sources = [];
 let cleanup;
 class Source {
  constructor() { this.listeners = new Map(); this.closed = false; sources.push(this); }
  addEventListener(type, listener) { this.listeners.set(type, listener); }
  removeEventListener(type) { this.listeners.delete(type); }
  close() { this.closed = true; }
  emit(type, data) { this.listeners.get(type)?.({data: JSON.stringify(data), lastEventId:'1'}); }
 }
 const exports = {};
 const code = ts.transpileModule(fs.readFileSync(path.join(root, filename), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 vm.runInNewContext(code, {exports, EventSource:Source, require: name => name === 'react' ? {
  useRef: value => ({current:value}), useEffect: fn => { cleanup = fn(); },
  useState: value => [value, next => states.push(next)],
 } : {sessionEventsUrl: id => '/session/' + id}});
 exports[exportName]('id', event => events.push(event), true);
 return {source:sources[0], events, states, cleanup};
}
for (const [filename, name] of [['src/hooks/useQueryEvents.ts','useQueryEvents'],['src/hooks/useSessionEvents.ts','useSessionEvents']]) {
 for (const type of ['stream.end','stream.closed']) test(name + ' closes on ' + type, () => {
  const fixture = loadHook(filename, name);
  fixture.source.emit(type, {status:'idle'});
  assert.equal(fixture.source.closed, true);
  assert.equal(fixture.events.length, 1);
 });
 test(name + ' permits reconnect on transport loss and closes on unmount', () => {
  const fixture = loadHook(filename, name);
  fixture.source.onerror?.({});
  assert.equal(fixture.source.closed, false);
  fixture.cleanup();
  assert.equal(fixture.source.closed, true);
 });
}


test('terminal analysis messages stop polling while running and first reads continue', () => {
 const exports = {};
 const code = ts.transpileModule(fs.readFileSync(path.join(root, 'src/api/polling.ts'), 'utf8'), {compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 vm.runInNewContext(code, {exports});
 for (const status of ['COMPLETED','PARTIAL','FAILED','CANCELLED']) assert.equal(exports.analysisPollInterval(status), false);
 assert.equal(exports.analysisPollInterval('EXECUTING'), 3000);
 assert.equal(exports.analysisPollInterval(undefined), 3000);
});
