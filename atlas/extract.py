"""Evidence-first extraction.

Nothing here generates text. Every extracted field is a (value, quote, source_url, offset) tuple where
`quote` is a verbatim span of the fetched document. A field that cannot be anchored to a span is simply
absent and is rendered as "Not specified". The verifier later re-checks every quote against the snapshot.
"""
import re, datetime as dt

NS = "Not specified"
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

DATE_RES = [
    re.compile(r"\b(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]{3,9})\.?,?\s+(\d{4})\b"),
    re.compile(r"\b([A-Za-z]{3,9})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b"),
]


def find_dates(text):
    """-> list of (date, start, end) for every unambiguous date in text."""
    out = []
    for i, rx in enumerate(DATE_RES):
        for m in rx.finditer(text):
            try:
                if i == 0:
                    d = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                elif i == 1:
                    mo = MONTHS.get(m.group(2)[:3].lower())
                    if not mo:
                        continue
                    d = dt.date(int(m.group(3)), mo, int(m.group(1)))
                else:
                    mo = MONTHS.get(m.group(1)[:3].lower())
                    if not mo:
                        continue
                    d = dt.date(int(m.group(3)), mo, int(m.group(2)))
                out.append((d, m.start(), m.end()))
            except ValueError:
                pass
    return sorted(out, key=lambda x: x[1])


ABBR = re.compile(r"(?:\bRs|\bNo|\bNos|\bDr|\bMr|\bMrs|\bMs|\bSt|\bvs|\betc|\bi\.e|\be\.g|\bS|\bSl|\bapprox|\bResp|\bPh|\bB|\bM)\.$", re.I)


def sentences(text):
    """Split into (sentence, offset). Splits on . ; and list markers, but not after abbreviations (Rs. 8 lakh)."""
    parts, pos = [], 0
    for m in re.finditer(r"(?<=[.;?!])\s+(?=[A-Z0-9(])|\s+(?=\(?[ivx]{1,4}\)\s)|\s+(?=\d{1,2}\)\s)", text):
        if ABBR.search(text[max(0, m.start() - 8):m.start()]):
            continue
        parts.append((text[pos:m.start()], pos))
        pos = m.end()
    parts.append((text[pos:], pos))
    return [(s, o) for s, o in parts if len(s.strip()) > 15]


HEAD_RE = re.compile(r"(?:(?<=\s)|^)(?:\d{1,2}(?:\.\d)?\.?\s+)?([A-Z][A-Z0-9 &/,\-()']{5,70}?)\s?:\s")


def sections(text):
    """Numbered guideline-style sections: [(heading, body, body_offset)]."""
    hs = list(HEAD_RE.finditer(text))
    out = []
    for i, m in enumerate(hs):
        end = hs[i + 1].start() if i + 1 < len(hs) else len(text)
        out.append((m.group(1).strip(), text[m.end():end].strip(), m.end()))
    return out


def clip(s, n=600):
    s = s.strip()
    if len(s) <= n:
        return s
    cut = s[:n]
    k = max(cut.rfind(". "), cut.rfind("; "))
    return (cut[:k + 1] if k > n * 0.5 else cut).strip()


def money(text):
    """Find monetary amounts -> [(rupees:int|None, raw, start, end)]"""
    out = []
    for m in re.finditer(r"(?:Rs\.?|INR|₹)\s?([\d][\d,]*(?:\.\d+)?)\s?(lakhs?|lacs?|crores?)?(?:/-)?", text, re.I):
        try:
            n = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        u = (m.group(2) or "").lower()
        if u.startswith(("lakh", "lac")):
            n *= 100000
        elif u.startswith("crore"):
            n *= 10000000
        out.append((int(n), m.group(0), m.start(), m.end()))
    return out


def inr(n):
    s = str(int(n))
    if len(s) <= 3:
        return "₹" + s
    head, tail = s[:-3], s[-3:]
    parts = []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return "₹" + ",".join(parts + [tail])


class Doc:
    def __init__(self, url, text, kind="html", title=""):
        self.url, self.text, self.kind, self.title = url, text, kind, title
        self.secs = sections(text) if kind == "pdf" or re.search(r"\d\.\d\s+[A-Z ]{5,}:", text) else []
        self.sents = sentences(text)


def F(value, quote, doc, offset, method):
    return {"value": value, "quote": quote, "url": doc.url, "offset": offset, "method": method}


def scoped_hit(scope, rx):
    for d, s, o in scope:
        if re.search(rx, s, re.I):
            return [(d, s.strip(), o)]
    return []


