"""Atlas scholarship crawler: Discover -> Classify -> Crawl -> Extract -> Verify -> Score -> Store -> Update.

Usage:
  python -m atlas.crawl                      # live run against the seed hubs
  python -m atlas.crawl --offline [--snap DIR]   # replay from cached snapshots
  python -m atlas.crawl --mode simulated --snap data/snapshots_sim --note "..."
"""
import argparse, datetime as dt, json, re, sys, time, urllib.parse, os
from bs4 import BeautifulSoup
from . import fetch as F
from .classify import classify, host
from .db import connect
from .extract import Doc, extract, NS, find_dates
from .verify import score, quote_ok, date_ok, norm, name_tokens, garble_ratio

TODAY = dt.date.today()
FIELD_COLS = {  # evidence field -> scholarships column
    "amount": "amount", "eligibility": "eligibility", "academic": "academic_requirements",
    "education_level": "education_level", "income": "income_criteria", "age": "age_criteria",
    "gender": "gender_criteria", "category": "category_criteria", "domicile": "domicile",
    "institution": "institution_requirements", "opening_date": "opening_date", "closing_date": "closing_date",
    "documents": "documents_required", "selection": "selection_process", "renewal": "renewal", "num_awards": "num_awards"}
TRACKED = list(FIELD_COLS.keys()) + ["application_url", "official_url"]
KW = re.compile(r"scholar|fellowship|bursar|stipend|financial aid|grant|loan scholarship|means|award", re.I)
COMPANION = re.compile(r"eligib|criteria|timeline|apply|how-to|process|faq|guideline|who-can|selection|documents|terms", re.I)
SKIP = re.compile(r"login|signin|sign-in|register|password|facebook|twitter|linkedin|youtube|instagram|whatsapp|news|press|annual-report|careers|privacy|cookie|sitemap|\.(jpg|png|zip|doc|docx|xls)$", re.I)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def regdom(u):
    h = host(u).split(".")
    return ".".join(h[-3:]) if len(h) >= 3 and h[-2] in ("ac", "co", "gov", "nic", "edu", "org") else ".".join(h[-2:])


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80]


# --------------------------------------------------------------------------- discovery
def parse_nsp(doc):
    """Structured parser for the NSP 'All-Scholarships' page -> list of candidate dicts."""
    soup = BeautifulSoup(doc.get("html", ""), "lxml")
    out = []
    apply_link = next((l["href"] for l in doc["links"] if re.search(r"apply for scholarship", l["text"], re.I)), None)
    for item in soup.select("div.accordion-item"):
        btn = item.find("button", class_="accordion-button")
        if not btn:
            continue
        ministry = F.clean(btn.get_text(" "))
        if re.fullmatch(r"(Students|Institutes|Officers|Public|Fellowship)", ministry):
            continue
        for row in item.select("div.accordion-body div.col-md-10"):
            h = row.find("h6")
            if not h:
                continue
            raw = F.clean(h.get_text(" "))
            spans = [F.clean(s.get_text(" ")) for s in row.find_all("span")]
            m = re.search(r"\((Merit Based|Welfare Based)[^)]*\)\s*$", raw)
            name = raw[:m.start()].strip() if m else raw
            links = {a.get_text(strip=True).lower(): urllib.parse.urljoin(doc["final_url"], a["href"]) for a in row.find_all("a", href=True)}
            c = dict(name=re.sub(r"\s+", " ", name), provider=ministry, kind="nsp", hub_url=doc["url"], raw_title=raw,
                     spans=spans, guideline=links.get("specifications"), faq=links.get("faq"), apply=apply_link, scheme_type=m.group(1) if m else "")
            out.append(c)
    return out


def hub_dates(c, hubdoc):
    """Opening / closing dates from the NSP listing cards, as evidence dicts (quote verbatim from hub text)."""
    res = {}
    for sp in c["spans"]:
        d = find_dates(sp)
        if not d:
            continue
        if re.search(r"Scheme Open from", sp, re.I):
            res["opening"] = {"value": d[0][0].isoformat(), "quote": sp, "url": hubdoc["url"], "offset": hubdoc["text"].find(sp), "method": "listing-card"}
        elif re.search(r"Student Application (Open till|Closed on)", sp, re.I):
            res["closing"] = {"value": d[0][0].isoformat(), "quote": sp, "url": hubdoc["url"], "offset": hubdoc["text"].find(sp), "method": "listing-card"}
    return res


