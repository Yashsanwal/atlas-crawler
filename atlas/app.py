"""Read-only dashboard + JSON API over the SQLite repository.   Run: uvicorn atlas.app:app --port 8000"""
import os, json, datetime as dt
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from .db import connect

app = FastAPI(title="Atlas Scholarship Intelligence")
WEB = os.path.join(os.path.dirname(__file__), "..", "web")


def rows(sql, args=()):
    db = connect()
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


@app.get("/")
def index():
    return FileResponse(os.path.join(WEB, "index.html"))


@app.get("/api/stats")
def stats():
    one = lambda q: rows(q)[0]["n"]
    return {
        "total": one("select count(*) n from scholarships"),
        "verified": one("select count(*) n from scholarships where label='VERIFIED'"),
        "review": one("select count(*) n from scholarships where label!='VERIFIED'"),
        "by_status": {r["status"]: r["n"] for r in rows("select status, count(*) n from scholarships group by status")},
        "by_type": rows("select source_type, count(*) n, sum(label='VERIFIED') v from scholarships group by source_type"),
        "avg_conf": round(one("select coalesce(avg(confidence),0) n from scholarships"), 1),
        "avg_conf_verified": round(one("select coalesce(avg(confidence),0) n from scholarships where label='VERIFIED'"), 1),
        "ge95": one("select count(*) n from scholarships where confidence>=95"),
        "changes": one("select count(*) n from changes"),
        "recent_changes": rows("""select c.*, s.name from changes c join scholarships s on s.id=c.scholarship_id
                                  order by c.id desc limit 12"""),
        "runs": rows("select id,started_at,finished_at,mode,note,stats_json from runs order by id desc limit 10"),
        "leads": one("select count(*) n from leads"),
        "recently_updated": rows("select id,name,status,confidence,last_verified from scholarships order by last_verified desc limit 5"),
    }


@app.get("/api/scholarships")
def listing(q: str = "", status: str = "", label: str = "", type: str = "", sort: str = "confidence"):
    where, args = ["1=1"], []
    if q:
        where.append("(name like ? or provider like ? or eligibility like ? or category_criteria like ?)")
        args += [f"%{q}%"] * 4
    if status:
        where.append("status=?"); args.append(status)
    if label:
        where.append("label=?"); args.append(label)
    if type:
        where.append("source_type=?"); args.append(type)
    order = {"confidence": "confidence desc", "name": "name", "deadline": "closing_date", "verified": "last_verified desc"}.get(sort, "confidence desc")
    return rows(f"""select id,name,provider,source_type,amount,closing_date,status,label,confidence,last_verified,
                    (select count(*) from changes where scholarship_id=scholarships.id) nchanges
                    from scholarships where {' and '.join(where)} order by {order}, name""", args)


@app.get("/api/scholarships/{sid}")
def detail(sid: int):
    s = rows("select * from scholarships where id=?", (sid,))
    if not s:
        raise HTTPException(404)
    return {"scholarship": s[0],
            "evidence": rows("select * from evidence where scholarship_id=? order by id", (sid,)),
            "breakdown": rows("select * from score_breakdown where scholarship_id=? order by id", (sid,)),
            "changes": rows("select c.*, r.mode run_mode from changes c join runs r on r.id=c.run_id where scholarship_id=? order by c.id desc", (sid,))}


@app.get("/api/leads")
def leads():
    return rows("select * from leads order by id desc limit 200")
