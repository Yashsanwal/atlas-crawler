"""Builds a MODIFIED COPY of the cached snapshots (data/snapshots -> data/snapshots_sim) so that a second crawl
run can demonstrate change detection and stale detection on demand. Real sources rarely change within a demo,
so this edits the *copy* only; the crawl run that consumes it is stored with mode='simulated' and every resulting
change row carries simulated=1 (the UI shows a SIMULATED SOURCE EDIT badge). Nothing live is touched.

Edits applied:
  1. NSP listing: closing date of 'AICTE - Swanath ... Technical Degree' 30-11-2026 -> 15-12-2026   (CHANGE)
  2. NSP listing: closing date of 'National Scholarship For Post Graduate Studies' 30-11-2026 -> 31-12-2026 (CHANGE)
  3. NSP listing: card for 'Ishan Uday Special Scholarship Scheme For NER' removed                    (REMOVED_FROM_SOURCE)
  4. Tata Trusts Means Grant PDF: '31st March 2027' -> '30th June 2027' (if cached)                  (CHANGE, non-government)
"""
import json, os, re, shutil, sys, glob
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from bs4 import BeautifulSoup
from atlas import fetch as F

SRC, DST = F.SNAP, os.path.join(os.path.dirname(F.SNAP), "snapshots_sim")
shutil.rmtree(DST, ignore_errors=True)
shutil.copytree(SRC, DST)
log = []
for p in glob.glob(DST + "/*.json"):
    d = json.load(open(p))
    if d["url"].endswith("scholarships.gov.in/All-Scholarships"):
        soup = BeautifulSoup(d["html"], "lxml")
        for row in soup.select("div.col-md-10"):
            h = row.find("h6")
            if not h:
                continue
            name = F.clean(h.get_text(" "))
            if name.startswith("AICTE - Swanath Scholarship Scheme ( Technical Degree)") or name.startswith("National Scholarship For Post Graduate Studies"):
                for sp in row.find_all("span"):
                    if "Application Open till" in F.clean(sp.get_text()):
                        new = "15-12-2026" if name.startswith("AICTE") else "31-12-2026"
                        sp.string = re.sub(r"\d\d-\d\d-\d{4}", new, F.clean(sp.get_text()))
                        log.append(f"closing date edited for {name[:50]} -> {new}")
            if name.startswith("Ishan Uday Special Scholarship"):
                (row.find_parent("div", class_="row") or row).decompose()
                log.append("card removed: " + name[:50])
        d["html"] = str(soup)
        d["title"], d["links"], d["text"] = F.parse_html(d["html"].encode(), d["final_url"])
        json.dump(d, open(p, "w"), ensure_ascii=False)
    elif "means-grant-programme.pdf" in d["url"] and "31st March 2027" in d["text"]:
        d["text"] = d["text"].replace("31st March 2027", "30th June 2027")
        json.dump(d, open(p, "w"), ensure_ascii=False)
        log.append("Tata Means Grant PDF: deadline text edited")
print("\n".join(log))
