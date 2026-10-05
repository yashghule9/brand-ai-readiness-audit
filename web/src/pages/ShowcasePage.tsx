import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router";
import { getShowcase, getShowcaseRepresentation } from "../api";
import Report from "../Report";
import type { Report as ReportData } from "../types";

export default function ShowcasePage() {
  const { slug = "" } = useParams();
  const [report, setReport] = useState<ReportData | null>(null);
  const [missing, setMissing] = useState(false);
  const loadRepresentation = useCallback(() => getShowcaseRepresentation(slug), [slug]);

  useEffect(() => {
    getShowcase(slug).then(setReport, () => setMissing(true));
  }, [slug]);

  if (missing) {
    return (
      <p>
        There's no sample audit for {slug}. <Link to="/">Run one yourself</Link>.
      </p>
    );
  }
  return report ? <Report report={report} loadRepresentation={loadRepresentation} /> : <p className="waiting">Loading the sample audit.</p>;
}
