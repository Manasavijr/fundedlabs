"""FundedLabs API (FastAPI) over the MySQL database.

  pip install -r requirements.txt
  uvicorn api.app:app --reload --port 8000        # from the FundedLabs folder
  open http://localhost:8000/docs                 # interactive list of every endpoint

MySQL connection: same MYSQL_* variables as ingest/pipeline.py. Every query response has the shape
{"rows": [...], "sql": "...", "ms": 12.3} so the UI can show which SQL produced what it displays and
how long it took (measured server-side around cursor.execute + fetchall).

`today` defaults to the server's current date; pass ?today=YYYY-MM-DD to pin it (the demo can use
the same @today as sql/queries.sql so app numbers match the saved experiment results).
"""

import os
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "api"))
from pipeline import connect  # noqa: E402
import queries as Q  # noqa: E402

app = FastAPI(title="FundedLabs API", version="1.0")
app.add_middleware(CORSMiddleware, allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
                   allow_methods=["*"], allow_headers=["*"])

Status = Literal["to_contact", "emailed", "replied", "meeting", "offer", "declined"]

_CACHE: dict = {}
CACHE_SECONDS = 300


def jsonable(v):
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, bytes):
        return v.decode("utf-8", "replace")
    return v


def run(sql: str, params: Optional[dict] = None, write: bool = False, cache: bool = False):
    """Execute one statement; return rows (list of dicts), the SQL text and measured ms."""
    key = (sql, tuple(sorted((params or {}).items()))) if cache else None
    if key and key in _CACHE and time.time() - _CACHE[key][0] < CACHE_SECONDS:
        return {**_CACHE[key][1], "cached": True}
    conn = connect()
    try:
        cur = conn.cursor()
        t = time.perf_counter()
        cur.execute(sql, params or {})
        rows = cur.fetchall() if cur.description else []
        ms = round((time.perf_counter() - t) * 1000, 2)
        cols = [c[0] for c in cur.description] if cur.description else []
        out = {"rows": [{c: jsonable(v) for c, v in zip(cols, r)} for r in rows], "sql": sql.strip(),
               "ms": ms, "params": {k: jsonable(v) for k, v in (params or {}).items()}}
        if write:
            out["rowcount"] = cur.rowcount
            out["lastrowid"] = cur.lastrowid
        if key:
            _CACHE[key] = (time.time(), out)
        return out
    finally:
        conn.close()


def today_or(d: Optional[date]) -> date:
    return d or date.today()


# --- Explore ---------------------------------------------------------------------------------------

@app.get("/api/states")
def states(today: Optional[date] = None):
    """Active awards and funding per state (the map)."""
    return run(Q.STATE_TOTALS, {"today": today_or(today)}, cache=True)


@app.get("/api/states/{state}/institutions")
def state_institutions(state: str, today: Optional[date] = None, limit: int = Query(30, le=200)):
    return run(Q.STATE_INSTITUTIONS, {"today": today_or(today), "state": state.upper(), "limit": limit})


@app.get("/api/feed")
def feed(today: Optional[date] = None, limit: int = Query(20, le=100)):
    """Most recently started awards (the live feed)."""
    return run(Q.RECENT_AWARDS, {"today": today_or(today), "limit": limit}, cache=True)


@app.get("/api/explore")
def explore(today: Optional[date] = None, state: Optional[str] = None, inst: Optional[str] = None,
            agency: Optional[str] = None, q: Optional[str] = None,
            limit: int = Query(50, le=200), offset: int = Query(0, ge=0)):
    """Active awards under the Explore filters, plus counters and the top-funded PIs.
    inst = comma-separated inst_ids."""
    inst_ids = [int(x) for x in inst.split(",") if x.strip().isdigit()] if inst else None
    where, p = Q.explore_where(state.upper() if state else None, inst_ids, agency, (q or "").strip() or None)
    p["today"] = today_or(today)
    awards = run(Q.explore_awards(where), {**p, "limit": limit, "offset": offset})
    ids = sorted({r["pi_id"] for r in awards["rows"] if r["pi_id"]})
    if ids:
        names = run(Q.INVESTIGATOR_NAMES.format(ids=",".join(str(int(i)) for i in ids)))
        nm = {r["inv_id"]: r["full_name"] for r in names["rows"]}
        for r in awards["rows"]:
            r["pi_name"] = nm.get(r["pi_id"])
    return {"awards": awards, "totals": run(Q.explore_totals(where), p, cache=True),
            "leaders": run(Q.explore_leaders(where), p, cache=True)}


