import type { ResearchMode, Run } from "../workspace/types";

export type RunEvent = {
  id: number;
  kind: string;
  data: Record<string, unknown>;
};

type SseFrame = { id?: string; event?: string; data: string };

export function splitSseFrames(buffer: string): { frames: string[]; remainder: string } {
  const frames: string[] = [];
  const boundary = /\r?\n\r?\n/g;
  let start = 0;
  let match: RegExpExecArray | null;
  while ((match = boundary.exec(buffer))) {
    frames.push(buffer.slice(start, match.index));
    start = match.index + match[0].length;
  }
  return { frames, remainder: buffer.slice(start) };
}

function parseSseFrame(frame: string): SseFrame {
  let id: string | undefined;
  let event: string | undefined;
  const data: string[] = [];
  for (const line of frame.split(/\r?\n/)) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    const value = separator === -1 ? "" : line.slice(separator + 1).replace(/^ /, "");
    if (field === "id") id = value;
    else if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return { id, event, data: data.join("\n") };
}

export function decodeResearchEvent(frame: string): RunEvent | undefined {
  const parsed = parseSseFrame(frame);
  if (parsed.event !== "research" || !parsed.data) return undefined;
  try {
    const body = JSON.parse(parsed.data) as Partial<RunEvent>;
    const id = typeof body.id === "number" ? body.id : Number(parsed.id);
    if (!Number.isSafeInteger(id) || id < 0 || typeof body.kind !== "string" || !body.data || typeof body.data !== "object" || Array.isArray(body.data)) return undefined;
    return { id, kind: body.kind, data: body.data as Record<string, unknown> };
  } catch {
    return undefined;
  }
}

function isTerminal(status: string) {
  return !["queued", "pending", "running"].includes(status);
}

export function startRunEventStream(options: {
  runId: number;
  fetchRun: (id: number, signal: AbortSignal) => Promise<Run>;
  onEvents: (events: RunEvent[]) => void;
  onRunUpdate: (run: Run) => void;
}): () => void {
  const controller = new AbortController();
  let active = true;
  let cursor = 0;
  let failures = 0;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const maxFailures = 8;

  const retry = () => {
    if (!active || failures >= maxFailures) return;
    const delay = Math.min(500 * (2 ** Math.max(0, failures - 1)), 5000);
    timer = setTimeout(connect, delay);
  };

  const connect = async () => {
    if (!active) return;
    try {
      const response = await fetch(`/api/runs/${options.runId}/events?after=${cursor}`, {
        headers: { Accept: "text/event-stream", "Cache-Control": "no-cache" },
        signal: controller.signal,
      });
      if (!response.ok || !response.body) throw new Error(`研究活动流不可用 (${response.status})`);
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (active) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const split = splitSseFrames(buffer);
        buffer = split.remainder;
        const received: RunEvent[] = [];
        for (const frame of split.frames) {
          const event = decodeResearchEvent(frame);
          if (!event || event.id <= cursor) continue;
          cursor = event.id;
          received.push(event);
        }
        if (received.length) {
          failures = 0;
          options.onEvents(received);
        }
      }
      if (!active) return;
      buffer += decoder.decode();
      const trailing = decodeResearchEvent(buffer);
      if (trailing && trailing.id > cursor) {
        cursor = trailing.id;
        failures = 0;
        options.onEvents([trailing]);
      }

      // The stream closes when a run finishes. Confirm that state before deciding
      // whether this was the terminal close or a dropped connection.
      const latestRun = await options.fetchRun(options.runId, controller.signal);
      if (!active) return;
      options.onRunUpdate(latestRun);
      if (isTerminal(latestRun.status)) return;
      failures++;
      retry();
    } catch {
      if (!active || controller.signal.aborted) return;
      failures++;
      retry();
    }
  };

  void connect();
  return () => {
    active = false;
    if (timer) clearTimeout(timer);
    controller.abort();
  };
}

type ModelTextKey = "answer" | "text";

function readJsonString(source: string, start: number): { value: string; end: number; complete: boolean } | undefined {
  if (source[start] !== '"') return undefined;
  let index = start + 1;
  let safeEnd = index;
  while (index < source.length) {
    const char = source[index];
    if (char === '"') {
      try { return { value: JSON.parse(source.slice(start, index + 1)) as string, end: index + 1, complete: true }; }
      catch { return undefined; }
    }
    if (char !== "\\") {
      index++;
      safeEnd = index;
      continue;
    }
    const escape = source[index + 1];
    if (escape === undefined) break;
    if ('"\\/bfnrt'.includes(escape)) {
      index += 2;
      safeEnd = index;
      continue;
    }
    if (escape === "u" && /^[\da-f]{4}$/i.test(source.slice(index + 2, index + 6))) {
      index += 6;
      safeEnd = index;
      continue;
    }
    break;
  }
  if (safeEnd === start + 1) return { value: "", end: source.length, complete: false };
  try { return { value: JSON.parse(`${source.slice(start, safeEnd)}\"`) as string, end: source.length, complete: false }; }
  catch { return undefined; }
}