GENERIC = {"scholarship", "scholarships", "fellowship", "fellowships", "grant", "grants", "award", "awards", "india", "support",
           "resource", "hub", "host", "who", "can", "apply", "explore", "partner", "opportunities", "grantees", "medical", "education",
           "pdf", "about", "home", "financial", "aid", "what", "we", "do", "how", "our", "the", "a", "for", "to", "of", "and", "in", "students", "student", "tata", "trusts"}
BAD_NAME = re.compile(r"^[a-z]\)|section|application form|\d{6}|mr\b|faculty|results?\b|list of|sanction|notice|intimation|details$|public|portal|distinguished|alumni|database|archive|filter|search|about us|datenbank|overview|host a|resource|partner|opportunities for|who can apply|support|alumni|impact|news|story|stories|why study|find a|timeline|faq|medical|annual|report", re.I)


def good_name(nm):
    """A programme name must contain a funding word and at least one distinctive (non-generic) token."""
    if not nm or not KW.search(nm) or BAD_NAME.search(nm):
        return False
    toks = [t for t in re.findall(r"[a-z0-9]+", nm.lower()) if t not in GENERIC]
    return len(toks) >= 1


def page_title_name(doc, link_text=""):
    """Programme name from the page: title part that mentions scholarship/fellowship; otherwise <org> + section."""
    parts = [p.strip() for p in re.split(r"\s[|–—-]\s|\s::\s", doc["title"]) if p.strip()]
    for p in parts:
        if KW.search(p) and 6 < len(p) < 90:
            return re.sub(r"^(?:Empowering Dreams:\s*)", "", p)
    if link_text and KW.search(link_text) and len(link_text) < 80:
        return link_text.strip()
    return None


def detail_score(doc):
    t = doc["text"]
    sc = 0
    sc += 1 if KW.search(doc["title"]) else 0
    sc += 1 if re.search(r"(Rs\.?|₹|INR|£|€|\$)\s?[\d,]+", t) else 0
    sc += 1 if re.search(r"eligib|who can apply|criteria", t, re.I) else 0
    sc += 1 if re.search(r"apply|application|deadline|last date", t, re.I) else 0
    sc += 1 if len(t) > 1500 else 0
    return sc