@app.get("/api/institutions/top")
def top_institutions(today: Optional[date] = None, limit: int = Query(12, le=50)):
    return run(Q.TOP_INSTITUTIONS, {"today": today_or(today), "limit": limit}, cache=True)


# --- Search ----------------------------------------------------------------------------------------

@app.get("/api/search/institutions")
def search_institutions(q: str = Query(..., min_length=2)):
    return run(Q.INSTITUTION_SEARCH, {"q": q})


@app.get("/api/search/investigators")
def search_investigators(q: str = Query(..., min_length=2)):
    return run(Q.INVESTIGATOR_SEARCH, {"q": q})


# --- Institution (Q1, Q2) ------------------------------------------------------------------------------

@app.get("/api/institutions/{inst_id}")
def institution(inst_id: int, today: Optional[date] = None):
    info = run(Q.INSTITUTION, {"inst_id": inst_id})
    if not info["rows"]:
        raise HTTPException(404, "institution not found")
    p = {"inst_id": inst_id, "today": today_or(today)}
    return {"institution": info["rows"][0], "awards": run(Q.Q1, p), "investigators": run(Q.Q2, p)}


# --- Investigator profile (Q3, Q6) -----------------------------------------------------------------------

@app.get("/api/investigators/{inv_id}")
def investigator(inv_id: int, today: Optional[date] = None):
    info = run(Q.INVESTIGATOR, {"inv_id": inv_id})
    if not info["rows"]:
        raise HTTPException(404, "investigator not found")
    p = {"inv_id": inv_id, "today": today_or(today)}
    return {"investigator": info["rows"][0], "awards": run(Q.INVESTIGATOR_AWARDS, p),
            "funding_by_year": run(Q.INVESTIGATOR_FY, p),
            "publications": run(Q.Q3, p), "coinvestigators": run(Q.Q6, p)}


@app.get("/api/awards/{award_id}")
def award(award_id: int):
    info = run(Q.AWARD, {"award_id": award_id})
    if not info["rows"]:
        raise HTTPException(404, "award not found")
    p = {"award_id": award_id}
    return {"award": info["rows"][0], "people": run(Q.AWARD_PEOPLE, p), "funding": run(Q.AWARD_FUNDING, p)}


# --- Q4, Q5, Q7 ----------------------------------------------------------------------------------------

@app.get("/api/topic")
def topic(q: str = Query(..., min_length=2), fulltext: bool = False):
    """Q4. fulltext=true uses MATCH ... AGAINST and needs ft_award_text (sql/indexes.sql)."""
    try:
        return run(Q.Q4_FULLTEXT if fulltext else Q.Q4, {"topic": q}, cache=True)
    except Exception as e:
        if fulltext and "FULLTEXT" in str(e).upper():
            raise HTTPException(409, "ft_award_text index is not present")
        raise


@app.get("/api/ending")
def ending(months: int = Query(12, ge=1, le=120), today: Optional[date] = None):
    return run(Q.Q5, {"months": months, "today": today_or(today)})


@app.get("/api/programs")
def programs():
    return run(Q.PROGRAMS, cache=True)


@app.get("/api/programs/top")
def program_top(program: str):
    return run(Q.Q7, {"program": program})


# --- Outreach (Q8: insert, update, read) -----------------------------------------------------------------

class OutreachIn(BaseModel):
    inv_id: int
    award_id: Optional[int] = None
    status: Status = "to_contact"
    sent_on: Optional[date] = None
    follow_up_on: Optional[date] = None
    notes: Optional[str] = None


class OutreachPatch(BaseModel):
    status: Optional[Status] = None
    sent_on: Optional[date] = None
    follow_up_on: Optional[date] = None
    notes: Optional[str] = None


@app.get("/api/outreach")
def outreach(today: Optional[date] = None):
    return run(Q.Q8_READ, {"today": today_or(today)})


