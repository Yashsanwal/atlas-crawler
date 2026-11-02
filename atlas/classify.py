"""Source classification: decides whether a URL belongs to an OFFICIAL source and of what type.

Rule-based on purpose (TLD rules + an explicit registry of organisation domains). An LLM never
decides officialness. Unknown domains are 'unknown' and cannot be VERIFIED.
"""
import json, os, urllib.parse

CFG = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config", "sources.json")))
OFFICIAL_TYPES = {"government", "university", "corporate_csr", "foundation_trust", "international"}


def host(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _suffix_match(h, d):
    return h == d or h.endswith("." + d)


def classify(url):
    """-> dict(source_type, official(bool), org, rule)"""
    h = host(url)
    for d in CFG["aggregators"]:
        if _suffix_match(h, d):
            return {"source_type": "aggregator", "official": False, "org": d, "rule": "aggregator blocklist"}
    for d, v in CFG["registry"].items():
        if d.startswith("_"):
            continue
        org, st = v
        if _suffix_match(h, d):
            return {"source_type": st, "official": True, "org": org, "rule": "registry:" + d}
    for s in CFG["gov_suffixes"]:
        if h.endswith(s):
            return {"source_type": "government", "official": True, "org": h, "rule": "gov TLD " + s}
    for s in CFG["univ_suffixes"]:
        if h.endswith(s):
            return {"source_type": "university", "official": True, "org": h, "rule": "academic TLD " + s}
    return {"source_type": "unknown", "official": False, "org": h, "rule": "not in registry"}