def discover_generic(hub, run_fetch, max_pages=14):
    """Follow scholarship-relevant links from a hub (depth<=2), keep official pages that look like detail pages,
    and group them into programmes. Returns list of candidates {name, provider, docs:[fetched docs]}."""
    base = hub["url"]
    hdoc = run_fetch(base)
    if not hdoc.get("ok"):
        return [], hdoc
    queue, seen, pages = [(base, hdoc, "", 0)], {base}, []
    cands = []
    while queue and len(cands) < max_pages:
        url, doc, ltext, depth = queue.pop(0)
        cands.append((url, doc, ltext, depth))
        if depth >= 2:
            continue
        for l in doc["links"]:
            u = l["href"].split("#")[0]
            if u in seen or SKIP.search(u) or not u.startswith("http"):
                continue
            blob = l["text"] + " " + u
            if not KW.search(blob) and not (hub.get("programme_name") and COMPANION.search(blob)):
                continue
            ci = classify(u)
            if not ci["official"]:
                continue
            # stay on the hub's registrable domain or on another official domain explicitly of gov/academic type
            if regdom(u) != regdom(base):
                continue
            seen.add(u)
            if len(seen) > max_pages * 3:
                break
            d = run_fetch(u)
            if d.get("ok") and len(d["text"]) > 300:
                queue.append((u, d, l["text"], depth + 1))
    # group into programmes
    progs = {}
    for url, doc, ltext, depth in cands:
        if detail_score(doc) < 3 and not (doc["kind"] == "pdf" and detail_score(doc) >= 2):
            continue
        ci = classify(url)
        nm = page_title_name(doc, ltext)
        if not nm and doc["kind"] == "pdf":
            fn = urllib.parse.unquote(urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]).rsplit(".", 1)[0]
            nm = f"{ci['org'].split(' (')[0]} {re.sub(r'[-_]+', ' ', fn).title()}"
        if nm:
            nm = re.sub(r"^India \((.*)\)$", r"\1", nm).strip()
            nm = re.sub(r"\bWhat We Do\b\s*", "", nm).strip()
        if not good_name(nm):
            segs = [s for s in urllib.parse.urlparse(url).path.split("/") if s]
            org = ci["org"].split(" (")[0]
            nm = f"{org} {segs[0].replace('-', ' ').replace('.php', '').replace('.aspx', '').title()}" if segs else None
            if not good_name(nm) or not KW.search(nm):
                continue
        key = (host(url), frozenset(re.findall(r"[a-z0-9]+", nm.lower())) - {"the", "of", "for", "and", "in", "a"})
        matched = next((k for k in progs if k[0] == key[0] and (len(k[1] & key[1]) / max(1, len(k[1] | key[1]))) >= 0.5), None)
        progs.setdefault(matched or key, {"name": nm, "docs": [], "ci": ci})["docs"].append(doc)
    if hub.get("programme_name"):
        # single-product site: every fetched page of the site is evidence for the one named programme
        docs = [d for u_, d, lt, dp in cands if d.get("ok")]
        ci = classify(base)
        return [dict(name=hub["programme_name"], provider=ci["org"], kind="generic", hub_url=base, docs=docs[:8], primary=docs[0])], hdoc
    used = {d["url"] for v in progs.values() for d in v["docs"]}
    out = []
    for k, v in progs.items():
        docs = sorted(v["docs"], key=lambda d: (len(urllib.parse.urlparse(d["url"]).path), -len(d["text"])))
        # companions: unassigned pages of the same host under the programme's path prefix (eligibility, timeline, how-to-apply...)
        pre = urllib.parse.urlparse(docs[0]["url"]).path.rstrip("/")
        pre = pre.rsplit("/", 1)[0] + "/" if docs[0]["kind"] == "pdf" or pre.count("/") > 1 and False else (pre + "/")
        for url, doc, ltext, depth in cands:
            if url not in used and doc.get("ok") and host(url) == host(docs[0]["url"]) and urllib.parse.urlparse(url).path.startswith(pre) and pre.count("/") >= 2 and COMPANION.search(url + " " + ltext) and len(doc["text"]) > 300:
                docs.append(doc); used.add(url)
        out.append(dict(name=v["name"], provider=v["ci"]["org"].split(" (")[0] if v["ci"]["source_type"] != "government" else v["ci"]["org"],
                        kind="generic", hub_url=base, docs=docs[:6], primary=docs[0]))
    return out, hdoc


def discover_leads(hub, run_fetch):
    """Aggregator hub: record scholarship mentions as LEADS, resolve outbound links to official domains.
    Leads are never verified from the aggregator itself."""
    d = run_fetch(hub["url"])
    leads = []
    if not d.get("ok"):
        return leads, d
    for l in d["links"]:
        if KW.search(l["text"]) and 8 < len(l["text"]) < 120 and host(l["href"]) != host(hub["url"]):
            ci = classify(l["href"])
            leads.append({"name": l["text"], "url": l["href"], "official": ci["official"], "ci": ci})
    return leads, d


# --------------------------------------------------------------------------- persistence helpers
def upsert_source(db, url, role, status, content="", now=""):
    ci = classify(url)
    h = __import__("hashlib").sha1(content.encode()).hexdigest() if content else None
    r = db.execute("SELECT url FROM sources WHERE url=?", (url,)).fetchone()
    if r:
        db.execute("UPDATE sources SET last_fetched=?, last_status=?, content_hash=COALESCE(?,content_hash), fail_count=CASE WHEN ?=200 THEN 0 ELSE fail_count+1 END WHERE url=?",
                   (now, status, h, status, url))
    else:
        db.execute("INSERT INTO sources(url,domain,source_type,is_official,org,role,first_seen,last_fetched,last_status,content_hash,fail_count) VALUES(?,?,?,?,?,?,?,?,?,?,0)",
                   (url, host(url), ci["source_type"], int(ci["official"]), ci["org"], role, now, now, status, h))


def check_url(url, run_fetch):
    """Application URL resolves? (200..399, or accepted as an official portal that blocks bots with 403/405)."""
    d = run_fetch(url, cache_only_ok=True)
    s = d.get("status", 0)
    if d.get("ok"):
        return "ok"
    if s in (401, 403, 405, 429):
        return "ok"  # reachable but gated (login/WAF); the host answered
    return f"HTTP {s or 'unreachable'}"


