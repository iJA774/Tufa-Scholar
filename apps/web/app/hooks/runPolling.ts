export function startRunPolling<T extends { status: string }>(options: {
  fetchRun: (signal: AbortSignal) => Promise<T>;
  onUpdate: (run: T) => void;
  onError: (error: unknown) => void;
}): () => void {
  let active = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const controller = new AbortController();
  const poll = async () => {
    try {
      const run = await options.fetchRun(controller.signal);
      if (!active) return;
      options.onUpdate(run);
      if (active && ["queued", "pending", "running"].includes(run.status)) timer = setTimeout(poll, 1200);
    } catch (error) {
      if (active) options.onError(error);
    }
  };
  timer = setTimeout(poll, 1200);
  return () => { active = false; clearTimeout(timer); controller.abort(); };
}
