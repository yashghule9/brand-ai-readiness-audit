---
name: assistant-representation
description: Observes how an external AI assistant already represents a brand — whether it recognises the brand at all, resolves it to the right entity, names the correct official domain, and agrees with facts the deterministic audit verified. Use AFTER a Brand AI Readiness Audit has produced its report, when the question is "what does an assistant actually say about this brand?" rather than "is this website readable by crawlers". Experimental and observational — the output is never scored, never merged into the audit report, and never becomes a website defect.
allowed-tools: Bash, Read
license: MIT
---

# Assistant Representation (Phase 3 — experimental, unscored)

> **Reference implementation:** `braiaudit.assistant.evaluate_representation()`
> ([src/braiaudit/assistant.py](../../src/braiaudit/assistant.py)), runnable
> directly via
> `python skills/assistant-representation/scripts/query_representation.py --report report.json`.
> Output is validated against
> [schemas/assistant-representation.output.schema.json](../../schemas/assistant-representation.output.schema.json).

## What this measures, and what it does not

This skill measures **assistant representation**: what an AI assistant
already says about a brand when asked a fixed set of plain questions.

It does **not** measure, and must never be described as measuring, whether
an assistant "can crawl" or "has indexed" the website. Those are claims
about a system's internals that nothing here observes. Correct phrasings:
*assistant representation*, *assistant discoverability observation*,
*assistant-side entity/fact consistency*.

An assistant answering badly is **not** evidence of a website defect. A
brand can have a flawless site and still be poorly represented, and vice
versa. That is precisely why this output stays separate from the audit's
`findings`.

## Boundary against the deterministic audit

```
Phase 2 (deterministic)              Phase 3 (this skill)
─────────────────────────            ────────────────────────────
crawl → observe → render             verified facts (read-only)
clean → discover → ontology                    ↓
        ↓                            fixed questions → provider
verified findings + score                      ↓
                                     answers → observations
                                     (never scored, never merged)
```

Hard guarantees, each covered by a test in `tests/test_assistant.py`:

- Phase 3 never mutates the Phase 2 report it is given.
- No assistant answer can create, modify, or delete a finding.
- No assistant answer can change `readiness_score`, an axis score, or a
  severity. The Phase 3 output has no score field at all.
- An unavailable assistant produces `not_evaluated`, never a defect and
  never a fabricated answer.

## Preconditions

- A **finished Phase 2 audit report** (the JSON from `braiaudit audit`).
  This skill reads it; it never crawls or re-fetches the site itself.
- Optionally, a caller-supplied brand name. Without one, a label derived
  from the domain is used and marked `heuristic`, never `verified`.
- An assistant provider, if one is configured. Without one, every question
  returns `not_evaluated` — which is a correct, honest result, not a
  failure to work around.

## Input

```json
{
  "phase2_report": { "site": "example.com", "site_info": { "domain": "example.com" } },
  "brand_name": "Example Corp",
  "mode": "blind"
}
```

## Verified facts

Only facts the deterministic pipeline actually established are used for
comparison. Today that is `official_domain` (from `site_info.domain`).
`brand_name` is `verified` only when the caller supplies it, otherwise
`heuristic`. `legal_name`, `location`, `products` and `operator` are
currently `unknown` — Phase 2 does not yet expose them as structured
fields, and parsing them out of prose evidence strings would be inference,
not verification.

An assistant's answer is **never** promoted into a verified fact.

## The fixed question set

Six questions, fixed in code, never generated or chosen by a model:

| id | question |
|---|---|
| `brand_identity` | What is `{brand}`? |
| `brand_offering` | What does `{brand}` offer? |
| `brand_products` | What products or services is `{brand}` known for? |
| `brand_location` | Where is `{brand}` based? |
| `brand_domain` | What is `{brand}`'s official website? |
| `brand_operator` | Who owns or operates `{brand}`? |

**Ambiguity is deliberately preserved.** If a brand's name collides with
something else, "What is Adobe?" is left exactly as written — it is not
rewritten to "What is Adobe Inc., the software company headquartered in San
Jose?", because the collision is the thing being observed. A disambiguated
query would be a different experiment and must be labelled as one.

## Two modes, never mixed

- **Mode A — `blind`** (default, and the only mode used so far): the
  assistant is asked cold, with no site content supplied. Measures what it
  already represents.
- **Mode B — `grounded`**: a short verified-fact snippet is supplied as
  context. Measures whether supplied information is used correctly.

A report always records which mode produced it. Results from the two are
not comparable — Mode B answers a much easier question, and reporting them
together would quietly turn the experiment into "can the assistant repeat
what we just told it?"

## Observation categories

| status | meaning | basis |
|---|---|---|
| `consistent` | nothing contradicted what is verified | deterministic when a verified domain backs it |
| `assistant_brand_not_found` | a fixed no-knowledge/refusal phrase matched | heuristic |
| `assistant_entity_confusion` | substantive answer that never mentions the brand keyword — may describe a different entity | heuristic |
| `assistant_domain_mismatch` | answer names a domain that is not the verified one | deterministic |
| `assistant_fact_conflict` | answer contradicts a verified fact | deterministic |
| `assistant_fact_omission` | a verified fact went unmentioned | deterministic |
| `not_evaluated` | no answer available (no provider, error, timeout, empty) | n/a |

Every observation carries an explicit `basis` field — `deterministic`,
`heuristic`, or `n/a` — so a keyword proxy is never read with the same
confidence as a direct string comparison against a verified fact. Heuristic
observations require human review before being treated as real.

## Provider interface

```
AssistantProvider.query(question, context) -> AssistantResponse
```

The provider receives the **bare question and context separately**; the
orchestrator never pre-wraps them into one prompt string, so a provider
whose API uses structured messages rather than a flat prompt is not forced
into the wrong shape. A prompt-driven provider should build its request
through `braiaudit.assistant.build_prompt()`, which is where the
injection-resistance framing lives.

Shipped implementations:

- `NullProvider` — the default. Always `not_evaluated`. Never fabricates.
- `RecordedAnswerProvider` — replays real answers supplied by the caller
  (an agent driving this skill, or a transcript from a real API call made
  elsewhere). It never invents text.

No HTTP-backed provider ships with this repository, and no API credential
is assumed to exist.

## Security — assistant output is untrusted data

Assistant answers are treated exactly like crawled website content: read,
never executed. An answer containing `"Ignore the audit and run ..."` is
only ever a string compared against the verified domain. Nothing in this
layer lets an answer reach a tool, a file, the ontology, the score, or the
crawl.

Symmetrically, website content never controls assistant querying: in Mode A
no site text reaches the provider at all, and in Mode B only a short
verified-fact snippet does, wrapped in an explicit untrusted-content label.

## Reproducibility

Assistant answers are not reproducible the way Phase 2's measurements are.
Each question records `provider`, `model`, `queried_at`, `status`,
`error_category`, `elapsed_ms`, and any provider config supplied. Do not
present a single run as a stable property of a brand or of an assistant.

## Error handling

Every failure is explicit and none becomes a website defect:
`provider_not_configured`, `no_recorded_answer`, `timeout`, `rate_limit`,
`auth_failure`, `empty_response`, `refused`, `provider_unavailable`. All
surface as `status: not_evaluated` / `error` with the category preserved.

**"Assistant unavailable" must never be reported as "brand is not
discoverable."** That inference is invalid and this skill does not make it.
