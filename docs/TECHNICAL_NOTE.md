# Technical note - Atlas Scholarship Intelligence Crawler

**Stack:** Python 3.9, requests + BeautifulSoup/lxml, pypdf, SQLite, FastAPI + a single-page dashboard. No LLM, no paid API.

## Architecture
`config/sources.json` (seed hubs + official-domain registry) → `fetch.py` (robots.txt, per-host delay, retries, snapshot cache) → `classify.py` → `extract.py` → `verify.py` → `crawl.py` (SQLite upsert, change/stale detection) → `app.py`/`web/index.html`.
Tables: `runs, sources, leads, scholarships, evidence, score_breakdown, changes`.

## Discovery
Seeds are *hubs*, not scholarships. `nsp_table` hubs (National Scholarship Portal scheme list) use a structured parser; `generic` hubs are crawled by keyword-guided link following (depth ≤ 2, same registrable domain, official destinations only); detail pages are kept if a page-score (funding word in title, amount, eligibility words, apply/deadline words, length) ≥ 3, then grouped into programmes with companion pages (eligibility, timeline). `aggregator` hubs only create *leads*; nothing is verified from them. Limitation: some generic discoveries are umbrella pages (e.g. a university "Scholarships" page) and score low by design.

## Source classification
Rule-based: aggregator blocklist → organisation registry (domain → org, type) → gov TLDs (.gov.in/.nic.in) → academic TLDs (.ac.in/.edu.in/.edu) → `unknown`. Only official types can pass 70%.

## Extraction
Deterministic regex/section rules over HTML and PDF text. Each field = `(value, verbatim quote, source URL, char offset, method)`. If no span supports a field it is "Not specified" (never guessed). Dates come from listing cards or date literals near deadline phrases; "offer/interview/reference" dates are excluded; "applications closed" and month-only deadlines are stored as such.

## Verification & confidence
`confidence = Σ weight·score − penalties`, then caps. Weights: official source 20, present on source 15, application URL (official, linked, resolves) 10, eligibility 15, deadline 15, amount 8, freshness 7, cross-view consistency 5, traceability 5. Each score is computed from evidence (domain rule, HTTP 200, name-token coverage, quote presence/structured criteria count, date arithmetic, agreement between listing and guideline PDF). Penalties: conflicting dates (−10 each), guideline PDF shared by several schemes (−8), scanned/OCR PDF (−12). Caps: aggregator ≤ 60, non-official ≤ 70, unreachable ≤ 50. VERIFIED ⇔ ≥ 95. The breakdown is stored per record and shown as "Why this score?".

## Anti-hallucination
(1) Extractors only slice source text. (2) `quote_ok` re-checks every quote against the snapshot and checks numbers in the value occur in the quote; failures lower traceability. (3) Absent fields stay "Not specified". (4) Scanned PDFs are detected (page-sized raster images) and penalised because OCR errors corrupt numbers (we saw "Rs. 12000/-" read as 120001). Trace path: scholarship → `official_url` → `evidence.quote` + `source_url` → `value`.

## Change & stale detection
Each run compares new extracted values with stored ones per field and writes `changes(old, new, old/new quote, source, detected_at, run_id, simulated)`; records are updated, history is kept. A scholarship absent from a successfully fetched hub becomes `NO_LONGER_VERIFIABLE` (REMOVED_FROM_SOURCE). Status is derived from evidence: ACTIVE, EXPIRING_SOON (≤30d), UPCOMING, EXPIRED (date passed or "applications closed"), REVIEW_REQUIRED (no deadline evidence).

## Results (this repo's `data/atlas.db`)
58 records; 13 VERIFIED (all ≥ 95%, government/NSP); 45 REVIEW_REQUIRED; 5 source types; 9 EXPIRED (real, e.g. NSP schemes closed 31-10-2026, Inlaks/Commonwealth/Chevening cycles). Honest gaps: non-government pages rarely state amount + exact deadline + application link together, so none reach 95%; the "15+ verified" target is not met (13). Change-detection demo (run 2) uses **simulated** edits to a copy of the snapshots (`demo/simulate_changes.py`): 2 NSP deadline changes, 1 Tata Trusts deadline change, 1 removal; run 3 reverts them. Runs 1-3 are replays of pages crawled live on 2026-11-02 (the live crawl takes ~25 min).
