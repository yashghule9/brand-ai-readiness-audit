// Sample audits served as static JSON from public/showcase/, so the home
// page has something real to show even when the audit server isn't running.
// Regenerate with the CLI:  braiaudit audit <domain> --output web/public/showcase/<domain>.json
export const SHOWCASE: { slug: string; note: string }[] = [
  { slug: "website.grader.com", note: "HubSpot's own Website Grader, audited" },
  { slug: "basecamp.com", note: "A full fifteen-page crawl of a mostly static site" },
  { slug: "bbc.co.uk", note: "Blocks AI crawlers in robots.txt while allowing search engines" },
  { slug: "developer.mozilla.org", note: "A large documentation site" },
  { slug: "lego.com", note: "Serves a bot challenge, so nothing could be assessed" },
];