def find_in_sections(doc, head_rx, maxlen=700):
    for h, body, off in doc.secs:
        if re.search(head_rx, h, re.I):
            q = clip(body, maxlen)
            if len(q) > 20:
                return q, off
    return None


def sent_hit(doc, rx, extra=None, first=True):
    hits = []
    for s, o in doc.sents:
        if re.search(rx, s, re.I) and (extra is None or re.search(extra, s, re.I)):
            hits.append((s.strip(), o))
            if first:
                break
    return hits


LEVELS = [
    ("School (Class 1-8)", r"\bclass(?:es)?\s*(?:[1-8]|i{1,3}|iv|v|vi{0,3})\b|primary|elementary"),
    ("Pre-matric (Class 9-10)", r"pre[- ]?matric|\bclass(?:es)?\s*(?:9|10|ix|x)\b|\b(?:9th|10th)\b"),
    ("Class 11-12", r"post[- ]?matric|\bclass(?:es)?\s*(?:11|12|xi|xii)\b|10\s?\+\s?2|higher secondary|senior secondary|\b(?:11th|12th)\b"),
    ("Diploma", r"\bdiploma\b|polytechnic"),
    ("Undergraduate", r"undergraduate|under[- ]graduate|\bb\.?\s?(?:tech|e|sc|a|com|pharm|arch|des)\b|\bbachelor|\bdegree\b|\bmbbs\b|\bUG\b"),
    ("Postgraduate", r"post[- ]?graduate|\bm\.?\s?(?:tech|e|sc|a|com|phil|ba)\b|\bmaster|\bPG\b|\bmba\b|\bmsw\b"),
    ("PhD / Research", r"ph\.?\s?d|doctoral|doctorate|research (?:scholar|fellow)|\bjrf\b|\bsrf\b"),
]
CATEGORIES = [
    ("SC", r"\bSC\b|scheduled castes?"), ("ST", r"\bST\b|scheduled tribes?"),
    ("OBC", r"\bOBC\b|other backward"), ("EWS", r"\bEWS\b|economically weaker"),
    ("Minority", r"minorit(?:y|ies)|muslim|christian|sikh|buddhist|jain|parsi"),
    ("PwD / Disability", r"disabilit|specially[- ]abled|divyang|persons? with disabilit|\bPwD\b"),
    ("Orphan", r"\borphan"), ("Defence / Armed forces wards", r"armed forces|ex-?servicem|paramilitary|martyr|defence personnel|police personnel"),
    ("Girl students", r"girl (?:child|student)s?|women|female|girls"),
    ("Labour / Beedi-mine workers' wards", r"beedi|cine worker|mica|limestone|dolomite|iron ore|manganese|chrome ore"),
    ("North-East region", r"north[- ]?east|\bNER\b"), ("J&K / Ladakh", r"jammu|kashmir|ladakh"),
]


def substantive_elig(q):
    return len(q) >= 150 and bool(re.search(r"\b(must|should|required|need to|have to|only|at least|minimum|citizen|resident|hold(?:ing)? a|completed|studying|pursuing|enrolled|admitted|age|income|aged|applicants? who|students? who|candidates? who)\b", q, re.I)) \
        and not re.search(r"learn more|click here|read more", q[:200], re.I)


