const { test, mock } = require('node:test');
const assert = require('node:assert/strict');
const { startRunPolling } = require(process.argv[2]);
const flush = () => new Promise(resolve => setImmediate(resolve));

test('polls without overlap, stops at terminal state', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let resolveFirst, calls = 0;
  const updates = [];
  const stop = startRunPolling({ fetchRun: () => { calls++; return calls === 1 ? new Promise(r => { resolveFirst = r; }) : Promise.resolve({status: 'succeeded'}); }, onUpdate: r => updates.push(r.status), onError: assert.fail });
  mock.timers.tick(1200); mock.timers.tick(10000);
  assert.equal(calls, 1);
  resolveFirst({status: 'running'}); await flush();
  mock.timers.tick(1200); await flush();
  mock.timers.tick(10000);
  assert.deepEqual(updates, ['running', 'succeeded']); assert.equal(calls, 2);
  stop(); mock.timers.reset();
});

test('cleanup aborts and ignores stale response; error stops polling', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let resolveFirst, signal, updates = 0, errors = 0;
  const stop = startRunPolling({ fetchRun: s => { signal = s; return new Promise(r => { resolveFirst = r; }); }, onUpdate: () => updates++, onError: () => errors++ });
  mock.timers.tick(1200); stop(); assert.equal(signal.aborted, true);
  resolveFirst({status:'running'}); await flush(); assert.equal(updates, 0);
  let calls = 0;
  const stopNext = startRunPolling({ fetchRun: async () => { calls++; throw Error('failed'); }, onUpdate: () => updates++, onError: () => errors++ });
  mock.timers.tick(1200); await flush(); mock.timers.tick(10000);
  assert.equal(calls, 1); assert.equal(errors, 1);
  stopNext(); mock.timers.reset();
});
