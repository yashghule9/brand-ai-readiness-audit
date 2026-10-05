import { useEffect, useState } from "react";
import type { Representation as RepresentationData, VerifiedFact } from "./types";

const FACT_LABEL: Record<string, string> = {
  official_domain: "Official domain",
  brand_name: "Brand name",
  legal_name: "Legal name",
  location: "Location",
  products: "Products",
  operator: "Operated by",
};

const FACT_STATUS: Record<VerifiedFact["status"], string> = {
  verified: "Verified",
  heuristic: "Inferred",
  unavailable: "Not available",
};

/** Phase 3. Shows what the audit verified, then what an assistant says about
 *  the same things. With no assistant connected every question is plainly
 *  unasked — the layout doesn't change when one is, only the answers do. */
export default function Representation({ load }: { load: () => Promise<RepresentationData> }) {
  const [data, setData] = useState<RepresentationData | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    load().then(setData, () => setFailed(true));
  }, [load]);

  if (failed) return <p className="empty">The assistant comparison isn't available for this audit.</p>;
  if (!data) return <p className="waiting">Loading.</p>;

  const asked = data.summary.questions_evaluated;

  return (
    <div className="representation">
      <section aria-labelledby="facts-title">
        <h2 id="facts-title">What the audit verified</h2>
        <p className="muted">Facts read from the site itself. Assistant answers are checked against these.</p>
        <dl className="facts">
          {Object.entries(data.verified_facts).map(([key, fact]) => (
            <div key={key} className={`fact fact-${fact.status}`}>
              <dt>{FACT_LABEL[key] ?? key}</dt>
              <dd>
                <span className="fact-value">{formatValue(fact.value)}</span>
                <span className="fact-status">{FACT_STATUS[fact.status]}</span>
              </dd>
            </div>
          ))}
        </dl>
      </section>

      <section aria-labelledby="answers-title" className="answers-section">
        <h2 id="answers-title">What assistants say</h2>
        <p className="muted">
          {asked === 0
            ? "No assistant is connected to this server, so these questions haven't been asked. When one is, each answer is compared with the facts above."
            : `${asked} of ${data.summary.questions_asked} questions answered: ${data.summary.consistent} consistent with the site, ${data.summary.discrepancies} not.`}
        </p>
        <ol className="answers">
          {data.questions.map((q) => (
            <li key={q.id}>
              <p className="question">{q.question}</p>
              {q.status === "ok" && q.answer ? (
                <>
                  <p className="answer">{q.answer}</p>
                  <p className="muted">{q.observation.notes}</p>
                </>
              ) : (
                <p className="unasked">{q.status === "error" ? "The assistant didn't answer" : "Not asked"}</p>
              )}
            </li>
          ))}
        </ol>
      </section>

      <p className="report-foot">{data.disclaimer}</p>
    </div>
  );
}

function formatValue(value: VerifiedFact["value"]) {
  if (value === null || value === "" || (Array.isArray(value) && value.length === 0)) return "None found";
  return Array.isArray(value) ? value.join(", ") : value;
}
