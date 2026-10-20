import type { AuditEvent, Job, Report, Representation } from "./types";

/** A request the server answered with an error, carrying its status so
 *  callers can tell bad input (422) from a limit (429, 503). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null);
    throw new ApiError(body?.detail ?? `Request failed (${res.status})`, res.status);
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

export const getRepresentation = (id: string) =>
  fetch(`/api/audits/${id}/representation`).then((r) => json<Representation>(r));

export const getShowcaseRepresentation = (slug: string) =>
  fetch(`/showcase/${slug}.representation.json`).then((r) => json<Representation>(r));
