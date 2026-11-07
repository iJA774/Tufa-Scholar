export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try { const body = await response.json(); message = typeof body.detail === "string" ? body.detail : message; } catch { /* use status */ }
    throw new Error(message);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}
export const json = (body: unknown): RequestInit => ({ method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
export const messageOf = (error: unknown) => error instanceof DOMException && error.name === "TimeoutError" ? "搜索超时，请重试。" : error instanceof Error ? error.message : "操作失败，请重试";
