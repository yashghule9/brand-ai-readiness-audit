// The audit report contract, mirrored from schemas/audit-report.schema.json.
// Only the fields the UI reads are typed. `readiness_score` and each axis
// `score` are `number | null` on purpose: null means the audit obtained no
// evidence to score from, and the compiler forces every view to handle it.

export type Severity = "critical" | "high" | "medium" | "low";
export type Axis = "visibility" | "staleness" | "engagement" | "identity";

export interface AxisScore {
  score: number | null;
  findings: number;
  evidence_basis: { modes_fired: number; modes_possible: number };
}

export interface Finding {
  id: string;
  title: string;
  severity: Severity;
  evidence: string;
  suggested_action: { summary: string; priority: Severity };
  affected_urls: string[];
  category: string;
  axis: Axis;
  confidence: "high" | "medium" | "low";
  signal_type: string;
  parse_status: string;
}

export interface Limitation {
  title: string;
  detail: string;
  resolution?: string;
  affected_urls?: string[];
}

export interface Opportunity {
  id: string;
  priority: Severity;
  axis?: Axis;
  summary: string;
  rationale?: string;
}

export interface Report {
  schema_version: string;
  site: string;
  audited_at: string;
  site_info: {
    domain: string;
    url: string;
    brand_name_candidates: string[];
    declared_brand_name: string;
    organization_legal_name: string;
    organization_same_as: string[];
  };
  summary: {
    total_findings: number;
    critical: number;
    high: number;
    medium: number;
    readiness_score: number | null;
    by_axis: Record<Axis, AxisScore>;
    headline: string;
    top_priorities: { ref: string; priority: Severity; summary: string; type: string }[];
  };
  findings: Finding[];
  audit_limitations: Limitation[];
  opportunities: Opportunity[];
  meta: {
    coverage: { ontology_version: string; overall_coverage_pct: number; skills_engaged: string[] };
    crawl: {
      pages_crawled: number;
      pages_unreachable: string[];
      stopped_early: boolean;
      note: string;
      render_triggered: boolean;
      pages_rendered: number;
    };
  };
}

export type JobStatus = "queued" | "running" | "done" | "failed";

export interface Job {
  id: string;
  domain: string;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  error: string | null;
  report: Report | null;
}

// One snapshot per crawled URL, from run_audit(on_page=...).
export interface PageEvent {
  type: "page";
  url: string;
  depth: number;
  outcome: "analysed" | "blocked" | "unreachable";
  pages_crawled: number;
  pages_analysed: number;
  pages_rendered: number;
  queued: number;
  skills_engaged: string[];
}

export type AuditEvent =
  | { type: "status"; status: JobStatus }
  | PageEvent
  | { type: "done" }
  | { type: "failed"; error: string | null };
