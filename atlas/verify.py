"""Verification engine: a deterministic, evidence-based confidence score.

confidence = sum(weight_i * score_i) - penalties, then hard caps. Every score_i is computed from
inspectable evidence (HTTP result, domain classification, verbatim quote presence, cross-document agreement).
No LLM output is ever used as a number. Breakdown rows are persisted so the UI can answer "Why this score?".
"""
import re, datetime as dt
from .extract import NS, find_dates

WEIGHTS = [
    ("source_official", 20, "Is the source an official/primary one (gov TLD, academic TLD or registry-listed provider domain)?"),
    ("present_on_source", 15, "Is the scholarship currently present on the fetched official page (HTTP 200, name found)?"),
    ("application_url", 10, "Is there an application URL, on an official domain, that is linked from the source and resolves?"),
    ("eligibility_support", 15, "Is the eligibility text, and structured criteria, backed by verbatim quotes?"),
    ("deadline_support", 15, "Is the closing date (or explicit rolling-admission statement) backed by a verbatim quote?"),
    ("amount_support", 8, "Is the amount / benefit backed by a verbatim quote?"),
    ("freshness", 7, "Is the information current (dates in the current/next cycle; source fetched now)?"),
    ("consistency", 5, "Do independent extraction views agree (listing vs guideline document; no conflicting dates)?"),
    ("traceability", 5, "Do ALL stored field values trace to a quote that exists verbatim in the snapshot?"),
]
THRESHOLD = 95.0
CORE = ["education_level", "income", "age", "academic", "category", "gender", "domicile", "institution"]


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def name_tokens(n):
    stop = {"the", "of", "for", "and", "scheme", "scholarship", "scholarships", "to", "a", "in", "based", "merit", "welfare", "students", "student", "programme", "program", "fellowship"}
    return {t for t in re.findall(r"[a-z0-9]+", norm(n)) if t not in stop and len(t) > 1}


def garble_ratio(text):
    """Share of word-like tokens that look like OCR noise (backslashes, digit/letter mixes inside words,
    lower->UPPER flips, 4+ identical letters). Scanned-PDF text with a high ratio cannot support exact claims."""
    toks = [t for t in text.split() if len(t) >= 4][:6000]
    if not toks:
        return 1.0
    bad = 0
    for t in toks:
        if "\\" in t or re.search(r"[a-z][A-Z]{1}[a-z]", t) and not re.match(r"^[A-Z][a-z]+(?:[A-Z][a-z]+)+$", t) \
                or re.search(r"[A-Za-z]{2}\d[A-Za-z]{2}|\d[A-Za-z]{3,}\d", t) or re.search(r"([a-z])\1{3,}", t) or re.search(r"[|~^{}]", t):
            bad += 1
    return bad / len(toks)


def quote_ok(ev, texts):
    """The quote must occur verbatim (whitespace-normalised) in the snapshot text of its source URL,
    and any digits in the stored value must also occur inside the quote (value is supported by quote)."""
    t = texts.get(ev["url"])
    if not t:
        return False
    q = norm(ev["quote"])
    if not q or q not in norm(t):
        return False
    if ev["field"] in ("closing_date", "opening_date"):
        return True  # ISO-normalised from a date literal found in quote; checked separately
    digits = re.findall(r"\d[\d,]*", re.sub(r"[₹≤≥–]", " ", ev["value"]))
    for d in digits:
        raw = d.replace(",", "")
        if raw and raw not in re.sub(r"[,\s]", "", ev["quote"]) and not _lakh_ok(raw, ev["quote"]):
            return False
    return True


def _lakh_ok(raw, quote):
    # ₹8,00,000 derived from "Rs. 8 lakh": accept if quote mentions lakh/crore
    return bool(re.search(r"lakh|lac|crore", quote, re.I))


def date_ok(ev):
    try:
        d = dt.date.fromisoformat(ev["value"])
    except Exception:
        return False
    return any(x[0] == d for x in find_dates(ev["quote"]))


