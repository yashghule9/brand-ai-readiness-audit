import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { getAudit, getRepresentation, watchAudit } from "../api";
import Report from "../Report";
import type { Job, PageEvent } from "../types";

// The skills a page passes through, in pipeline order, named by what they
// do. Off-site corroboration runs once after the crawl, so it isn't here.
const STAGES: [skill: string, label: string][] = [
  ["website-observer", "Read the page"],
  ["crawl-render-audit", "Render JavaScript"],
  ["content-cleaner", "Extract the main content"],
  ["query-guided-discovery", "Follow answers across pages"],
];

type Phase = "loading" | "queued" | "running" | "done" | "failed";

export default function AuditPage() {
  const { id = "" } = useParams();
  const [job, setJob] = useState<Job | null>(null);
  const [phase, setPhase] = useState<Phase>("loading");
  const [pages, setPages] = useState<PageEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const loadRepresentation = useCallback(() => getRepresentation(id), [id]);

  useEffect(() => {
    let cancelled = false;
    let stop = () => {};

    const finish = () =>
      getAudit(id).then((done) => {
        if (cancelled) return;
        setJob(done);
        setPhase(done.status === "done" ? "done" : "failed");
        setError(done.error);
      });

    getAudit(id)
      .then((current) => {
        if (cancelled) return;
        setJob(current);
        if (current.status === "done" || current.status === "failed") {
          setPhase(current.status);
          setError(current.error);
          return;
        }
        setPhase(current.status);
        stop = watchAudit(id, (event) => {
          // The stream replays from the start on every connection, and
          // always opens with a status event — so reset on it, and a
          // browser reconnect can never double-count pages.
          if (event.type === "status") {
            setPages([]);
            setPhase("running");
          } else if (event.type === "page") {
            setPages((seen) => [...seen, event]);
          } else {
            void finish();
          }
        });
      })
      .catch((err: Error) => {
        if (cancelled) return;
        setError(err.message);
        setPhase("failed");
      });

    return () => {
      cancelled = true;
      stop();
    };
  }, [id]);

  if (phase === "done" && job?.report) return <Report report={job.report} loadRepresentation={loadRepresentation} />;

  if (phase === "failed") {
    return (
      <div className="notice" role="alert">
        <h2>{job ? `The audit of ${job.domain} stopped` : "This audit doesn't exist"}</h2>
        <p>{error ?? "No reason was recorded."}</p>
        <p>
          <Link to="/">Start a new audit</Link>
        </p>
      </div>
    );
  }

  const latest = pages.at(-1);
  const engaged = new Set(latest?.skills_engaged ?? []);

  return (
    <main aria-busy="true">
      <header className="run-head">
        <h1>{job?.domain ?? "Loading"}</h1>
        <p className="run-status" aria-live="polite">
          {phase === "queued" || (phase === "running" && !latest)
            ? phase === "queued"
              ? "Waiting for the audit ahead of this one to finish."
              : "Fetching robots.txt and the first page."
            : latest
              ? `Crawling. ${latest.pages_crawled} of up to 15 pages read, ${latest.queued} links waiting.`
              : "Loading."}
        </p>
      </header>

      <div className="run-grid">
        <section aria-label="Pages crawled">
          <ol className="crawl-log">
            {pages.map((page) => (
              <li key={page.url}>
                <span className={`outcome outcome-${page.outcome}`}>{OUTCOME[page.outcome]}</span>
                <span className="url" title={page.url}>
                  {page.url.replace(/^https?:\/\//, "")}
                </span>
              </li>
            ))}
          </ol>
          {pages.length === 0 && <p className="waiting">Pages appear here as they're read.</p>}
        </section>

        <aside>
          <dl className="counters">
            <div>
              <dt>Read</dt>
              <dd>{latest?.pages_crawled ?? 0}</dd>
            </div>
            <div>
              <dt>Analysed</dt>
              <dd>{latest?.pages_analysed ?? 0}</dd>
            </div>
            <div>
              <dt>Rendered</dt>
              <dd>{latest?.pages_rendered ?? 0}</dd>
            </div>
          </dl>
          <ul className="skills" aria-label="Checks running">
            {STAGES.map(([skill, label]) => (
              <li key={skill} className={engaged.has(skill) ? "engaged" : undefined}>
                {label}
              </li>
            ))}
          </ul>
        </aside>
      </div>
    </main>
  );
}

const OUTCOME: Record<PageEvent["outcome"], string> = {
  analysed: "Analysed",
  blocked: "Blocked",
  unreachable: "Unreachable",
};
