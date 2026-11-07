export function startRunPolling<T extends { status: string }>(options: {
  fetchRun: (signal: AbortSignal) => Promise<T>;
  onUpdate: (run: T) => void;
  onError: (error: unknown) => void;
}): () => void {
  let active = true;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let consecutiveFailures = 0;
  const controller = new AbortController();
  const poll = async () => {
    let run: T;
    try {
      run = await options.fetchRun(controller.signal);
    } catch (error) {
      if (!active) return;
      consecutiveFailures++;
      if (consecutiveFailures >= 3) options.onError(error);
      else timer = setTimeout(poll, 1200);
      return;
    }
    if (!active) return;
    consecutiveFailures = 0;
    options.onUpdate(run);
    if (active && ["queued", "pending", "running"].includes(run.status)) timer = setTimeout(poll, 1200);
  };
  timer = setTimeout(poll, 1200);
  return () => { active = false; clearTimeout(timer); controller.abort(); };
}