def build_record(cand, run_fetch):
    """Fetch docs for a candidate, extract, verify. Returns record dict ready for storage."""
    docs_raw, hub = [], None
    texts = {}
    if cand["kind"] == "nsp":
        hubdoc = cand["hubdoc"]
        hub = hub_dates(cand, hubdoc)
        texts[hubdoc["url"]] = hubdoc["text"]
        gdoc = fdoc = None
        if cand["guideline"]:
            gdoc = run_fetch(cand["guideline"])
        if cand["faq"] and cand["faq"] != cand["guideline"]:
            fdoc = run_fetch(cand["faq"])
        docs_raw = [d for d in (gdoc, fdoc) if d and d.get("ok") and d["text"]]
        primary = gdoc if gdoc and gdoc.get("ok") else hubdoc
        page_ok = bool(hubdoc.get("ok"))
        official_url = cand["guideline"] if gdoc and gdoc.get("ok") else hubdoc["url"]
        listing = hubdoc
        docs = [Doc(d["url"], d["text"], d["kind"], d.get("title", "")) for d in docs_raw]
        if not docs:
            docs = [Doc(hubdoc["url"], cand["raw_title"] + " " + " ".join(cand["spans"]), "html")]
    else:
        docs_raw = cand["docs"]
        primary = cand["primary"]
        page_ok = bool(primary.get("ok"))
        official_url = primary["url"]
        listing = primary
        docs = [Doc(d["url"], d["text"], d["kind"], d.get("title", "")) for d in docs_raw]
    for d in docs_raw:
        texts[d["url"]] = d["text"]

    fields = extract(docs, cand["name"], hub)
    if hub:
        fields = {**fields}
    # application URL
    app, appl_where = None, None
    if cand["kind"] == "nsp":
        app = cand.get("apply")
        appl_where = cand["hub_url"]
    else:
        for pass_official in (True, False):
          for d in docs_raw:
            for l in d["links"]:
                if pass_official and not classify(l["href"])["official"]:
                    continue
                if re.search(r"^\s*(apply|apply now|apply here|start (?:your )?application|online application|application portal|register( & | and )?apply|apply online)\b|scholarships?\.[a-z.]+/?$", l["text"], re.I) and not re.search(r"login", l["text"], re.I):
                    if l["href"].startswith("http"):
                        app, appl_where = l["href"], d["url"]
                        break
            if app:
                break
          if app:
            break
    app_ci = classify(app) if app else None
    app_status = check_url(app, run_fetch) if app and app_ci["official"] else ("n/a" if not app else "off-domain")

    # mark verification of each field's evidence
    for k, f in fields.items():
        f["field"] = k
        f["_ok"] = date_ok(f) if k in ("closing_date", "opening_date") and f["value"][:1].isdigit() and f["method"] != "listing-card" else quote_ok(f, texts)
        if k == "closing_date" and not f["value"][:1].isdigit():
            f["_ok"] = quote_ok(f, texts)
        if f["method"] == "listing-card":
            f["_ok"] = quote_ok(f, texts) and date_ok(f)

    # name presence
    nt = name_tokens(cand["name"])
    ptxt = norm((listing.get("title", "") + " " + listing.get("text", ""))[:200000]) if cand["kind"] != "nsp" else norm(listing["text"])
    # NSP: name must appear in listing page; generic: in title/h1 region
    where = "listing page" if cand["kind"] == "nsp" else "page title/body"
    if cand["kind"] == "nsp":
        cov = 1.0 if norm(cand["raw_title"]) in ptxt else len([t for t in nt if t in ptxt]) / max(1, len(nt))
    else:
        head = norm(primary.get("title", "") + " " + primary["text"][:600])
        cov = len([t for t in nt if t in head]) / max(1, len(nt))

    # consistency: independent views must agree. NSP: listing-card name vs guideline-document title + dates.
    agree, agree_d, conflicts = 0.5, "single view only", []
    if cand["kind"] == "nsp":
        if docs_raw:
            gt = norm(docs_raw[0]["text"][:3000])
            tcov = len([t for t in nt if t in gt]) / max(1, len(nt))
            agree = 0.4 + 0.6 * tcov
            agree_d = f"listing name vs guideline document: {int(tcov*100)}% name-token agreement"
            ds = {x[0] for x in find_dates(docs_raw[0]["text"][:6000])}
            cl = fields.get("closing_date")
            if cl and cl["_ok"] and len(ds) > 0 and cl["value"][:1].isdigit():
                cd = dt.date.fromisoformat(cl["value"])
                later = [x for x in ds if x.year >= cd.year and x > cd and (x - cd).days < 400 and re.search(r"last date|closing", docs_raw[0]["text"], re.I)]
        else:
            agree, agree_d = 0.3, "guideline document unavailable; listing only"
    else:
        if len(docs_raw) >= 2:
            cd_vals = {d_["value"] for d_ in [extract([Doc(x["url"], x["text"], x["kind"])], cand["name"]).get("closing_date") for x in docs_raw] if d_}
            if len(cd_vals) > 1:
                conflicts.append("closing dates differ across official pages: " + ", ".join(sorted(cd_vals)))
                agree, agree_d = 0.0, "official pages disagree on closing date"
            else:
                agree, agree_d = 0.9 if cd_vals else 0.7, f"{len(docs_raw)} official pages agree (no conflicting deadline)"
        else:
            agree, agree_d = 0.7, "single official page; no conflicting source found"
    ci = classify(official_url)
    ctx = dict(official=ci["official"], source_type=ci["source_type"], rule=ci["rule"], page_ok=page_ok, name_cov=cov,
               presence_where=where, app_status=app_status, app_official=bool(app_ci and app_ci["official"]),
               agree=agree, agree_detail=agree_d, conflicts=conflicts, shared_doc=cand.get("shared_doc", False),
               garble=max([garble_ratio(d["text"]) for d in docs_raw] + [0.1 if d.get("scan_ratio", 0) >= 0.5 else 0 for d in docs_raw] + [0]))
    rec = dict(name=cand["name"], provider=cand["provider"], source_type=ci["source_type"], official_url=official_url,
               application_url=app, kind=cand["kind"])
    if cand["kind"] == "nsp":
        ctx["shared_doc"] = bool(cand.get("shared_doc"))
    res = score(rec, fields, texts, ctx, TODAY)
    # app url evidence row
    if app:
        fields["application_url"] = {"field": "application_url", "value": app, "quote": ("Apply For Scholarship" if cand["kind"] == "nsp" else next((l["text"] for d in docs_raw for l in d["links"] if l["href"] == app), "")),
                                     "url": appl_where, "offset": -1, "method": "page-link", "_ok": True}
    return rec, fields, res, texts