@app.get("/api/outreach/suggest")
def outreach_suggest(inst_id: int, today: Optional[date] = None):
    return run(Q.OUTREACH_SUGGEST, {"inst_id": inst_id, "today": today_or(today)})


@app.post("/api/outreach", status_code=201)
def outreach_add(body: OutreachIn):
    try:
        return run(Q.Q8_INSERT, body.model_dump(), write=True)
    except Exception as e:
        raise HTTPException(400, str(e))


@app.patch("/api/outreach/{outreach_id}")
def outreach_update(outreach_id: int, body: OutreachPatch):
    fields = body.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(400, "nothing to update")
    sets = ", ".join(f"{k} = %({k})s" for k in fields)  # keys come from the model, not the client
    sql = f"UPDATE outreach SET {sets} WHERE outreach_id = %(outreach_id)s"
    try:
        out = run(sql, {**fields, "outreach_id": outreach_id}, write=True)
    except Exception as e:
        raise HTTPException(400, str(e))
    if out["rowcount"] == 0:
        exists = run("SELECT 1 FROM outreach WHERE outreach_id = %(i)s", {"i": outreach_id})
        if not exists["rows"]:
            raise HTTPException(404, "outreach row not found")
    return out


@app.delete("/api/outreach/{outreach_id}")
def outreach_delete(outreach_id: int):
    out = run(Q.Q8_DELETE, {"outreach_id": outreach_id}, write=True)
    if out["rowcount"] == 0:
        raise HTTPException(404, "outreach row not found")
    return out


# --- Pipeline monitor ----------------------------------------------------------------------------------------

@app.get("/api/pipeline")
def pipeline(run_id: Optional[int] = None):
    runs = run(Q.RUNS)
    rid = run_id or (runs["rows"][0]["run_id"] if runs["rows"] else None)
    steps = run(Q.STEPS, {"run_id": rid}) if rid else {"rows": [], "sql": Q.STEPS.strip(), "ms": 0}
    return {"runs": runs, "run_id": rid, "steps": steps, "tables": run(Q.TABLE_SIZES, cache=True)}


# --- Ask: run any workload query with its plan ----------------------------------------------------------------

DEFAULTS = {"topic": "machine learning", "months": 12, "program": "NATIONAL CANCER INSTITUTE"}


@app.get("/api/ask")
def ask_list():
    return [{"id": k, **{f: v[f] for f in ("question", "params", "concepts")}, "sql": v["sql"].strip()}
            for k, v in Q.ASK.items()]


@app.get("/api/ask/{qid}")
def ask(qid: str, inst_id: Optional[int] = None, inv_id: Optional[int] = None, topic: Optional[str] = None,
        months: Optional[int] = None, program: Optional[str] = None, today: Optional[date] = None,
        analyze: bool = False):
    """Run Q1-Q8 and return rows, SQL, time and EXPLAIN FORMAT=TREE (or EXPLAIN ANALYZE if analyze=true)."""
    spec = Q.ASK.get(qid.upper())
    if not spec:
        raise HTTPException(404, "unknown question")
    given = {"inst_id": inst_id, "inv_id": inv_id, "topic": topic, "months": months, "program": program}
    p = {"today": today_or(today)}
    for name in spec["params"]:
        val = given[name] if given[name] is not None else DEFAULTS.get(name)
        if val is None:
            raise HTTPException(400, f"{qid} needs ?{name}=")
        p[name] = val
    result = run(spec["sql"], p)
    plan = run(("EXPLAIN ANALYZE " if analyze else "EXPLAIN FORMAT=TREE ") + spec["sql"], p)
    result["plan"] = "\n".join(next(iter(r.values())) for r in plan["rows"])
    result["plan_kind"] = "EXPLAIN ANALYZE" if analyze else "EXPLAIN FORMAT=TREE"
    result["question"] = spec["question"]
    result["concepts"] = spec["concepts"]
    return result


@app.get("/api/health")
def health():
    out = run("SELECT VERSION() AS version, DATABASE() AS db, (SELECT COUNT(*) FROM award) AS awards")
    return out["rows"][0]
