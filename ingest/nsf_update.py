"""Incremental NSF update: fetch recently made awards from the NSF Award Search API and upsert them.

  python ingest/nsf_update.py --dry-run             # fetch + parse, show what would change, write nothing
  python ingest/nsf_update.py                       # awards made in the last 30 days
  python ingest/nsf_update.py --since 2026-10-01    # awards made on or after a date
  python ingest/nsf_update.py --max 200             # cap how many awards are fetched

This is the incremental half of acquisition (the historical half is pipeline.py). One run:
  1. fetch    GET https://api.nsf.gov/services/v1/awards.json, 25 awards per page
  2. parse    map each award to the same fields parse_nsf.py produces from the bulk files
  3. upsert   inside ONE transaction:
                institution        match on the bulk load's (name, city, state) key, else INSERT
                award              uq_award_source (agency, award no): UPDATE if present, else INSERT
                award_funding      replace the award's per-fiscal-year rows
                investigator       match a person already on the award, else name_norm at the same
                                   institution, else INSERT (the API's piId is not the bulk nsf_id)
                award_investigator INSERT ... ON DUPLICATE KEY UPDATE role
  4. log      one ingest_run row (source 'nsf_api') and one ingest_step row per step, so the app's
              Pipeline tab shows the run next to the bulk load.

A failure rolls the whole transaction back and marks the run 'failed'. Running it twice is safe:
the second run finds every award already present and only updates it.
"""

import argparse
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (ROOT, clean_text, fiscal_year, inst_key, log, parse_amount, parse_date,  # noqa: E402
                    person_name_norm, split_last_first)
from parse_nsf import clean_abstract  # noqa: E402
from pipeline import connect  # noqa: E402

API = "https://api.nsf.gov/services/v1/awards.json"
FIELDS = ("id,title,abstractText,agency,date,startDate,expDate,estimatedTotalAmt,fundsObligatedAmt,"
          "fundsObligated,awardeeName,awardeeCity,awardeeStateCode,awardeeCountryCode,piFirstName,"
          "piLastName,pdPIName,coPDPI,orgLongName,orgLongName2,divAbbr,fundProgramName,primaryProgram")
PAGE = 25                      # the API's maximum results per page
NSF_AGENCY_ID = 2
RAW_LOG = ROOT / "data" / "raw" / "nsf_api"


# ---- fetch -----------------------------------------------------------------------------------------

def fetch(since, until, max_awards):
    awards, offset, seen = [], 1, set()
    while len(awards) < max_awards:
        params = {"dateStart": since.strftime("%m/%d/%Y"), "dateEnd": until.strftime("%m/%d/%Y"),
                  "printFields": FIELDS, "rpp": PAGE, "offset": offset}
        req = urllib.request.Request(f"{API}?{urllib.parse.urlencode(params)}",
                                     headers={"User-Agent": "FundedLabs/1.0", "Accept": "application/json"})
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    body = json.load(r)
                break
            except OSError as e:
                if attempt == 3:
                    raise
                log(f"  retry after {e}")
                time.sleep(2 ** attempt)
        resp = body.get("response", {})
        if resp.get("serviceNotification"):
            log(f"  API notice: {resp['serviceNotification']}")
        page = resp.get("award", [])
        awards.extend(a for a in page if a.get("id") not in seen)   # pages can overlap
        seen.update(a.get("id") for a in page)
        log(f"  page at offset {offset}: {len(page)} awards")
        if len(page) < PAGE:
            break
        offset += PAGE
    return awards[:max_awards]


# ---- parse -----------------------------------------------------------------------------------------

def _person(full, first=None, last=None, role="PI"):
    if not (first and last):
        last, first = split_last_first(full)
    return {"full_name": clean_text(full) or clean_text(f"{first} {last}"),
            "norm": person_name_norm(last, first), "role": role}


