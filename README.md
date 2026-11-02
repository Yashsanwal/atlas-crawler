# Atlas Scholarship Intelligence Crawler

Discover → Classify → Crawl → Extract → Verify → Score → Store → Update, for scholarships available to Indian students.

## Quick start
```bash
pip3 install -r requirements.txt
python3 -m atlas.crawl                 # live run (fills data/atlas.db, caches pages in data/snapshots)
python3 -m atlas.crawl                 # run again: unchanged / changed / removed are detected
uvicorn atlas.app:app --port 8000      # dashboard at http://localhost:8000
sqlite3 data/atlas.db "select name,status,confidence from scholarships"
```
Change/stale demo (see `demo/simulate_changes.py`): `python3 demo/simulate_changes.py && python3 -m atlas.crawl --mode simulated --snap data/snapshots_sim --offline --note "simulated source edits"`.
Simulated runs are labelled `mode=simulated` and flagged in the UI; the real data is never edited.

## Layout
`config/sources.json` seed hubs + official-domain registry · `atlas/fetch.py` polite fetch, robots, snapshot cache · `atlas/classify.py` rule-based source classification · `atlas/extract.py` evidence-first extraction · `atlas/verify.py` confidence engine · `atlas/crawl.py` orchestrator, storage, change/stale detection · `atlas/app.py` + `web/index.html` dashboard · `docs/TECHNICAL_NOTE.md`.

## Key guarantees
* Every stored field has a verbatim `quote` + `source_url`; the verifier re-checks the quote against the snapshot. No quote → "Not specified".
* Official-ness is decided by TLD rules + an explicit registry, never by an LLM. Aggregators are lead sources only (capped at 60%).
* Confidence = weighted sum of nine evidence checks minus penalties (see `atlas/verify.py`, and "Why this score?" in the UI). VERIFIED needs ≥ 95.
* Scanned/OCR PDFs and PDFs shared by several schemes are penalised, so they land in REVIEW_REQUIRED.
* No LLM or paid API is used anywhere.

## Results & honesty notes
58 records, 13 VERIFIED (≥95%), all from the National Scholarship Portal + its official guideline PDFs; foundation/CSR/international/university records are present but REVIEW_REQUIRED because their pages lack one of: exact deadline, amount, application link. Runs 1 and 3 replay snapshots from a live crawl on 2026-11-02; run 2 uses **simulated** source edits (clearly flagged). Re-run live with `python3 -m atlas.crawl` (needs network, ~25 min). Scanned-PDF schemes (ICAR, NMMSS, etc.) are intentionally capped below 95%.
