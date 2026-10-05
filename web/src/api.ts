import type { AuditEvent, Job, Report } from "./types";

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${res.status})`);
  }
  return res.json() as Promise<T>;
}

export async function startAudit(domain: string): Promise<string> {
  const res = await fetch("/api/audits", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ domain }),
  });
  return (await json<{ id: string }>(res)).id;
}

export const getAudit = (id: string) => fetch(`/api/audits/${id}`).then((r) => json<Job>(r));

export const getShowcase = (slug: string) =>
  fetch(`/showcase/${slug}.json`).then((r) => json<Report>(r));

/** Subscribe to an audit's progress. Returns a function that closes the stream. */
export function watchAudit(id: string, onEvent: (event: AuditEvent) => void): () => void {
  const source = new EventSource(`/api/audits/${id}/events`);
  source.onmessage = (message) => {
    const event = JSON.parse(message.data) as AuditEvent;
    onEvent(event);
    // The server closes after a terminal event; closing here stops the
    // browser from treating that as a dropped connection and reconnecting.
    if (event.type === "done" || event.type === "failed") source.close();
  };
  return () => source.close();
}
