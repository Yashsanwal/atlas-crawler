"""HTTP fetching with polite delays, robots.txt awareness, TLS fallback and a snapshot cache.

Every fetched document is cached on disk (data/snapshots/<sha1>.json) so that a run can be
replayed offline (`--offline`) and so that evidence can always be re-inspected.
"""
import hashlib, json, re, time, io, urllib.robotparser, urllib.parse, os
import requests
from bs4 import BeautifulSoup

UA = "AtlasFundingBot/0.1 (+student-research; scholarship intelligence crawler)"
SNAP = os.path.join(os.path.dirname(__file__), "..", "data", "snapshots")
_robots = {}
_last = {}


def _key(url):
    return hashlib.sha1(url.encode()).hexdigest()


def snap_path(url):
    return os.path.join(SNAP, _key(url) + ".json")


def load_snapshot(url):
    p = snap_path(url)
    if os.path.exists(p):
        return json.load(open(p))
    return None


def save_snapshot(doc):
    os.makedirs(SNAP, exist_ok=True)
    json.dump(doc, open(snap_path(doc["url"]), "w"), ensure_ascii=False)


def allowed(url):
    host = urllib.parse.urlparse(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            r = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=8)
            rp.parse(r.text.splitlines() if r.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def pdf_text(content):
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(content))
    return "\n".join((p.extract_text() or "") for p in r.pages)


def pdf_scan_ratio(content):
    """Fraction of pages dominated by a large raster image (i.e. a scan carrying an OCR text layer)."""
    from pypdf import PdfReader
    r = PdfReader(io.BytesIO(content))
    scanned = 0
    for p in r.pages:
        try:
            xo = p["/Resources"].get_object().get("/XObject")
            big = False
            for k in (xo.get_object() if xo else {}):
                o = xo[k].get_object()
                if o.get("/Subtype") == "/Image" and int(o.get("/Width", 0)) * int(o.get("/Height", 0)) > 400000:
                    big = True
            scanned += big
        except Exception:
            pass
    return scanned / max(1, len(r.pages))


def clean(t):
    return re.sub(r"\s+", " ", t).strip()


def parse_html(content, base_url):
    """-> (title, links, visible_text). Links are collected before boilerplate (nav/header/footer) is stripped from the text."""
    soup = BeautifulSoup(content, "lxml")
    for t in soup(["script", "style", "noscript"]):
        t.decompose()
    title = clean(soup.title.get_text()) if soup.title else ""
    links = [{"text": clean(a.get_text(" "))[:200], "href": urllib.parse.urljoin(base_url, a["href"])}
             for a in soup.find_all("a", href=True) if not a["href"].startswith(("javascript:", "mailto:", "#"))]
    for t in soup(["nav", "header", "footer", "aside", "form"]):
        t.decompose()
    for t in soup.find_all(attrs={"class": re.compile(r"(^|\s)(nav|navbar|menu|breadcrumb|cookie|dropdown|footer|header)(\s|$|-)", re.I)}):
        t.decompose()
    return title, links, clean(soup.get_text(" "))


def fetch(url, offline=False, delay=1.0, use_cache_if_offline=True):
    """Return {url, final_url, status, kind, title, text, links, fetched_at, ok}."""
    if offline:
        d = load_snapshot(url)
        if d:
            return d
        return {"url": url, "ok": False, "status": 0, "error": "not in snapshot cache"}
    host = urllib.parse.urlparse(url).netloc
    wait = delay - (time.time() - _last.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last[host] = time.time()
    if not allowed(url):
        return {"url": url, "ok": False, "status": 0, "error": "blocked by robots.txt"}
    r = None
    for verify, attempt in [(True, 0), (True, 1), (True, 2), (False, 3)]:
        if attempt:
            time.sleep(3 * attempt)   # transient DNS / connection failures: back off and retry
        try:
            r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-IN,en"},
                             timeout=(10, 20), verify=verify, allow_redirects=True, stream=True)
            body, t0 = b"", time.time()
            for chunk in r.iter_content(65536):   # hard 45s / 25MB ceiling per document
                body += chunk
                if time.time() - t0 > 45 or len(body) > 25_000_000:
                    break
            r._content = body
            break
        except requests.exceptions.SSLError:
            if attempt < 3:
                continue
        except Exception as e:
            if attempt < 2:
                continue
            return {"url": url, "ok": False, "status": 0, "error": str(e)[:200]}
    if r is None:
        return {"url": url, "ok": False, "status": 0, "error": "ssl failure"}
    ctype = r.headers.get("content-type", "").lower()
    doc = {"url": url, "final_url": r.url, "status": r.status_code, "ok": r.status_code == 200,
           "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "links": [], "title": "", "text": "",
           "kind": "html"}
    if r.status_code != 200:
        return doc
    if "pdf" in ctype or url.lower().endswith(".pdf"):
        doc["kind"] = "pdf"
        try:
            doc["text"] = clean(pdf_text(r.content))
            doc["scan_ratio"] = round(pdf_scan_ratio(r.content), 2)
        except Exception as e:
            doc["ok"] = False
            doc["error"] = "pdf parse: " + str(e)[:100]
    else:
        doc["title"], doc["links"], doc["text"] = parse_html(r.content, r.url)
        doc["html"] = r.text if len(r.text) < 1_500_000 else ""
    save_snapshot(doc)
    return doc
