const { test } = require('node:test');
const assert = require('node:assert/strict');
const { buildModelPreviews, decodeResearchEvent, extractVisibleModelText, splitSseFrames, startRunEventStream } = require(process.argv[2]);

test('splits complete SSE frames and preserves a partial frame', () => {
  const first = splitSseFrames('id: 3\nevent: research\ndata: {"id":3,"kind":"node.started","data":{"node":"planner"}}\n\n eve');
  assert.equal(first.frames.length, 1);
  assert.equal(first.remainder, ' eve');
  const event = decodeResearchEvent(first.frames[0]);
  assert.deepEqual(event, { id: 3, kind: 'node.started', data: { node: 'planner' } });
});

test('ignores non research and malformed SSE events', () => {
  assert.equal(decodeResearchEvent('event: message\ndata: {"id":4,"kind":"node.started","data":{}}'), undefined);
  assert.equal(decodeResearchEvent('event: research\ndata: {not json}'), undefined);
});

test('extracts only quick answer text, including a partial structured JSON string', () => {
  const partial = '{"needs_research":false,"reason":"private route detail","answer":"A useful answer with \\u4e2d';
  assert.equal(extractVisibleModelText(partial, 'dispatcher', 'quick'), 'A useful answer with 中');
  assert.equal(extractVisibleModelText(partial, 'dispatcher', 'deep'), '');
});

test('extracts researcher and reporter paragraph text without exposing other fields', () => {
  const report = '{"sections":[{"heading":"Result","paragraphs":[{"text":"First claim.","citations":["P1"]},{"text":"Second claim.","citations":[]}]}],"limitations":[{"text":"A remaining gap."}]}';
  const researcher = '{"action":"finish","draft":{"sections":[{"heading":"Result","paragraphs":[{"text":"First claim.","citations":["P1"]},{"text":"Second claim.","citations":[]}]}],"limitations":[{"text":"A remaining gap."}]},"findings":[{"text":"Not a report preview.","citations":[]}]}';
  assert.equal(extractVisibleModelText(report, 'reporter', 'deep'), 'First claim.\n\nSecond claim.\n\nA remaining gap.');
  assert.equal(extractVisibleModelText(researcher, 'researcher', 'deep'), 'First claim.\n\nSecond claim.\n\nA remaining gap.');
  assert.equal(extractVisibleModelText('{"internal":{"text":"private"},"sections":[]}', 'reporter', 'deep'), '');
  assert.equal(extractVisibleModelText('<think>{"answer":"private"}</think>```json\n{"answer":"public"}', 'dispatcher', 'quick'), 'public');
  assert.equal(extractVisibleModelText('{"rationale":"internal planning","source":"web"}', 'planner', 'deep'), '');
  assert.equal(extractVisibleModelText('{"answer":"direct"}', 'researcher', 'deep'), '');
});

test('assembles model deltas and keeps only visible output from allowed nodes', () => {
  const events = [
    { id: 1, kind: 'model.started', data: { node: 'reporter', call_id: 'r1', attempt: 1 } },
    { id: 2, kind: 'model.delta', data: { node: 'reporter', call_id: 'r1', attempt: 1, text: '{"reason":"internal","sections":[{"paragraphs":[{"text":"A' } },
    { id: 3, kind: 'model.delta', data: { node: 'reporter', call_id: 'r1', attempt: 1, text: ' visible draft."}]}]}' } },
    { id: 4, kind: 'model.started', data: { node: 'planner', call_id: 'p1', attempt: 1 } },
    { id: 5, kind: 'model.delta', data: { node: 'planner', call_id: 'p1', attempt: 1, text: '{"rationale":"private"}' } },
  ];
  const preview = buildModelPreviews(events, 'deep');
  assert.equal(preview.length, 1);
  assert.equal(preview[0].text, 'A visible draft.');
});

test('streams persisted events and stops reconnecting after terminal confirmation', async () => {
  const originalFetch = global.fetch;
  const enc = new TextEncoder();
  const frame = `id: 9\nevent: research\ndata: ${JSON.stringify({ id: 9, kind: 'node.completed', data: { node: 'planner' } })}\n\n`;
  let requests = 0;
  const received = [];
  let resolveTerminal;
  const terminal = new Promise(resolve => { resolveTerminal = resolve; });
  global.fetch = async (url, init) => {
    requests++;
    assert.match(String(url), /\/api\/runs\/17\/events\?after=0$/);
    assert.equal(init.headers.Accept, 'text/event-stream');
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(enc.encode(frame)); controller.close(); } }));
  };
  const stop = startRunEventStream({
    runId: 17,
    fetchRun: async () => ({ id: 17, status: 'succeeded' }),
    onEvents: events => received.push(...events),
    onRunUpdate: run => resolveTerminal(run),
  });
  try {
    const run = await terminal;
    assert.equal(run.status, 'succeeded');
    assert.deepEqual(received.map(event => event.id), [9]);
    assert.equal(requests, 1);
  } finally {
    stop();
    global.fetch = originalFetch;
  }
});
