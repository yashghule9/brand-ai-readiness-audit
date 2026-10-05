import { scoreText } from "./format";
import type { Report as ReportData } from "./types";

// Placeholder until M3 builds the full report view.
export default function Report({ report }: { report: ReportData }) {
  return (
    <main>
      <h1>{report.site}</h1>
      <p>{scoreText(report.summary.readiness_score)}</p>
      <p>{report.summary.headline}</p>
    </main>
  );
}
