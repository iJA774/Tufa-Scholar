"use client";

import { useEffect, useRef, useState } from "react";
import type { Run } from "../workspace/types";
import { startRunEventStream, type RunEvent } from "./runEvents";

type Options = {
  run: Run | undefined;
  projectId: number | undefined;
  conversationId: number | undefined;
  fetchRun: (id: number, signal: AbortSignal) => Promise<Run>;
  onRunUpdate: (run: Run) => void;
};

type EventState = { scope: string; events: RunEvent[] };

export function useRunEvents(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const [state, setState] = useState<EventState>({ scope: "", events: [] });
  const id = options.run?.id;
  const projectId = options.projectId;
  const conversationId = options.conversationId;
  const scope = `${id ?? "none"}:${projectId ?? "none"}:${conversationId ?? "none"}`;

  useEffect(() => {
    if (id === undefined) {
      setState({ scope, events: [] });
      return;
    }
    setState({ scope, events: [] });
    const matches = () => latest.current.run?.id === id
      && latest.current.projectId === projectId
      && latest.current.conversationId === conversationId;
    return startRunEventStream({
      runId: id,
      fetchRun: (runId, signal) => latest.current.fetchRun(runId, signal),
      onEvents: incoming => {
        if (!matches()) return;
        setState(previous => {
          const existing = previous.scope === scope ? previous.events : [];
          const known = new Set(existing.map(event => event.id));
          const next = [...existing, ...incoming.filter(event => !known.has(event.id))];
          return { scope, events: next.length > 2000 ? next.slice(-2000) : next };
        });
      },
      onRunUpdate: next => {
        if (matches()) latest.current.onRunUpdate(next);
      },
    });
  }, [id, projectId, conversationId, scope]);

  return state.scope === scope ? state.events : [];
}
