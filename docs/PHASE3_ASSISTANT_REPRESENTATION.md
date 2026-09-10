# Phase 3 — Assistant Representation (experimental, unscored)

Phase 3 measures something Phase 2 deliberately cannot: **what an external
AI assistant already says about a brand.** It is observational, it is not
scored, and it never becomes part of the website audit.

## The two phases answer different questions

| | Phase 2 | Phase 3 |
|---|---|---|
| Question | Is this website readable, current, coherent and attributable to a real entity? | What does an AI assistant already represent about this brand? |
| Method | Deterministic crawl, render, parse, ontology classification | Fixed questions put to an assistant provider, compared to verified facts |
| Output | `findings`, `audit_limitations`, `opportunities`, `readiness_score` | `assistant-representation` report — separate artifact, no score |
| Reproducible | Yes, where the live site is stable | **No** — provider-, model- and time-dependent |
| Authority | The source of verified website measurements | Never authoritative about the website or the world |

Phase 2's principle is unchanged: *producers observe, the ontology decides,
the assembler reports.* Phase 3 adds a second, separate one:

> The auditor measures the website.
> The assistant-query layer measures assistant representation.
> The two are compared, but remain separate.

## What this is not

Phase 3 does **not** show that an assistant "can" or "cannot crawl" a
website, nor that a site is or isn't "indexed". Nothing here observes any
assistant's internals. Use *assistant representation*, *assistant
discoverability observation*, or *assistant-side entity/fact consistency* —
never "GPT can crawl this site".

An assistant answering badly is **not a website defect**. A brand can have
a technically flawless site and be poorly represented, or the reverse.
That is exactly why the two outputs stay apart.

## Hard boundaries, each enforced by a test

- Phase 3 never mutates the Phase 2 report it reads.
- No assistant answer can create, change or delete a finding.
- No assistant answer can change `readiness_score`, an axis score, or a
  severity. The Phase 3 output contains no score field at all.
- An unavailable assistant yields `not_evaluated` — never a defect, never a
  fabricated answer.
- Assistant output is untrusted data: read and string-compared, never
  executed, and it cannot reach a tool, file, the ontology or the crawl.

## Verified facts, and what is honestly missing

Comparisons only ever run against facts the deterministic pipeline actually
established. Today that means:

| Fact | Status | Source |
|---|---|---|
| `official_domain` | **verified** | `site_info.domain` |
| `brand_name` | **verified** when the caller supplies one, or when the site declares one | caller, or `site_info.declared_brand_name` (Organization.name / og:site_name) |
| `brand_name` (fallback) | `heuristic` | domain-derived label, when the site declares no name |
| `legal_name` | **verified** when the site declares it | `site_info.organization_legal_name` |
| `location`, `products`, `operator` | `unavailable` | would need parsing Phase 2 does not perform |

Measured across the corpus, the practical surface is still thin — but for a
reason worth stating: of Tata, Zerodha, CRED, Zoho and ISRO, only Zoho
declares a brand name in its markup (`og:site_name`), and **none** declares
`legalName` or `sameAs`. That is not the pipeline being lazy; it is these
sites genuinely lacking Organization structured data, which Phase 2 already
reports independently via `MISSING_STRUCTURED_DATA` and
`UNCLAIMED_ENTITY_IDENTITY` findings.

The `unavailable` row is a genuine limitation, not an oversight. Location,
products and operator would require parsing Phase 2 does not perform
(`PostalAddress` sub-objects, product enumeration). Deriving them instead
from prose evidence strings on findings would be inference dressed up as
verification — exactly the fabricated-fact failure this design exists to
avoid — so they stay `unavailable`. Consequently `assistant_fact_conflict`
and `assistant_fact_omission` rarely fire, correctly, since little is
verified to compare against.

A Phase 2 corrective pass (commit `5d5958d`) already closed the part of this
gap that could be closed honestly: `declared_brand_name`,
`organization_legal_name` and `organization_same_as` were values the
observer had been reading and discarding, so surfacing them required no new
parsing. Page `<title>` was deliberately excluded from
`declared_brand_name` — real markup (zerodha.com) declares no
Organization.name or og:site_name, leaving only the title "Zerodha: Online
brokerage platform for stock trading & investing", which would make a worse
question than the domain-derived label.

## Observation categories

| status | basis | meaning |
|---|---|---|
| `consistent` | **deterministic** only when a verified value was actually compared and agreed; **heuristic** when no verified fact applied and the answer merely tripped no check | nothing contradicted what is verified |
| `assistant_domain_mismatch` | deterministic | a domain was named and it is not the verified one |
| `assistant_fact_conflict` | deterministic | contradicts a verified fact |
| `assistant_fact_omission` | deterministic | a verified fact went unmentioned |
| `assistant_brand_not_found` | heuristic | a fixed refusal/no-knowledge phrase matched |
| `assistant_entity_confusion` | heuristic | substantive answer never mentions the brand keyword |
| `not_evaluated` | n/a | no answer available |

Every observation carries `basis` explicitly. A keyword proxy must never be
read with the confidence of a direct string comparison — the two heuristic
categories flag candidates for human review, nothing more.

## Modes, kept separate

