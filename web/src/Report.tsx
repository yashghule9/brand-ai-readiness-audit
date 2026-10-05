import { useState } from "react";
import { scoreText } from "./format";
import type { Axis, AxisScore, Finding, Report as ReportData, Severity } from "./types";

const AXES: { key: Axis; label: string; question: string }[] = [
  { key: "visibility", label: "Visibility", question: "Can assistants reach and read the site?" },
  { key: "engagement", label: "Engagement", question: "Will a visitor sent by an assistant stay?" },
  { key: "staleness", label: "Freshness", question: "Can assistants tell current facts from old ones?" },
  { key: "identity", label: "Identity", question: "Is the brand attributed to the right entity?" },
];

const SEVERITY_ORDER: Severity[] = ["critical", "high", "medium", "low"];
const SEVERITY_LABEL: Record<Severity, string> = {
  critical: "Critical",
  high: "High",
  medium: "Medium",
  low: "Low",
};

type Tab = "overview" | "findings" | "actions";

export default function Report({ report }: { report: ReportData }) {
  const [tab, setTab] = useState<Tab>("overview");
  const { summary, meta } = report;
  const assessed = summary.readiness_score !== null;

  const tabs: { id: Tab; label: string }[] = [
    { id: "overview", label: "Overview" },
    { id: "findings", label: `Findings (${report.findings.length})` },
    { id: "actions", label: "What to do" },
  ];

  return (
    <main className="report">
      <header className="report-head">
        <div>
          <h1>{report.site}</h1>
          <p className="report-meta">
            Audited {new Date(report.audited_at).toLocaleDateString(undefined, { dateStyle: "long" })}.{" "}
            {meta.crawl.pages_crawled} {meta.crawl.pages_crawled === 1 ? "page" : "pages"} read,{" "}
            {meta.crawl.pages_rendered} rendered.
          </p>
        </div>
        <div className={`score${assessed ? "" : " score-absent"}`}>
          <span className="score-value">{scoreText(summary.readiness_score)}</span>
          <span className="score-label">
            {assessed ? "Readiness, out of 100" : "No page could be read, so there is no score"}
          </span>
        </div>
      </header>

      <p className="headline">{summary.headline}</p>

      <div className="tabs" role="tablist" aria-label="Report sections">
        {tabs.map(({ id, label }) => (
          <button
            key={id}
            role="tab"
            id={`tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`panel-${id}`}
            className="tab"
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>

      <section role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="panel">
        {tab === "overview" && <Overview report={report} />}
        {tab === "findings" && <Findings report={report} />}
        {tab === "actions" && <Actions report={report} />}
      </section>

      <footer className="report-foot">
        Ontology {meta.coverage.ontology_version}, {meta.coverage.overall_coverage_pct}% of checks
        could run on this audit. {meta.crawl.stopped_early && meta.crawl.note}
      </footer>
    </main>
  );
}

function Overview({ report }: { report: ReportData }) {
  return (
    <>
      <div className="axes">
        {AXES.map((axis) => (
          <AxisRow
            key={axis.key}
            label={axis.label}
            question={axis.question}
            score={report.summary.by_axis[axis.key]}
            findings={report.findings.filter((f) => f.axis === axis.key)}
          />
        ))}
      </div>
      <Limitations report={report} />
    </>
  );
}

function AxisRow({
  label,
  question,
  score,
  findings,
}: {
  label: string;
  question: string;
  score: AxisScore;
  findings: Finding[];
}) {
  const { modes_possible: possible } = score.evidence_basis;
  const fired = [...findings].sort(
    (a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity),
  );
  const assessed = score.score !== null;
  const passed = Math.max(possible - fired.length, 0);

  return (
    <div className="axis">
      <div className="axis-text">
        <h2>{label}</h2>
        <p>{question}</p>
      </div>
      <div className="axis-score">{scoreText(score.score)}</div>
      <div
        className={`strip${assessed ? "" : " strip-absent"}`}
        role="img"
        aria-label={
          assessed
            ? `${fired.length} of ${possible} checks found a problem`
            : `${possible} checks, none could be evaluated`
        }
      >
        {assessed && fired.map((f) => <span key={f.id} className={`cell cell-${f.severity}`} title={f.title} />)}
        {Array.from({ length: assessed ? passed : possible }, (_, i) => (
          <span key={i} className="cell" />
        ))}
      </div>
      <p className="axis-caption">
        {assessed
          ? fired.length === 0
            ? `All ${possible} checks passed`
            : `${fired.length} of ${possible} checks found a problem`
          : "Not assessed"}
      </p>
    </div>
  );
}

function Limitations({ report }: { report: ReportData }) {
  if (report.audit_limitations.length === 0) return null;
  return (
    <section className="limitations" aria-labelledby="limits-title">
      <h2 id="limits-title">What this audit couldn't check</h2>
      <p className="muted">These describe the audit, not the site, and never affect the score.</p>
      {report.audit_limitations.map((limit) => (
        <div key={limit.title} className="limitation">
          <h3>{limit.title}</h3>
          <p>{limit.detail}</p>
          {limit.resolution && <p className="muted">{limit.resolution}</p>}
        </div>
      ))}
    </section>
  );
}

function Findings({ report }: { report: ReportData }) {
  if (report.findings.length === 0) {
    return (
      <p className="empty">
        {report.summary.readiness_score === null
          ? "No findings, because no page could be read. See the overview for why."
          : "No problems were found on the pages this audit read."}
      </p>
    );
  }
  return (
    <ol className="findings">
      {report.findings.map((f) => (
        <li key={f.id} className={`finding sev-${f.severity}`}>
          <div className="finding-head">
            <span className="severity">{SEVERITY_LABEL[f.severity]}</span>
            <h3>{f.title}</h3>
          </div>
          <p className="evidence">{f.evidence}</p>
          <p className="finding-tags">
            {AXES.find((a) => a.key === f.axis)?.label}, {f.confidence} confidence
          </p>
          {f.affected_urls.length > 0 && (
            <details>
              <summary>
                {f.affected_urls.length} affected {f.affected_urls.length === 1 ? "page" : "pages"}
              </summary>
              <ul className="urls">
                {f.affected_urls.map((url) => (
                  <li key={url}>
                    <a href={url} target="_blank" rel="noreferrer">
                      {url.replace(/^https?:\/\//, "")}
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </li>
      ))}
    </ol>
  );
}

function Actions({ report }: { report: ReportData }) {
  return (
    <>
      {report.findings.length > 0 && (
        <section className="actions">
          <h2>Fixes for what this audit found</h2>
          <ol>
            {report.findings.map((f) => (
              <li key={f.id} className={`finding sev-${f.severity}`}>
                <div className="finding-head">
                  <span className="severity">{SEVERITY_LABEL[f.suggested_action.priority]}</span>
                  <h3>{f.title}</h3>
                </div>
                <p>{f.suggested_action.summary}</p>
              </li>
            ))}
          </ol>
        </section>
      )}
      {report.opportunities.length > 0 && (
        <section className="actions">
          <h2>Standing advice</h2>
          <p className="muted">
            Recommended for most sites whatever this audit found. Not derived from evidence on this site.
          </p>
          <ol>
            {report.opportunities.map((o) => (
              <li key={o.id} className="advice">
                <p>{o.summary}</p>
                {o.rationale && <p className="muted">{o.rationale}</p>}
              </li>
            ))}
          </ol>
        </section>
      )}
    </>
  );
}
