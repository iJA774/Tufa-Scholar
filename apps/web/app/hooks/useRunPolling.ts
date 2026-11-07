"use client";

import { useEffect, useRef } from "react";
import type { Run } from "../workspace/types";
import { startRunPolling } from "./runPolling";

type Options = {
  run: Run | undefined;
  projectId: number | undefined;
  conversationId: number | undefined;
  fetchRun: (id: number, signal: AbortSignal) => Promise<Run>;
  onUpdate: (run: Run) => void;
  onError: (error: unknown) => void;
};

export function useRunPolling(options: Options) {
  const latest = useRef(options);
  latest.current = options;
  const id = options.run?.id;
  const active = !!options.run && ["queued", "pending", "running"].includes(options.run.status);
  useEffect(() => {
    if (id === undefined || !active) return;
    const projectId = options.projectId;
    const conversationId = options.conversationId;
    const matches = () => latest.current.run?.id === id && latest.current.projectId === projectId && latest.current.conversationId === conversationId;
    return startRunPolling({
      fetchRun: signal => latest.current.fetchRun(id, signal),
      onUpdate: run => { if (matches()) latest.current.onUpdate(run); },
      onError: error => { if (matches()) latest.current.onError(error); },
    });
  }, [id, active, options.projectId, options.conversationId]);
}