def _co_pi(entry):
    """coPDPI entries look like 'Jane Q Doe jdoe@univ.edu': the name, then an email (no NSF person id)."""
    name = " ".join(t for t in str(entry).split() if "@" not in t)
    return _person(name, role="CO_PI")


def _funding(raw, start, total):
    """fundsObligated entries look like 'FY 2026 = $150,000.00'."""
    out = {}
    for item in raw or []:
        m = re.search(r"(\d{4})\D+?([\d,]+(?:\.\d+)?)", str(item))
        if m:
            fy = int(m.group(1))
            out[fy] = out.get(fy, Decimal(0)) + (parse_amount(m.group(2)) or Decimal(0))
    if not out and start and total is not None:
        out[fiscal_year(start)] = total        # same fallback as parse_nsf.py
    return out


def parse(doc):
    start, end = parse_date(doc.get("startDate")), parse_date(doc.get("expDate"))
    total = parse_amount(doc.get("fundsObligatedAmt")) or parse_amount(doc.get("estimatedTotalAmt"))
    # piId is not used: it is a different identifier from the bulk files' nsf_id and never matches it
    pis = []
    if doc.get("piLastName") or doc.get("pdPIName"):
        pis.append(_person(doc.get("pdPIName"), doc.get("piFirstName"), doc.get("piLastName")))
    pis += [_co_pi(e) for e in doc.get("coPDPI") or []]
    return {
        "source_award_no": clean_text(doc.get("id")),
        "title": clean_text(doc.get("title"), 600),
        "abstract": clean_abstract(doc.get("abstractText")),
        "program": clean_text(doc.get("orgLongName2") or doc.get("orgLongName")
                              or doc.get("fundProgramName") or doc.get("primaryProgram"), 255),
        "start": start, "end": end, "total_amount": total,
        "inst": (clean_text(doc.get("awardeeName"), 255), clean_text(doc.get("awardeeCity"), 100) or "",
                 (clean_text(doc.get("awardeeStateCode")) or "")[:2], clean_text(doc.get("awardeeCountryCode"), 100)),
        "pis": [p for p in pis if p["norm"]],
        "funding": _funding(doc.get("fundsObligated"), start, total),
    }


def check(a):
    if not a["source_award_no"]:
        return "no_award_id"
    if not a["title"]:
        return "no_title"
    if not a["inst"][0]:
        return "no_institution"
    if a["start"] and a["end"] and a["end"] < a["start"]:
        return "end_before_start"
    if a["total_amount"] is not None and a["total_amount"] < 0:
        return "negative_amount"
    return None


# ---- upsert ----------------------------------------------------------------------------------------