function skipWhitespace(source: string, index: number) {
  while (/\s/.test(source[index] || "")) index++;
  return index;
}

function removePrivateThought(source: string) {
  return source.replace(/<think\b[^>]*>[\s\S]*?(?:<\/think\s*>|$)/gi, "");
}

function cleanVisibleText(source: string) {
  return removePrivateThought(source).replace(/^\s*```(?:markdown|md|text)?\s*/i, "").replace(/\s*```\s*$/, "").trim();
}

function matchesPath(path: string[], candidate: string[]) {
  return path.length === candidate.length && path.every((part, index) => part === candidate[index]);
}

/** Extract only schema-defined answer/draft text, never raw JSON or reasoning fields. */
export function extractVisibleModelText(source: string, node: string, mode?: ResearchMode | "auto"): string {
  const quick = node === "dispatcher" && mode === "quick";
  const deepOutput = node === "reporter" || node === "researcher";
  if (!quick && !deepOutput) return "";
  const input = removePrivateThought(source).replace(/^\s*```(?:json)?\s*/i, "");
  const rootStart = input.indexOf("{");
  if (rootStart === -1) return "";
  const json = input.slice(rootStart);
  const allowed = quick
    ? [["answer"]]
    : node === "researcher"
      ? [["draft", "sections", "[]", "paragraphs", "[]", "text"], ["draft", "limitations", "[]", "text"]]
      : [["sections", "[]", "paragraphs", "[]", "text"], ["limitations", "[]", "text"]];
  type Frame = { type: "object" | "array"; path: string[]; key?: string; expectingKey?: boolean };
  const stack: Frame[] = [];
  const values: string[] = [];
  for (let index = 0; index < json.length; index++) {
    const char = json[index];
    const parent = stack[stack.length - 1];
    if (char === '"') {
      const token = readJsonString(json, index);
      if (!token) continue;
      if (parent?.type === "object" && parent.expectingKey !== false) {
        parent.key = token.value;
        parent.expectingKey = false;
      } else if (parent?.type === "object" && parent.key !== undefined) {
        const path = [...parent.path, parent.key];
        if (allowed.some(candidate => matchesPath(path, candidate)) && token.value) values.push(token.value);
        parent.key = undefined;
        parent.expectingKey = true;
      }
      if (!token.complete) break;
      index = token.end - 1;
      continue;
    }
    if (char === "{") {
      const path = parent ? [...parent.path, parent.type === "object" ? (parent.key || "") : "[]"] : [];
      if (parent?.type === "object") { parent.key = undefined; parent.expectingKey = true; }
      stack.push({ type: "object", path, expectingKey: true });
    } else if (char === "[") {
      const path = parent ? [...parent.path, parent.type === "object" ? (parent.key || "") : "[]"] : [];
      if (parent?.type === "object") { parent.key = undefined; parent.expectingKey = true; }
      stack.push({ type: "array", path });
    } else if (char === "}" || char === "]") {
      stack.pop();
      if (stack.length === 0) break;
    } else if (char === "," && parent?.type === "object") {
      parent.key = undefined;
      parent.expectingKey = true;
    }
  }
  return values.map(cleanVisibleText).filter(Boolean).join("\n\n");
}

export function modelPreviewKey(node: string, callId: unknown, attempt: unknown) {
  return `${node}:${String(callId ?? "")}:${String(attempt ?? "")}`;
}

export type ModelPreview = { key: string; node: string; text: string; status: "streaming" | "complete" };

export function buildModelPreviews(events: RunEvent[], mode?: ResearchMode | "auto"): ModelPreview[] {
  const previews = new Map<string, ModelPreview>();
  const raw = new Map<string, string>();
  for (const event of events) {
    if (!event.kind.startsWith("model.")) continue;
    const node = typeof event.data.node === "string" ? event.data.node : "";
    const canShow = (node === "dispatcher" && mode === "quick") || node === "reporter" || node === "researcher";
    if (!node || !canShow) continue;
    const key = modelPreviewKey(node, event.data.call_id, event.data.attempt);
    if (event.kind === "model.started") {
      raw.set(key, "");
      previews.set(key, { key, node, text: "", status: "streaming" });
      continue;
    }
    if (event.kind === "model.failed") {
      raw.delete(key);
      previews.delete(key);
      continue;
    }
    if (event.kind !== "model.delta" && event.kind !== "model.completed") continue;
    const delta = typeof event.data.text === "string" ? event.data.text : "";
    const nextRaw = event.kind === "model.completed" && delta ? delta : `${raw.get(key) || ""}${delta}`;
    raw.set(key, nextRaw);
    const previous = previews.get(key);
    previews.set(key, { key, node, text: previous?.text || "", status: event.kind === "model.completed" ? "complete" : "streaming" });
  }
  // A batch can contain many token deltas. Assemble each call first, then parse
  // once per call rather than rescanning its growing JSON after every token.
  for (const [key, output] of raw) {
    const preview = previews.get(key);
    if (!preview) continue;
    preview.text = extractVisibleModelText(output, preview.node, mode);
  }
  return [...previews.values()].filter(preview => preview.text);
}