# --------------------------------------------------------------------------- status logic
def derive_status(fields, label):
    c = fields.get("closing_date")
    o = fields.get("opening_date")
    if not c or not c["_ok"]:
        return "REVIEW_REQUIRED"
    if c["value"].startswith("Applications closed"):
        return "EXPIRED"
    if c["value"].startswith("Month:"):
        y, m = map(int, c["value"][7:].split("-"))
        nxt = dt.date(y + (m == 12), m % 12 + 1, 1)
        return "EXPIRED" if nxt <= TODAY else ("EXPIRING_SOON" if (nxt - TODAY).days <= 31 else "ACTIVE")
    if not c["value"][:1].isdigit():
        return "ACTIVE"
    cd = dt.date.fromisoformat(c["value"])
    if o and o["_ok"] and o["value"][:1].isdigit() and dt.date.fromisoformat(o["value"]) > TODAY:
        return "UPCOMING"
    if cd < TODAY:
        return "EXPIRED"
    if (cd - TODAY).days <= 30:
        return "EXPIRING_SOON"
    return "ACTIVE"


def store(db, run_id, rec, fields, res, now, mode):
    key = ("nsp:" if rec["kind"] == "nsp" else "web:" + host(rec["official_url"]) + ":") + slug(rec["name"])
    old = db.execute("SELECT * FROM scholarships WHERE key=?", (key,)).fetchone()
    vals = {col: (fields[f]["value"] if f in fields and fields[f]["_ok"] else NS) for f, col in FIELD_COLS.items()}
    status = derive_status(fields, res["label"])
    row = dict(name=rec["name"], provider=rec["provider"], source_type=rec["source_type"], official_url=rec["official_url"],
               application_url=rec["application_url"] or NS, status=status, label=res["label"], confidence=res["confidence"],
               last_verified=now, last_seen_run=run_id, missing_runs=0, **vals)
    row["criteria_json"] = json.dumps({k: fields[k]["value"] for k in ("education_level", "income", "age", "academic", "category", "gender", "domicile", "institution") if k in fields and fields[k]["_ok"]}, ensure_ascii=False)
    changes = []
    if old is None:
        cols = ",".join(row) + ",key,first_seen,first_run"
        db.execute(f"INSERT INTO scholarships({cols}) VALUES({','.join('?'*(len(row)+3))})", list(row.values()) + [key, now, run_id])
        sid = db.execute("SELECT id FROM scholarships WHERE key=?", (key,)).fetchone()[0]
        kind = "NEW"
    else:
        sid = old["id"]
        # change detection: compare previous stored value with newly extracted value, per tracked field
        prev_ev = {r["field"]: r for r in db.execute("SELECT * FROM evidence WHERE scholarship_id=?", (sid,))}
        for f in TRACKED:
            col = FIELD_COLS.get(f, f)
            ov = old[col]
            nv = row.get(col, NS)
            if f in ("application_url", "official_url"):
                nv = row[f] if f in row else NS
            if (ov or NS) != (nv or NS):
                pe = prev_ev.get(f)
                ne = fields.get(f)
                changes.append(dict(field=f, old=ov or NS, new=nv or NS, old_q=pe["quote"] if pe else None,
                                    new_q=ne["quote"] if ne else None, src=(ne or {}).get("url") or (pe["source_url"] if pe else rec["official_url"])))
        if old["status"] in ("NO_LONGER_VERIFIABLE",):
            changes.append(dict(field="status", old=old["status"], new=status, old_q=None, new_q=None, src=rec["official_url"]))
        sets = ",".join(f"{k}=?" for k in row)
        db.execute(f"UPDATE scholarships SET {sets} WHERE id=?", list(row.values()) + [sid])
        kind = "CHANGED" if changes else "UNCHANGED"
        if old["status"] != status and not any(c["field"] == "status" for c in changes) and old["status"] not in (None,):
            changes.append(dict(field="status", old=old["status"], new=status, old_q=None, new_q=None, src=rec["official_url"]))
            kind = "CHANGED"
    for c in changes:
        db.execute("INSERT INTO changes(scholarship_id,run_id,field,old_value,new_value,old_quote,new_quote,source_url,detected_at,change_type,simulated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                   (sid, run_id, c["field"], c["old"], c["new"], c["old_q"], c["new_q"], c["src"], now, "CHANGE_DETECTED", int(mode == "simulated")))
    # evidence + breakdown (current state; history is in `changes`)
    db.execute("DELETE FROM evidence WHERE scholarship_id=?", (sid,))
    for f, ev in fields.items():
        db.execute("INSERT INTO evidence(scholarship_id,field,value,quote,source_url,char_offset,method,quote_verified,run_id) VALUES(?,?,?,?,?,?,?,?,?)",
                   (sid, f, ev["value"], ev["quote"], ev["url"], ev.get("offset", -1), ev["method"], int(ev["_ok"]), run_id))
    db.execute("DELETE FROM score_breakdown WHERE scholarship_id=?", (sid,))
    for b in res["breakdown"]:
        db.execute("INSERT INTO score_breakdown(scholarship_id,run_id,check_name,weight,score,points,detail) VALUES(?,?,?,?,?,?,?)",
                   (sid, run_id, b["check"], b["weight"], b["score"], b["points"], b["detail"]))
    if res["notes"] or res["penalty"]:
        db.execute("INSERT INTO score_breakdown(scholarship_id,run_id,check_name,weight,score,points,detail) VALUES(?,?,?,?,?,?,?)",
                   (sid, run_id, "penalties_and_caps", 0, 0, -res["penalty"], " | ".join(res["notes"])))
    return sid, kind, changes


# --------------------------------------------------------------------------- run
def run(mode="live", snap=None, note="", only=None):
    offline = mode in ("replay", "simulated")
    if snap:
        F.SNAP = snap
    db = connect()
    now = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    run_id = db.execute("INSERT INTO runs(started_at,mode,note) VALUES(?,?,?)", (now, mode, note)).lastrowid
    db.commit()
    cfg = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config", "sources.json")))
    stats = dict(pages_fetched=0, hubs=0, candidates=0, new=0, changed=0, unchanged=0, removed=0, leads=0, errors=0)
    cache = {}

    def run_fetch(url, cache_only_ok=False):
        if url in cache:
            return cache[url]
        d = F.fetch(url, offline=offline)
        if offline and not d.get("ok") and cache_only_ok:
            d = {"url": url, "ok": True, "status": 200, "text": "", "links": [], "kind": "html"}  # URL checks are not replayed
        stats["pages_fetched"] += 1
        upsert_source(db, url, "doc", d.get("status", 0), d.get("text", ""), now)
        cache[url] = d
        return d

    seen_ids, hub_ok_prefix = set(), []
    for hub in cfg["hubs"]:
        if only and only not in hub["url"]:
            continue
        stats["hubs"] += 1
        log(f"[hub] {hub['mode']:10} {hub['url']}")
        cands, hubdoc = [], None
        if hub["mode"] == "nsp_table":
            hubdoc = run_fetch(hub["url"])
            if hubdoc.get("ok"):
                cands = parse_nsp(hubdoc)
                # a guideline PDF shared by >1 scheme cannot be attributed to a single scheme
                cnt = {}
                for c in cands:
                    cnt[c["guideline"]] = cnt.get(c["guideline"], 0) + 1
                for c in cands:
                    c["hubdoc"] = hubdoc
                    c["shared_doc"] = bool(c["guideline"]) and cnt[c["guideline"]] > 1
                hub_ok_prefix.append("nsp:")
                log(f"   parsed {len(cands)} scheme cards")
        elif hub["mode"] == "generic":
            cands, hubdoc = discover_generic(hub, run_fetch)
            if hubdoc.get("ok"):
                hub_ok_prefix.append("web:" + host(hub["url"]))
            log(f"   discovered {len(cands)} programmes")
        elif hub["mode"] == "aggregator":
            leads, hubdoc = discover_leads(hub, run_fetch)
            for l in leads:
                state = "official-domain-link: queued for official verification" if l["official"] else "aggregator-only: not verifiable"
                db.execute("INSERT OR IGNORE INTO leads(name,url,found_on,found_run,resolved_official_url,state) VALUES(?,?,?,?,?,?)",
                           (l["name"], l["url"], hub["url"], run_id, l["url"] if l["official"] else None, state))
            stats["leads"] += len(leads)
            log(f"   {len(leads)} leads (never verified from aggregator)")
            continue
        if hubdoc is not None and not hubdoc.get("ok"):
            stats["errors"] += 1
            log("   HUB FAILED", hubdoc.get("status"), hubdoc.get("error"))
        for c in cands:
            stats["candidates"] += 1
            try:
                rec, fields, res, texts = build_record(c, run_fetch)
                sid, kind, ch = store(db, run_id, rec, fields, res, now, mode)
            except Exception as e:  # keep crawling; record error
                import traceback
                stats["errors"] += 1
                log("   ERR", c["name"], repr(e)); traceback.print_exc()
                continue
            seen_ids.add(sid)
            stats[{"NEW": "new", "CHANGED": "changed", "UNCHANGED": "unchanged"}[kind]] += 1
            log(f"   {kind:9} {res['confidence']:5.1f}% {res['label']:16} {rec['name'][:70]}")
        db.commit()

    # ------------- stale / removed detection -------------
    for r in db.execute("SELECT id,key,name,status,official_url FROM scholarships").fetchall():
        if r["id"] in seen_ids:
            continue
        # only judge scholarships whose hub was successfully fetched this run
        if not any(r["key"].startswith(p) for p in hub_ok_prefix):
            continue
        db.execute("UPDATE scholarships SET missing_runs=missing_runs+1 WHERE id=?", (r["id"],))
        if r["status"] != "NO_LONGER_VERIFIABLE":
            db.execute("UPDATE scholarships SET status='NO_LONGER_VERIFIABLE', label='REVIEW_REQUIRED', confidence=MIN(confidence,50) WHERE id=?", (r["id"],))
            db.execute("INSERT INTO changes(scholarship_id,run_id,field,old_value,new_value,old_quote,new_quote,source_url,detected_at,change_type,simulated) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                       (r["id"], run_id, "status", r["status"], "NO_LONGER_VERIFIABLE", None,
                        "Scheme card no longer present on the official listing page in this crawl", r["official_url"], now, "REMOVED_FROM_SOURCE", int(mode == "simulated")))
            stats["removed"] += 1
            log(f"   REMOVED   {r['name'][:70]}")
    stats["finished"] = dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    db.execute("UPDATE runs SET finished_at=?, stats_json=? WHERE id=?", (stats["finished"], json.dumps(stats), run_id))
    db.commit()
    log("[done]", json.dumps(stats))
    return run_id


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--mode", default=None)
    ap.add_argument("--snap", default=None)
    ap.add_argument("--note", default="")
    ap.add_argument("--only", default=None)
    a = ap.parse_args()
    run(mode=a.mode or ("replay" if a.offline else "live"), snap=a.snap, note=a.note, only=a.only)
