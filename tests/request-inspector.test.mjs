/** Offline request fixtures; no game or model calls. */
import test from 'node:test';
import assert from 'node:assert/strict';
import { requestEvents, selectRequest, flattenState, stateValueType } from '../live/public/request-inspector.js';

const event = (id, step, attempt = 1, request = {}) => ({ id, type:'jev_request', step, attempt, request, time:'2026-09-22T01:00:00Z' });

test('actual request selection keeps retries distinct and never substitutes observation/decision data', () => {
  const one = event('run:9', 8, 1, {state:{x:3}});
  const retry = event('run:12', 8, 2, {state:{x:3}});
  const observation = {id:'run:13',type:'observation',observation:{x:99}};
  const rows = [null, one, {type:'decision',selected:'a'},retry,observation];
  assert.deepEqual(requestEvents(rows),[one,retry]);
  assert.equal(selectRequest(rows),retry);
  assert.equal(selectRequest(rows,{followLatest:false,selectedId:one.id}),one);
  assert.equal(selectRequest(rows).request.state.x,3);
});

test('pinned historical request survives window eviction but does not replace another selected ID', () => {
  const pinned=event('old:1',1),latest=event('new:2001',300);
  assert.equal(selectRequest([latest],{followLatest:false,selectedId:pinned.id,pinnedEvent:pinned}),pinned);
  assert.equal(selectRequest([latest],{followLatest:false,selectedId:'different',pinnedEvent:pinned}),latest);
  assert.equal(selectRequest([latest],{followLatest:true,selectedId:pinned.id,pinnedEvent:pinned}),latest);
  assert.equal(selectRequest([]),null);
});

test('field inventory preserves null, false, zero, empty text and containers without inventing missing fields', () => {
  const input={unknown:null,open:false,zero:0,text:'',emptyArray:[],emptyObject:{},history:[{frame:0},false]};
  const rows=flattenState(input), byPath=new Map(rows.map(row=>[row.path,row]));
  for (const [key,type,value] of [['unknown','null',null],['open','boolean',false],['zero','number',0],['text','string','']]) {
    assert.equal(byPath.get(`$.state.${key}`).type,type);
    assert.equal(byPath.get(`$.state.${key}`).value,value);
  }
  assert.equal(byPath.get('$.state.emptyArray').type,'array');
  assert.equal(byPath.get('$.state.emptyObject').type,'object');
  assert.equal(byPath.get('$.state.history[0].frame').value,0);
  assert.equal(byPath.get('$.state.history[1]').value,false);
  assert.equal(byPath.has('$.state.missing'),false);
  assert.equal(stateValueType(undefined),'undefined');
  assert.deepEqual(input.history,[{frame:0},false]);
});

test('field paths retain unusual keys and user strings exactly without treating them as markup', () => {
  const value=JSON.parse('{"a.b":{"x[0]":"<img src=x onerror=alert(1)>"},"__proto__":{"testMarker":true}}');
  const rows=flattenState(value);
  assert.ok(rows.some(row=>row.path==='$.state["a.b"]["x[0]"]' && row.value==='<img src=x onerror=alert(1)>'));
  assert.ok(rows.some(row=>row.path==='$.state.__proto__.testMarker' && row.value===true));
  assert.equal({}.testMarker,undefined);
});

test('scalar and array state schemas remain inspectable rather than being replaced by empty objects', () => {
  assert.deepEqual(flattenState('actual text'),[{path:'$.state',type:'string',value:'actual text',depth:0}]);
  assert.deepEqual(flattenState(null),[{path:'$.state',type:'null',value:null,depth:0}]);
  assert.equal(flattenState([false,0,''])[3].value,'');
});
