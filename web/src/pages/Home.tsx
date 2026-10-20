import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router";
import { ApiError, getShowcase, startAudit } from "../api";
import { scoreText } from "../format";
import { SHOWCASE } from "../showcase";
import type { Report } from "../types";

export default function Home() {
  const navigate = useNavigate();
  const [domain, setDomain] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      navigate(`/audit/${await startAudit(domain)}`);
    } catch (err) {
      // Three different failures, three different instructions: the API
      // isn't up, the input isn't a domain, or a limit was hit (the server's
      // own message says how long to wait).
      setError(
        !(err instanceof ApiError)
          ? "The audit server isn't reachable right now. Try again in a moment."
          : err.status === 422
            ? "That doesn't look like a domain. Try one like example.com."
            : err.message,
      );
      setBusy(false);
    }
  }

  return (
    <main>
      <section className="hero">
        <h1>Can AI assistants read your website?</h1>
        <p className="hero-lede">
          Crawls up to fifteen pages, checks them against 38 known ways sites fail AI crawlers, and says
          plainly what it could not check.
        </p>
        <form className="audit-form" onSubmit={submit}>
          <label htmlFor="domain" className="visually-hidden">
            Website domain
          </label>
          <input
            id="domain"
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            placeholder="example.com"
            autoComplete="url"
            spellCheck={false}
            required
          />
          <button className="button" disabled={busy}>
            {busy ? "Starting…" : "Run audit"}
          </button>
        </form>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </section>

      <Samples />
    </main>
  );
}

function Samples() {
  const [reports, setReports] = useState<Record<string, Report>>({});

  useEffect(() => {
    for (const { slug } of SHOWCASE) {
      // A sample that hasn't been generated yet is simply left out.
      getShowcase(slug)
        .then((report) => setReports((r) => ({ ...r, [slug]: report })))
        .catch(() => {});
    }
  }, []);

  const ready = SHOWCASE.filter(({ slug }) => reports[slug]);
  if (ready.length === 0) return null;

  return (
    <section className="samples" aria-labelledby="samples-title">
      <h2 id="samples-title">Sample audits</h2>
      <p>Real audits of public sites, run with this tool.</p>
      <ul className="sample-list">
        {ready.map(({ slug, note }) => (
          <li key={slug}>
            <Link to={`/sample/${slug}`}>
              <span className="sample-domain">{slug}</span>
              <span className="sample-score">{scoreText(reports[slug]!.summary.readiness_score)}</span>
              <span className="sample-note">{note}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}