def extract(docs, name="", hub=None):
    """docs: list[Doc] (primary first). Returns {field: evidence-dict}. Missing fields are absent."""
    out = {}

    def put(field, f):
        if f and field not in out:
            out[field] = f

    # --- eligibility text -------------------------------------------------
    for d in docs:
        r = find_in_sections(d, r"eligib|who can apply|qualif", 1000)
        if r:
            put("eligibility", F(clip(r[0], 1000), clip(r[0], 1000), d, r[1], "section-heading"))
            break
    if "eligibility" not in out:
        for d in docs:
            hs = [(s_, o) for s_, o in d.sents if re.search(r"eligib", s_, re.I) and len(s_) < 500 and not re.search(r"__|annexure|undertaking", s_, re.I)]
            if hs:
                a0 = hs[0][1]
                q = clip(d.text[a0:a0 + 900], 900)
                put("eligibility", F(q, q, d, a0, "sentence-trigger"))
                break
    if "eligibility" in out and not substantive_elig(out["eligibility"]["quote"]):
        del out["eligibility"]
    ecorpus = out["eligibility"]["quote"] if "eligibility" in out else ""
    ed = next((d for d in docs if "eligibility" in out and d.url == out["eligibility"]["url"]), None)
    NOISE = re.compile(r"expand dropdown|skip to content|toggle submenu|cookies?|annexure|certificate that|i hereby|page \d+ of|signature|undertaking|_{3,}|\.{5,}", re.I)
    eo = out["eligibility"]["offset"] if "eligibility" in out else 0
    escope = [(ed, s_, o) for s_, o in ed.sents if eo - 5 <= o < eo + len(ecorpus) and not NOISE.search(s_)] if ed else []
    allscope = [(d, s_, o) for d in docs for s_, o in d.sents if not NOISE.search(s_) and len(s_) < 700]
    scope = escope + [x for x in allscope if x not in escope]

    # --- education level (eligibility region + programme name first) -----
    levels, first_q = [], None
    for lvl, rx in LEVELS:
        if re.search(rx, name, re.I):
            levels.append(lvl)
    for lvl, rx in LEVELS:
        for d, s_, o in escope or [x for x in allscope if re.search(r"eligib|studying|pursuing|enrolled|course|apply|applicant", x[1], re.I)]:
            if re.search(rx, s_, re.I):
                if lvl not in levels:
                    levels.append(lvl)
                first_q = first_q or (d, s_.strip(), o)
                break
    if levels:
        if not first_q:
            d0 = docs[0]
            mm = re.search("|".join(rx for l, rx in LEVELS if l in levels), d0.text, re.I)
            if mm and re.search("|".join(rx for l, rx in LEVELS if l in levels), name, re.I):
                a = max(0, mm.start() - 60)
                first_q = (d0, d0.text[a:mm.end() + 60], a)
            else:
                levels = []
        if first_q:
            d, s_, o = first_q
            put("education_level", F(" / ".join(levels), clip(s_, 400), d, o, "keyword-map"))

    # --- income -----------------------------------------------------------
    inc = None
    for d in [None]:
        for d, s, o in scope:
            if re.search(r"income", s, re.I):
                m = re.search(r"(?:not\s+(?:be\s+)?(?:more|exceed|greater|higher)[a-z ]{0,12}|less than|below|up\s?to|upto|within|maximum|max\.?|not exceeding|should not exceed|exceed)\s*(?:of\s*)?(?:Rs\.?|INR|₹)\s?([\d,\.]+)\s?(lakhs?|lacs?|crores?)?", s, re.I)
                if not m:
                    m = re.search(r"(?:Rs\.?|INR|₹)\s?([\d,\.]+)\s?(lakhs?|lacs?|crores?)?[^.]{0,40}?(?:or less|and below|or below|per annum or less)", s, re.I)
                if m:
                    amts = money(s[m.start():m.end() + 5] if "Rs" in m.group(0) or "₹" in m.group(0) or "INR" in m.group(0) else s)
                    if amts:
                        v = amts[0][0]
                        per = "per annum" if re.search(r"annum|annual|year|yearly", s, re.I) else ""
                        inc = F(("≤ " + inr(v) + " " + per).strip(), clip(s, 420), d, o, "income-regex")
                        break
                elif re.search(r"no income (?:limit|criteri|bar)|irrespective of (?:their )?(?:family )?income|income is not a (?:criteri|bar)", s, re.I):
                    inc = F("No income limit stated as a condition", clip(s, 300), d, o, "income-regex")
                    break
        if inc:
            break
    put("income", inc)

    # --- age --------------------------------------------------------------
    for d in [None]:
        for d, s, o in scope:
            if re.search(r"\bage\b|years? of age|aged", s, re.I) and re.search(r"\d{2}\s?(?:years|yrs)", s, re.I) and not re.search(r"age(?:-| )wise|percentage|average|marriage|manage|image|stage", s.split("years")[0][-60:], re.I):
                m = re.search(r"between\s+(\d{2})\s*(?:and|to|-)\s*(\d{2})\s*(?:years|yrs)", s, re.I)
                if m:
                    put("age", F(f"{m.group(1)}–{m.group(2)} years", clip(s, 350), d, o, "age-regex"))
                    break
                m = re.search(r"(?:not\s+(?:be\s+)?(?:more|exceed|older|above)[a-z ]{0,10}|below|under|less than|up\s?to|upto|maximum(?: age)?(?: of)?|max\.?)\s*(\d{2})\s*(?:years|yrs)", s, re.I) or \
                    re.search(r"(\d{2})\s*(?:years|yrs)\s*(?:of age\s*)?(?:or below|or less|or younger|and below)", s, re.I)
                if m:
                    put("age", F(f"≤ {m.group(1)} years", clip(s, 350), d, o, "age-regex"))
                    break
                m = re.search(r"(?:at least|minimum|not less than|above|over|older than)\s*(?:age of\s*)?(\d{2})\s*(?:years|yrs)", s, re.I)
                if m:
                    put("age", F(f"≥ {m.group(1)} years", clip(s, 350), d, o, "age-regex"))
                    break
        if "age" in out:
            break

    # --- academic requirements -------------------------------------------
    for d in [None]:
        for d, s, o in scope:
            if re.search(r"(?:minimum|at least|not less than|securing|scored?|obtained|marks|percentage|cgpa|grade|first class)", s, re.I) and \
                    re.search(r"\d{2}(?:\.\d+)?\s?%|\d(?:\.\d+)?\s?cgpa|cgpa\s*(?:of|:)?\s*\d|first class|percentile", s, re.I) and \
                    re.search(r"eligib|apply|candidate|student|applicant|secur|obtain|pursu|scholar", s, re.I) and len(s) < 600:
                m = re.search(r"(\d{2}(?:\.\d+)?)\s?%", s)
                m2 = re.search(r"cgpa[^.]{0,30}?(\d(?:\.\d+)?)|(\d(?:\.\d+)?)\s?cgpa", s, re.I)
                if m:
                    v = f"Minimum {m.group(1)}% marks"
                elif m2:
                    v = f"CGPA {m2.group(1) or m2.group(2)}"
                elif re.search(r"first class", s, re.I):
                    v = "First class"
                else:
                    v = "Percentile / rank based (see evidence)"
                put("academic", F(v, clip(s, 420), d, o, "academic-regex"))
                break
        if "academic" in out:
            break

    # --- category / gender -----------------------------------------------
    cats, cq = [], None
    cscope = ecorpus + " " + name
    for label, rx in CATEGORIES:
        if re.search(rx, cscope, re.I):
            cats.append(label)
            if not cq:
                for d, s, o in scope:
                    if re.search(rx, s, re.I):
                        cq = (d, clip(s, 400), o)
                        break
    if cats and cq:
        put("category", F(", ".join(cats), cq[1], cq[0], cq[2], "keyword-map"))
    if re.search(r"\b(girl|women|female)s?\b", cscope, re.I):
        for d, s, o in scope:
            if re.search(r"\b(girl|women|female)s?\b", s, re.I):
                put("gender", F("Female", clip(s, 300), d, o, "keyword-map"))
                break

    # --- domicile / citizenship ------------------------------------------
    for d in [None]:
        h = scoped_hit(scope, r"domicile|permanent resident|resident of|belong(?:s|ing)? to (?:the )?state|(?:must|should) be (?:an )?indian|indian (?:citizen|national|resident)s?|citizens? of india|indian passport|ordinarily resident|reside in")
        if h:
            d, s, o = h[0]
            put("domicile", F(clip(s, 350), clip(s, 350), d, o, "sentence-trigger"))
            break

    # --- institution ------------------------------------------------------
    for d in [None]:
        h = scoped_hit(scope, r"(?:AICTE|UGC|NAAC|NIRF|recogni[sz]ed|approved|affiliated|accredited|aided)[^.]{0,90}(?:institut|universit|college|school)|(?:institut|universit|college)[^.]{0,40}(?:recogni[sz]ed|approved|affiliated|accredited)")
        if h:
            d, s, o = h[0]
            put("institution", F(clip(s, 350), clip(s, 350), d, o, "sentence-trigger"))
            break

    # --- amount / benefit -------------------------------------------------
    for d in docs:
        r = find_in_sections(d, r"amount|benefit|value|quantum|financial assist|rate of|scholarship (?:amount|award)|award", 500)
        if r and (money(r[0]) or re.search(r"fully[- ]funded|tuition|stipend|living|covers", r[0], re.I)):
            am = money(r[0])
            v = inr(am[0][0]) if am else clip(r[0], 160)
            put("amount", F(v + (" (first amount stated; see evidence)" if len(am) > 1 else ""), clip(r[0], 500), d, r[1], "section-heading"))
            break
    if "amount" not in out:
        best = None
        for d in docs:
            for s, o in d.sents:
                am = money(s)
                if am and re.search(r"scholarship|fellowship|stipend|per (?:annum|month|year)|tuition|grant|award|financial assistance|loan|worth|up to|upto", s, re.I) and not re.search(r"income|salary of parent", s, re.I) and len(s) < 700:
                    best = best or (d, s, o, am)
        if best:
            d, s, o, am = best
            put("amount", F(inr(am[0][0]) + (" (see evidence for period / scope)"), clip(s, 450), d, o, "sentence-trigger"))
        else:
            for d in docs:
                h = sent_hit(d, r"fully[- ]funded|full tuition|tuition fees?|monthly (?:stipend|allowance)|living (?:allowance|expenses)|airfare|covers? the cost")
                if h:
                    s, o = h[0]
                    put("amount", F("Non-cash / foreign-currency benefit (see evidence)", clip(s, 450), d, o, "sentence-trigger"))
                    break

    # --- number of awards -------------------------------------------------
    for d in docs:
        for s, o in d.sents:
            m = re.search(r"(?:maximum|total|upto|up to|about|approximately)?\s*(?:of\s*)?(\d[\d,]*)\s+(?:new\s+)?(?:scholarships?|fellowships?|awards?|slots|seats|students)\b", s, re.I)
            if m and re.search(r"award|select|number|will be|per year|every year|annual|offer|grant", s, re.I):
                put("num_awards", F(m.group(1).replace(",", ""), clip(s, 300), d, o, "count-regex"))
                break
        if "num_awards" in out:
            break

    # --- dates ------------------------------------------------------------
    if hub and hub.get("opening"):
        put("opening_date", hub["opening"])
    if hub and hub.get("closing"):
        put("closing_date", hub["closing"])
    CL = r"last date|closing date|deadline|apply (?:by|before|until|till)|applications? (?:close|closes|closing|are open until|due)|open (?:till|until)|closes? on|any ?time before|last day"
    OP = r"opening date|applications? (?:open|opens|start|starts|commence|begin|will open)|open(?:s|ed)? (?:from|on)|starting from|available from|launch"
    for d in docs:
        for s, o in d.sents:
            for (dd, a, b) in find_dates(s):
                window = s[max(0, a - 90):b + 20]
                if "closing_date" not in out and re.search(CL, window, re.I) and not re.search(r"offer|reference|referee|nominat|interview|result|announce|commence|begin|start of|enrol|payment|disburs|submit(?:ted)? (?:the )?(?:report|certificate)|transcript|visa", window, re.I):
                    put("closing_date", F(dd.isoformat(), clip(s, 350), d, o, "date-near-trigger"))
                elif "opening_date" not in out and re.search(OP, window, re.I):
                    put("opening_date", F(dd.isoformat(), clip(s, 350), d, o, "date-near-trigger"))
    if "closing_date" not in out:
        for d in docs:
            h = sent_hit(d, r"applications? (?:for [^.]{0,90}?)?(?:are |is |have been |were )?(?:now )?closed\b|applications closed|no longer (?:accepting|open)|window (?:has )?closed")
            if h:
                put("closing_date", F("Applications closed (exact date not stated)", clip(h[0][0], 300), d, h[0][1], "closed-notice"))
                break
    if "closing_date" not in out:
        for d in docs:
            for s_, o in d.sents:
                m = re.search(r"((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+(20\d\d))\s+Applications? close", s_)
                if m:
                    mo = MONTHS[m.group(1)[:3].lower()]
                    put("closing_date", F(f"Month: {m.group(2)}-{mo:02d}", clip(m.group(0), 200), d, o + m.start(), "month-only"))
                    break
            if "closing_date" in out:
                break
    if "closing_date" not in out:
        for d in docs:
            h = sent_hit(d, r"rolling basis|open (?:throughout|all year|year[- ]round)|no (?:last|closing) date|applications? (?:are )?accepted (?:throughout|round the year|all year)")
            if h:
                put("closing_date", F("Rolling / open throughout the year", clip(h[0][0], 300), d, h[0][1], "rolling-phrase"))
                break

    # --- documents / selection / renewal ---------------------------------
    for field, hr, sr in [
        ("documents", r"document", r"documents?\s+(?:required|needed|to be (?:uploaded|submitted|attached|enclosed))|required documents|following documents|enclose"),
        ("selection", r"selection|criteria of selection|how .*selected", r"selection (?:process|criteria|committee|will be)|shortlist|merit list|selected (?:on the basis|based on|solely|by a)"),
        ("renewal", r"renew", r"renew|continuation|continue (?:the )?(?:scholarship|award)|subsequent year"),
    ]:
        for d in docs:
            r = find_in_sections(d, hr, 600)
            if r:
                put(field, F(clip(r[0], 600), clip(r[0], 600), d, r[1], "section-heading"))
                break
        if field not in out:
            for d in docs:
                h = sent_hit(d, sr)
                if h:
                    s, o = h[0]
                    q = clip(d.text[o:o + 520], 520)
                    put(field, F(q, q, d, o, "sentence-trigger"))
                    break
    return out