class Upserter:
    def __init__(self, cur, dry_run):
        self.cur, self.dry = cur, dry_run
        self.stats = {"institutions_new": 0, "awards_new": 0, "awards_updated": 0, "awards_unchanged": 0,
                      "funding_rows": 0, "investigators_new": 0, "investigators_matched": 0, "links": 0}
        self.changes = []
        cur.execute("SELECT inst_id, name, city, state FROM institution")
        self.inst = {inst_key(n, c, s): i for i, n, c, s in cur.fetchall()}
        self.next_id = {}
        for table, col in (("institution", "inst_id"), ("award", "award_id"), ("investigator", "inv_id")):
            cur.execute(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table}")
            self.next_id[table] = cur.fetchone()[0]

    def _new_id(self, table):
        self.next_id[table] += 1
        return self.next_id[table] - 1

    def _x(self, sql, args=()):
        if not self.dry:
            self.cur.execute(sql, args)

    def institution(self, name, city, state, country):
        key = inst_key(name, city, state)
        if key in self.inst:
            return self.inst[key]
        iid = self.inst[key] = self._new_id("institution")
        self._x("INSERT INTO institution (inst_id, name, city, state, country) VALUES (%s,%s,%s,%s,%s)",
                (iid, name, city, state, country))
        self.stats["institutions_new"] += 1
        return iid

    def award(self, a, inst_id):
        cols = ("inst_id", "title", "abstract", "program", "start_date", "end_date", "total_amount")
        vals = (inst_id, a["title"], a["abstract"], a["program"], a["start"], a["end"], a["total_amount"])
        self.cur.execute(f"SELECT award_id, {', '.join(cols)} FROM award WHERE agency_id = %s AND source_award_no = %s",
                         (NSF_AGENCY_ID, a["source_award_no"]))
        row = self.cur.fetchone()
        if row is None:
            aid = self._new_id("award")
            self._x("INSERT INTO award (award_id, agency_id, inst_id, source_award_no, title, abstract, department, "
                    "program, start_date, end_date, total_amount) VALUES (%s,%s,%s,%s,%s,%s,NULL,%s,%s,%s,%s)",
                    (aid, NSF_AGENCY_ID, inst_id, a["source_award_no"], a["title"], a["abstract"], a["program"],
                     a["start"], a["end"], a["total_amount"]))
            self.stats["awards_new"] += 1
            self.changes.append(("new", a["source_award_no"], a["title"]))
            return aid
        aid, old = row[0], row[1:]
        diff = [c for c, o, n in zip(cols, old, vals) if (o if not isinstance(o, Decimal) else Decimal(o)) != n]
        if diff:
            self._x(f"UPDATE award SET {', '.join(c + ' = %s' for c in cols)} WHERE award_id = %s", (*vals, aid))
            self.stats["awards_updated"] += 1
            self.changes.append(("updated", a["source_award_no"], ", ".join(diff)))
        else:
            self.stats["awards_unchanged"] += 1
        return aid

    def funding(self, aid, funding):
        self._x("DELETE FROM award_funding WHERE award_id = %s", (aid,))
        for fy, amt in sorted(funding.items()):
            if 1950 <= fy <= 2100:
                self._x("INSERT INTO award_funding (award_id, fiscal_year, amount) VALUES (%s,%s,%s)", (aid, fy, amt))
                self.stats["funding_rows"] += 1

    def investigator(self, p, inst_id, aid):
        # 1. someone already linked to this award with the same name (co-PIs are often at another
        #    institution; the suffix match covers compound surnames, e.g. 'obristfarner|jonathan')
        self.cur.execute("SELECT v.inv_id FROM award_investigator ai JOIN investigator v USING (inv_id) "
                         "WHERE ai.award_id = %s AND (v.name_norm = %s OR v.name_norm LIKE CONCAT('%%', %s)) LIMIT 2",
                         (aid, p["norm"], p["norm"]))
        rows = self.cur.fetchall()
        row = rows[0] if len(rows) == 1 else None
        if row is None:
            # 2. as in the bulk load: normalized name (or surname suffix) + same institution, only if unambiguous
            self.cur.execute("SELECT inv_id FROM investigator WHERE inst_id = %s "
                             "AND (name_norm = %s OR name_norm LIKE CONCAT('%%', %s)) LIMIT 2",
                             (inst_id, p["norm"], p["norm"]))
            rows = self.cur.fetchall()
            row = rows[0] if len(rows) == 1 else None
        if row is not None:
            self.stats["investigators_matched"] += 1
            return row[0]
        # 3. a new person (no NSF id: the API does not give the id the bulk files use)
        vid = self._new_id("investigator")
        self._x("INSERT INTO investigator (inv_id, full_name, name_norm, nih_profile_id, nsf_id, inst_id) "
                "VALUES (%s,%s,%s,NULL,NULL,%s)", (vid, p["full_name"][:255], p["norm"][:160], inst_id))
        self.stats["investigators_new"] += 1
        return vid

    def link(self, aid, vid, role):
        self._x("INSERT INTO award_investigator (award_id, inv_id, role) VALUES (%s,%s,%s) "
                "ON DUPLICATE KEY UPDATE role = IF(role = 'PI', 'PI', VALUES(role))", (aid, vid, role))
        self.stats["links"] += 1


