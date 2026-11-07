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

test('cleanup aborts and ignores stale response', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let resolveFirst, signal, updates = 0, errors = 0;
  const stop = startRunPolling({ fetchRun: s => { signal = s; return new Promise(r => { resolveFirst = r; }); }, onUpdate: () => updates++, onError: () => errors++ });
  mock.timers.tick(1200); stop(); assert.equal(signal.aborted, true);
  resolveFirst({status:'running'}); await flush(); assert.equal(updates, 0);
  assert.equal(errors, 0); mock.timers.tick(10000); assert.equal(updates, 0);
  mock.timers.reset();
});

test('retries failures and resets the count after success', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0, errors = 0;
  const updates = [];
  const stop = startRunPolling({
    fetchRun: async () => { calls++; if ([1, 2, 4, 5].includes(calls)) throw Error('temporary'); return {status: calls === 6 ? 'succeeded' : 'running'}; },
    onUpdate: r => updates.push(r.status), onError: () => errors++,
  });
  for (let i = 0; i < 6; i++) { mock.timers.tick(1200); await flush(); }
  mock.timers.tick(10000);
  assert.equal(calls, 6); assert.equal(errors, 0); assert.deepEqual(updates, ['running', 'succeeded']);
  stop(); mock.timers.reset();
});

test('reports the third consecutive failure and stops', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0; const errors = [];
  const stop = startRunPolling({ fetchRun: async () => { calls++; throw Error(`failure ${calls}`); }, onUpdate: assert.fail, onError: e => errors.push(e.message) });
  for (let i = 0; i < 3; i++) { mock.timers.tick(1200); await flush(); }
  mock.timers.tick(10000);
  assert.equal(calls, 3); assert.deepEqual(errors, ['failure 3']);
  stop(); mock.timers.reset();
});

test('cleanup cancels a scheduled retry and ignores an aborted rejection', async () => {
  mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0, errors = 0, rejectRequest;
  const stop = startRunPolling({ fetchRun: () => { calls++; return Promise.reject(Error('temporary')); }, onUpdate: assert.fail, onError: () => errors++ });
  mock.timers.tick(1200); await flush(); stop(); mock.timers.tick(10000);
  assert.equal(calls, 1); assert.equal(errors, 0);
  const stopPending = startRunPolling({ fetchRun: () => new Promise((_, reject) => { rejectRequest = reject; }), onUpdate: assert.fail, onError: () => errors++ });
  mock.timers.tick(1200); stopPending(); rejectRequest(Error('aborted')); await flush(); mock.timers.tick(10000);
  assert.equal(errors, 0); mock.timers.reset();
});