- **Mode A `blind`** (default, and the only mode used in validation so far):
  the assistant is asked cold. Measures what it already represents.
- **Mode B `grounded`**: a short verified-fact snippet is supplied.
  Measures whether given information is used correctly.

A report always records its mode. The two are not comparable, and reporting
them together would quietly reduce the experiment to "can the assistant
repeat what we just told it?"

## Reproducibility

Assistant answers are not reproducible the way Phase 2's measurements are.
Each question records `provider`, `model`, `queried_at`, `status`,
`error_category`, `elapsed_ms` and any supplied provider config. A single
run is a snapshot, not a stable property of a brand or of an assistant.

## Validation performed

### Live provider: not evaluated

**No live assistant provider was available.** This environment has no API
credential (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY` and peers all absent), no
provider SDK installed, and no provider configuration file. Per the
project's own rule, a missing provider is reported honestly rather than
worked around, so the live Mode A experiment is recorded as
**`not_evaluated`**, with `error_category: provider_not_configured` on all
30 questions across the five sites.

Artifacts: `tests/regression/phase3/*.not_evaluated.json`. Every one shows
6/6 `not_evaluated`, zero discrepancies, zero findings and no score change —
confirming that provider unavailability degrades to an explicit gap and
never to a website defect.

**Consequence: real provider latency remains unmeasured, and no
`Phase 2 + Phase 3 ≤ 300s` claim can be made.**

### Observation layer: exercised with agent-recorded answers

To exercise the comparison logic despite having no live provider, the five
sites were run against answers recorded from the authoring agent
(`claude-opus-5`), Mode A, with no site content supplied. These are real
answers from a real assistant, replayed through `RecordedAnswerProvider` —
**but this is not a live API call and carries no network latency**, so it
does not substitute for the live experiment above.

Artifacts: `tests/regression/phase3/*.agent_recorded.json`.

Result: 30/30 `consistent` — but that headline is close to meaningless on
its own, and the basis breakdown is the number that matters:

| | count |
|---|---|
| Classified on a **deterministic** basis (a verified value was compared and agreed) | **5** |
| Classified on a **heuristic** basis (brand keyword present, nothing verified applied) | **25** |
| Not evaluated | 0 |

Only the five `brand_domain` questions had a verified value to check
against. The other 25 passed because *no check could fire*, which is a far
weaker statement than "correctly represented" and is labelled as such.

### Negative control

Because a clean sweep proves little, the detector was run against a
deliberately faulty answer set for Zerodha. It correctly produced
`assistant_brand_not_found` (refusal phrasing), `assistant_entity_confusion`
(an off-topic answer about a noodle dish) and `assistant_domain_mismatch`
(`zerodha.org` against the verified `zerodha.com`, on a **deterministic**
basis), while leaving three sound answers `consistent`. The mechanism does
distinguish a clearly wrong representation from a correct one.

## Timing

Phase 3 timing must be reported separately from Phase 2's, because a live
assistant API can dominate total runtime.

| | |
|---|---|
| Phase 2 (10-site corpus, measured) | min 4.8s, max 168.2s, mean 50.2s, median 18.9s |
| Phase 3 comparison overhead (30 questions, 5 sites) | 32 ms total |
| Phase 3 provider latency | **not measured** |

The 32 ms covers only question construction and comparison. Because answers
were pre-recorded rather than fetched live, **no real provider latency is
included** — a live provider at even 2–5 s per call would add roughly
12–30 s per site for six questions, which is the figure that would actually
matter against the 300 s budget. Phase 2's own maximum (168.2 s) leaves
around 130 s of headroom, so a live Phase 3 pass would fit for most sites
but should be measured, not assumed, before any claim that
`Phase 2 + Phase 3 ≤ 300s` holds end to end.

## Known limitations

- **Thin verified-fact surface.** Only `official_domain` is verified, so
  conflict/omission checks are largely dormant. See above.
- **Heuristic entity confusion.** Keyword absence is a proxy, not a finding.
  A correct answer that never repeats the brand name would be flagged; a
  wrong-entity answer that happens to use the name would not.
- **Public-suffix handling is a fixed list.** `_registrable()` special-cases
  common multi-part suffixes (`.gov.in`, `.co.uk`, …) rather than using a
  public-suffix library. An unusual suffix outside the list can still
  collapse two different domains into a false match.
- **A discovered Phase 2 issue, deliberately not fixed here.**
  `corroborate._registrable()` has the same naive last-two-labels flaw that
  was fixed in Phase 3's copy: it reduces every `*.gov.in` to `gov.in`, so
  entity-reciprocity comparisons on multi-part-suffix domains can match
  incorrectly. Phase 2 is frozen and its behaviour was not changed as a side
  effect of Phase 3 work; this is recorded for a separate decision.
- **No live provider ships.** The interface supports one; nothing here calls
  an external API.
- **Single-assistant, single-run validation.** The results above describe one
  assistant at one point in time, not assistant behaviour in general.

## Not implemented in Phase 3

No RAG, no retrieval augmentation, no model-generated findings, no
assistant-derived ontology modes, and no contribution to any score. Whether
assistant observations should ever become scored audit concepts is a
separate decision that should follow empirical data, not precede it.
