/** Shared HTTP boundary for all frontend features. */
export async function api(path: string, body?: unknown): Promise<any> {
  const response = await fetch("/api/" + path.replace(/^\//, ""), {
    method: body === undefined ? "GET" : "POST",
    headers:
      body === undefined
        ? undefined
        : { "Content-Type": "application/json", "X-ZhiXing-Local": "1" },
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: "same-origin",
  });
  const value = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(value.detail || `HTTP ${response.status}`);
  return value;
}