# ---- main ------------------------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", type=date.fromisoformat, default=date.today() - timedelta(days=30),
                    help="earliest award date (YYYY-MM-DD); default 30 days ago")
    ap.add_argument("--until", type=date.fromisoformat, default=date.today())
    ap.add_argument("--max", type=int, default=500, help="most awards to fetch (default 500)")
    ap.add_argument("--dry-run", action="store_true", help="fetch and compare, write nothing")
    args = ap.parse_args()

    steps = []

    def step(name, rows_in, rows_out, t0):
        steps.append((name, rows_in, rows_out, int((time.perf_counter() - t0) * 1000)))
        log(f"[{name}] rows_in={rows_in} rows_out={rows_out} {steps[-1][3]} ms")

    t = time.perf_counter()
    log(f"NSF awards made {args.since} .. {args.until}")
    raw = fetch(args.since, args.until, args.max)
    step("nsf_api_1_fetch", None, len(raw), t)
    RAW_LOG.mkdir(parents=True, exist_ok=True)
    (RAW_LOG / f"awards_{args.since}_{args.until}.json").write_text(json.dumps(raw, indent=1))

    t = time.perf_counter()
    parsed, rejected = [], {}
    for doc in raw:
        a = parse(doc)
        why = check(a)
        if why:
            rejected[why] = rejected.get(why, 0) + 1
        else:
            parsed.append(a)
    step("nsf_api_2_parse", len(raw), len(parsed), t)
    if rejected:
        log(f"  rejected: {rejected}")

    conn = connect()
    conn.autocommit(False)
    cur = conn.cursor()
    run_id = None
    if not args.dry_run:
        cur.execute("INSERT INTO ingest_run (source, started_at, status) VALUES ('nsf_api', NOW(3), 'running')")
        run_id = cur.lastrowid
        conn.commit()
    try:
        up = Upserter(cur, args.dry_run)
        t = time.perf_counter()
        before = up.stats["institutions_new"]
        inst_ids = [up.institution(*a["inst"]) for a in parsed]
        step("nsf_api_3_institutions", len(parsed), up.stats["institutions_new"] - before, t)

        t = time.perf_counter()
        award_ids = []
        for a, iid in zip(parsed, inst_ids):
            aid = up.award(a, iid)
            up.funding(aid, a["funding"])
            award_ids.append(aid)
        step("nsf_api_4_awards", len(parsed), up.stats["awards_new"] + up.stats["awards_updated"], t)

        t = time.perf_counter()
        n_people = 0
        for a, iid, aid in zip(parsed, inst_ids, award_ids):
            for p in a["pis"]:
                n_people += 1
                up.link(aid, up.investigator(p, iid, aid), p["role"])
        step("nsf_api_5_investigators", n_people, up.stats["investigators_new"] + up.stats["investigators_matched"], t)

        if args.dry_run:
            conn.rollback()
        else:
            for name, rows_in, rows_out, ms in steps:
                cur.execute("INSERT INTO ingest_step (run_id, step, rows_in, rows_out, duration_ms) "
                            "VALUES (%s,%s,%s,%s,%s)", (run_id, name, rows_in, rows_out, ms))
            cur.execute("UPDATE ingest_run SET status = 'success', finished_at = NOW(3) WHERE run_id = %s", (run_id,))
            conn.commit()
    except Exception:
        conn.rollback()
        if run_id is not None:
            cur.execute("UPDATE ingest_run SET status = 'failed', finished_at = NOW(3) WHERE run_id = %s", (run_id,))
            conn.commit()
        raise
    finally:
        conn.close()

    print(("DRY RUN, nothing written. " if args.dry_run else f"ingest_run {run_id}: ") + json.dumps(up.stats))
    for kind, no, what in up.changes[:15]:
        print(f"  {kind:8} {no}  {what[:90]}")
    if len(up.changes) > 15:
        print(f"  ... {len(up.changes) - 15} more")


if __name__ == "__main__":
    main()