def score(rec, fields, texts, ctx, today):
    """rec: dict(name,...); fields: {field: ev}; texts: {url: text}; ctx: dict(official, source_type, page_ok,
    app_status, name_in_source, agree, conflicts, shared_doc, aggregator_only)."""
    rows = []

    def add(name, s, detail):
        w = next(w for n, w, _ in WEIGHTS if n == name)
        rows.append({"check": name, "weight": w, "score": round(max(0.0, min(1.0, s)), 3), "points": round(w * max(0.0, min(1.0, s)), 2), "detail": detail})

    # 1 official
    st = ctx["source_type"]
    if ctx["official"]:
        add("source_official", 1.0, f"{st}: {ctx['rule']}")
    elif st == "unknown":
        add("source_official", 0.3, "domain not in registry / no gov-academic TLD")
    else:
        add("source_official", 0.0, f"{st} source - discovery only")

    # 2 presence
    if ctx["page_ok"]:
        c = ctx["name_cov"]
        add("present_on_source", 1.0 if c >= 0.8 else 0.7 if c >= 0.5 else 0.3,
            f"HTTP 200; {int(c*100)}% of name tokens found in {ctx['presence_where']}")
    else:
        add("present_on_source", 0.0, "official page not reachable")

    # 3 application url
    a = ctx.get("app_status")
    if rec.get("application_url") and ctx.get("app_official") and a == "ok":
        add("application_url", 1.0, f"{rec['application_url']} - official domain, linked from source, resolves")
    elif rec.get("application_url") and ctx.get("app_official"):
        add("application_url", 0.6, "official-domain link found but did not resolve (%s)" % a)
    elif rec.get("application_url"):
        add("application_url", 0.3, "application link is on a non-official domain")
    else:
        add("application_url", 0.0, "no application URL found on source")

    # 4 eligibility
    e = fields.get("eligibility")
    core = [k for k in CORE if k in fields and fields[k]["_ok"]]
    if e and e["_ok"]:
        add("eligibility_support", 0.6 + 0.4 * min(len(core) / 2.0, 1.0), f"eligibility quote verified; {len(core)} structured criteria evidenced: {', '.join(core) or 'none'}")
    elif core:
        add("eligibility_support", 0.3 * min(len(core), 2) / 2, f"no eligibility section; {len(core)} criteria evidenced")
    else:
        add("eligibility_support", 0.0, "no eligibility text found on official source")

    # 5 deadline
    c = fields.get("closing_date")
    if c and c["_ok"]:
        cv = c["value"]
        add("deadline_support", 1.0 if cv[0].isdigit() else 0.8 if cv.startswith("Rolling") else 0.7,
            f"closing info '{cv}' quoted: \"{c['quote'][:90]}\"")
    else:
        add("deadline_support", 0.0, "no closing date or rolling statement found on source")

    # 6 amount
    am = fields.get("amount")
    add("amount_support", 1.0 if am and am["_ok"] else 0.0, "benefit amount verified by quote" if am and am["_ok"] else "no amount/benefit statement found")

    # 7 freshness
    fr, why = 0.4, "no cycle indicator"
    cd = None
    if c and c["_ok"] and c["value"][:1].isdigit():
        cd = dt.date.fromisoformat(c["value"])
    if cd:
        if cd >= today:
            fr, why = 1.0, f"closing date {cd} is in the future"
        elif (today - cd).days <= 120:
            fr, why = 0.9, f"closing date {cd} passed {(today-cd).days}d ago (record current, cycle closed)"
        else:
            fr, why = 0.4, f"closing date {cd} is more than 120 days old"
    elif c and c["_ok"] and c["value"].startswith("Month:"):
        y, m = map(int, c["value"][7:].split("-"))
        fr, why = (1.0, "month-level deadline in the future") if (y, m) >= (today.year, today.month) else (0.6, "month-level deadline has passed")
    elif c and c["_ok"] and c["value"].startswith("Applications closed"):
        fr, why = 0.8, "source states the cycle is closed (current statement, fetched now)"
    elif c and c["_ok"]:
        fr, why = 0.9, "rolling admission statement on page fetched today"
    elif re.search(r"%d|%d" % (today.year, today.year + 1), " ".join(texts.get(u, "")[:3000] for u in texts)):
        fr, why = 0.6, "current-year reference on page but no deadline"
    if not ctx["page_ok"]:
        fr, why = 0.0, "source not fetched"
    add("freshness", fr, why)

    # 8 consistency
    cons = ctx["agree"]
    add("consistency", cons, ctx["agree_detail"])

    # 9 traceability
    total = [f for f in fields.values()]
    ok = [f for f in total if f["_ok"]]
    add("traceability", (len(ok) / len(total)) if total else 0.0, f"{len(ok)}/{len(total)} stored values trace to a verbatim quote in the snapshot")

    raw = sum(r["points"] for r in rows)
    pen, notes = 0.0, []
    if ctx.get("conflicts"):
        pen += 10 * len(ctx["conflicts"])
        notes.append("conflicting values: " + "; ".join(ctx["conflicts"]))
    if ctx.get("garble", 0) > 0.03:
        pen += 12
        notes.append("source is a scanned PDF / OCR-quality text; exact figures (amounts, counts, dates) cannot be trusted without manual review")
    if ctx.get("shared_doc"):
        pen += 8
        notes.append("guideline document is shared by several schemes; per-scheme attribution is ambiguous")
    final = raw - pen
    cap, capwhy = 100.0, None
    if not ctx["official"]:
        cap, capwhy = (60.0 if ctx["source_type"] == "aggregator" else 70.0), "source is not official"
    elif not ctx["page_ok"]:
        cap, capwhy = 50.0, "official page not fetched"
    elif ctx["name_cov"] < 0.5:
        cap, capwhy = 80.0, "name not clearly present on source"
    if capwhy:
        notes.append(f"capped at {cap:.0f}: {capwhy}")
    final = round(min(final, cap), 1)
    final = max(final, 0.0)
    return {"confidence": final, "label": "VERIFIED" if final >= THRESHOLD else "REVIEW_REQUIRED",
            "breakdown": rows, "raw": round(raw, 2), "penalty": pen, "notes": notes}
