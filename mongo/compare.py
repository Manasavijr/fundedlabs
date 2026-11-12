"""Four operations run in MongoDB and in MySQL on the SAME subset, side by side.

  python mongo/compare.py                    # all four, default subset (CA, end_date >= 2021-10-01)
  python mongo/compare.py --runs 9 --topic "gene therapy"

Run mongo/build_documents.py first; --state/--since default to the subset it exported
(read from mongo/results/build.json). The SQL side of every
operation is restricted to the same subset, so both systems answer the same question over the
same awards and the results can be checked against each other.

  1. Keyword search     Mongo $text index (stemmed, inverted index)  vs  SQL LIKE '%term%'
                        (and MATCH ... AGAINST if ft_award_text exists in MySQL)
  2. Funding by year    $unwind + $group pipeline                    vs  JOIN + GROUP BY
  3. Co-PI pairs        $unwind the embedded array twice             vs  self-join of award_investigator
  4. Rename a person    update every document that embeds them       vs  UPDATE one investigator row
                        (both reverted afterwards; SQL runs inside a rolled-back transaction)

For each: result check (do both systems return the same answer?), median and spread of --runs
timed runs after one warm-up, the MongoDB explain summary and the MySQL EXPLAIN ANALYZE.
Writes mongo/results/compare.json and mongo/results/compare.md.

Timings are measured wall-clock times from this script on this machine only. MongoDB holds the
subset; MySQL holds the full database and filters to the subset, so timings are not a fair
engine-vs-engine benchmark.
"""

import argparse
import json
import statistics
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
sys.path.insert(0, str(ROOT / "mongo"))
from pipeline import connect  # noqa: E402
from build_documents import mongo_db  # noqa: E402

RESULTS = ROOT / "mongo" / "results"

SUBSET_SQL = "i.state = %(state)s AND a.end_date >= %(since)s"


def timed(fn, runs):
    fn()  # warm-up
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        fn()
        times.append((time.perf_counter() - t) * 1000)
    return {"median_ms": round(statistics.median(times), 2), "min_ms": round(min(times), 2),
            "max_ms": round(max(times), 2), "runs": runs}


def num(v):
    """Decimal128 / Decimal / float -> Decimal rounded to cents, for comparing sums."""
    if v is None:
        return None
    if hasattr(v, "to_decimal"):
        v = v.to_decimal()
    return Decimal(str(v)).quantize(Decimal("0.01"))


def sql_rows(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchall()


def sql_explain(cur, sql, params):
    try:
        cur.execute("EXPLAIN ANALYZE " + sql, params)
        return "\n".join(r[0] for r in cur.fetchall())
    except Exception as e:
        return f"(EXPLAIN ANALYZE failed: {e})"


def summarize_mongo_explain(exp):
    """Pull the useful parts (plan stages, docs and keys examined) out of a MongoDB explain document."""
    out = {"stages": [], "totalDocsExamined": None, "totalKeysExamined": None, "nReturned": None}

    def walk(node):
        if isinstance(node, dict):
            if "stage" in node and node["stage"] not in out["stages"]:
                out["stages"].append(node["stage"])
            for k in ("totalDocsExamined", "totalKeysExamined", "nReturned"):
                if k in node and out[k] is None:
                    out[k] = node[k]
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(exp)
    return out


def mongo_agg_explain(db, coll, pipeline):
    try:
        exp = db.command("explain", {"aggregate": coll, "pipeline": pipeline, "cursor": {}},
                         verbosity="executionStats")
        return summarize_mongo_explain(exp)
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------------------------
# 1. Keyword search
# ---------------------------------------------------------------------------------------------

def op_keyword(db, cur, p, runs):
    topic = p["topic"]
    mfilter = {"$text": {"$search": f'"{topic}"'}}
    m_ids = sorted(d["_id"] for d in db.awards.find(mfilter, {"_id": 1}))
    sql_like = f"""SELECT a.award_id FROM award a JOIN institution i ON i.inst_id = a.inst_id
                   WHERE {SUBSET_SQL}
                     AND (a.title LIKE CONCAT('%%', %(topic)s, '%%') OR a.abstract LIKE CONCAT('%%', %(topic)s, '%%'))"""
    s_ids = sorted(r[0] for r in sql_rows(cur, sql_like, p))
    res = {
        "question": f"Which awards in the subset mention '{topic}'?",
        "mongo": {"query": f"db.awards.find({{$text: {{$search: '\"{topic}\"'}}}})",
                  "matches": len(m_ids), "timing": timed(lambda: list(db.awards.find(mfilter, {"_id": 1})), runs)},
        "mysql_like": {"query": sql_like.strip(), "matches": len(s_ids),
                       "timing": timed(lambda: sql_rows(cur, sql_like, p), runs),
                       "explain_analyze": sql_explain(cur, sql_like, p)},
        "overlap": {"both": len(set(m_ids) & set(s_ids)), "mongo_only": len(set(m_ids) - set(s_ids)),
                    "mysql_only": len(set(s_ids) - set(m_ids)),
                    "mongo_only_examples": sorted(set(m_ids) - set(s_ids))[:5],
                    "mysql_only_examples": sorted(set(s_ids) - set(m_ids))[:5]},
    }
    try:
        res["mongo"]["explain"] = summarize_mongo_explain(
            db.awards.find(mfilter, {"_id": 1}).explain())
    except Exception as e:
        res["mongo"]["explain"] = {"error": str(e)}

    cur.execute("""SELECT COUNT(*) FROM information_schema.STATISTICS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'award' AND INDEX_NAME = 'ft_award_text'""")
    if cur.fetchone()[0]:
        sql_ft = f"""SELECT a.award_id FROM award a JOIN institution i ON i.inst_id = a.inst_id
                     WHERE {SUBSET_SQL}
                       AND MATCH(a.title, a.abstract) AGAINST (CONCAT('"', %(topic)s, '"') IN BOOLEAN MODE)"""
        f_ids = sorted(r[0] for r in sql_rows(cur, sql_ft, p))
        res["mysql_fulltext"] = {"query": sql_ft.strip(), "matches": len(f_ids),
                                 "timing": timed(lambda: sql_rows(cur, sql_ft, p), runs),
                                 "explain_analyze": sql_explain(cur, sql_ft, p),
                                 "same_set_as_mongo": f_ids == m_ids}
    else:
        res["mysql_fulltext"] = "skipped: ft_award_text not present (it exists only during experiment E6)"
    res["note"] = ("Results can differ by design: $text stems words and ignores case/punctuation "
                   "(e.g. 'learning' also matches 'learn'), LIKE matches the exact substring.")
    return res


# ---------------------------------------------------------------------------------------------
# 2. Funding by fiscal year and agency
# ---------------------------------------------------------------------------------------------

def op_funding(db, cur, p, runs):
    pipeline = [
        {"$unwind": "$funding"},
        {"$match": {"funding.fiscal_year": {"$gte": 2016, "$lte": 2026}}},
        {"$group": {"_id": {"fy": "$funding.fiscal_year", "agency": "$agency"},
                    "awards": {"$sum": 1}, "funding": {"$sum": "$funding.amount"}}},
        {"$sort": {"_id.fy": 1, "_id.agency": 1}},
    ]
    sql = f"""SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
              FROM award a
              JOIN institution i   ON i.inst_id = a.inst_id
              JOIN award_funding f ON f.award_id = a.award_id
              JOIN agency g        ON g.agency_id = a.agency_id
              WHERE {SUBSET_SQL} AND f.fiscal_year BETWEEN 2016 AND 2026
              GROUP BY f.fiscal_year, g.code
              ORDER BY f.fiscal_year, g.code"""
    m = [(d["_id"]["fy"], d["_id"]["agency"], d["awards"], num(d["funding"]))
         for d in db.awards.aggregate(pipeline)]
    s = [(r[0], r[1], r[2], num(r[3]) if r[3] is not None else num(0)) for r in sql_rows(cur, sql, p)]
    return {
        "question": "How much funding went to the subset per fiscal year and agency?",
        "mongo": {"pipeline": pipeline, "groups": len(m),
                  "timing": timed(lambda: list(db.awards.aggregate(pipeline)), runs),
                  "explain": mongo_agg_explain(db, "awards", pipeline)},
        "mysql": {"query": sql.strip(), "groups": len(s), "timing": timed(lambda: sql_rows(cur, sql, p), runs),
                  "explain_analyze": sql_explain(cur, sql, p)},
        "results_match": m == s,
        "rows": [{"fiscal_year": r[0], "agency": r[1], "awards": r[2], "funding": str(r[3])} for r in s],
        "mismatches": [{"mongo": str(a), "mysql": str(b)} for a, b in zip(m, s) if a != b][:5],
    }


# ---------------------------------------------------------------------------------------------
# 3. Co-investigator pairs
# ---------------------------------------------------------------------------------------------

def op_pairs(db, cur, p, runs):
    pairs_stages = [
        {"$match": {"investigators.1": {"$exists": True}}},
        {"$project": {"a": "$investigators.inv_id", "b": "$investigators.inv_id"}},
        {"$unwind": "$a"},
        {"$unwind": "$b"},
        {"$match": {"$expr": {"$lt": ["$a", "$b"]}}},
        {"$group": {"_id": {"a": "$a", "b": "$b"}, "shared": {"$sum": 1}}},
    ]
    top = pairs_stages + [{"$sort": {"shared": -1, "_id.a": 1, "_id.b": 1}}, {"$limit": 20}]
    total = pairs_stages + [{"$count": "pairs"}]
    sql_pairs = f"""FROM award_investigator x
              JOIN award_investigator y ON y.award_id = x.award_id AND x.inv_id < y.inv_id
              JOIN award a              ON a.award_id = x.award_id
              JOIN institution i        ON i.inst_id = a.inst_id
              WHERE {SUBSET_SQL}
              GROUP BY x.inv_id, y.inv_id"""
    sql_top = f"""SELECT x.inv_id AS a, y.inv_id AS b, COUNT(*) AS shared
              {sql_pairs}
              ORDER BY shared DESC, a, b
              LIMIT 20"""
    sql_total = f"SELECT COUNT(*) FROM (SELECT 1 {sql_pairs}) t"
    m = [(d["_id"]["a"], d["_id"]["b"], d["shared"]) for d in db.awards.aggregate(top)]
    s = [tuple(r) for r in sql_rows(cur, sql_top, p)]
    m_total = next(iter(db.awards.aggregate(total)), {"pairs": 0})["pairs"]
    s_total = sql_rows(cur, sql_total, p)[0][0]
    return {
        "question": "Which pairs of investigators share the most awards in the subset?",
        "mongo": {"pipeline": top, "distinct_pairs": m_total,
                  "timing": timed(lambda: list(db.awards.aggregate(top)), runs),
                  "explain": mongo_agg_explain(db, "awards", top)},
        "mysql": {"query": sql_top.strip(), "distinct_pairs": s_total,
                  "timing": timed(lambda: sql_rows(cur, sql_top, p), runs),
                  "explain_analyze": sql_explain(cur, sql_top, p)},
        "results_match": m == s and m_total == s_total,
        "top_pairs": [{"inv_a": r[0], "inv_b": r[1], "shared_awards": r[2]} for r in s[:10]],
    }


# ---------------------------------------------------------------------------------------------
# 4. Rename an investigator who is embedded in many documents
# ---------------------------------------------------------------------------------------------

def op_update(db, cur, conn, p, runs):
    top = list(db.awards.aggregate([
        {"$unwind": "$investigators"},
        {"$group": {"_id": "$investigators.inv_id", "docs": {"$sum": 1},
                    "name": {"$first": "$investigators.full_name"}}},
        {"$sort": {"docs": -1, "_id": 1}}, {"$limit": 1}]))
    if not top:
        return {"skipped": "no investigators in the subset"}
    inv_id, old_name = top[0]["_id"], top[0]["name"]
    new_name = old_name + " (renamed)"
    filt = {"investigators.inv_id": inv_id}

    def mongo_set(name):
        return db.awards.update_many(filt, {"$set": {"investigators.$[p].full_name": name}},
                                     array_filters=[{"p.inv_id": inv_id}])

    r = mongo_set(new_name)
    docs_matched, docs_modified = r.matched_count, r.modified_count
    still_old = db.awards.count_documents({"investigators": {"$elemMatch": {"inv_id": inv_id, "full_name": old_name}}})
    mongo_set(old_name)

    def mongo_cycle():
        mongo_set(new_name)
        mongo_set(old_name)

    m_timing = timed(mongo_cycle, runs)

    sql_upd = "UPDATE investigator SET full_name = %(name)s WHERE inv_id = %(inv_id)s"

    def sql_cycle():
        conn.begin()
        cur.execute(sql_upd, {"name": new_name, "inv_id": inv_id})
        n = cur.rowcount
        conn.rollback()
        return n

    sql_rows_changed = sql_cycle()
    s_timing = timed(sql_cycle, runs)
    try:
        explain_idx = db.awards.find(filt).explain()
        m_explain = summarize_mongo_explain(explain_idx)
    except Exception as e:
        m_explain = {"error": str(e)}
    cur.execute("SELECT COUNT(*) FROM award_investigator WHERE inv_id = %s", (inv_id,))
    sql_awards = cur.fetchone()[0]
    return {
        "question": "An investigator's name changes. What has to be written?",
        "investigator": {"inv_id": inv_id, "name": old_name, "awards_in_mysql": sql_awards},
        "mongo": {"update": "db.awards.updateMany({'investigators.inv_id': X}, "
                            "{$set: {'investigators.$[p].full_name': NEW}}, {arrayFilters: [{'p.inv_id': X}]})",
                  "documents_matched": docs_matched, "documents_modified": docs_modified,
                  "documents_still_with_old_name_after_update": still_old,
                  "timing_note": "each timed run = rename + rename back (2 updateMany calls)",
                  "timing": m_timing, "find_explain": m_explain},
        "mysql": {"update": sql_upd, "rows_changed": sql_rows_changed,
                  "timing_note": "each timed run = BEGIN; UPDATE; ROLLBACK (data unchanged)",
                  "timing": s_timing},
        "reverted": True,
        "note": ("MongoDB has no multi-document atomicity here unless a transaction (replica set) is "
                 "used; a crash mid-updateMany leaves some documents with the old name. In MySQL the "
                 "name is stored once (normalized), so every award sees the change through the join."),
    }


# ---------------------------------------------------------------------------------------------

def fmt_t(t):
    return f"{t['median_ms']} ms (min {t['min_ms']}, max {t['max_ms']}, n={t['runs']})"


def write_md(out):
    L = ["# MongoDB vs MySQL comparison", "",
         f"Subset: state {out['params']['state']}, end_date >= {out['params']['since']}. "
         f"Mongo documents: {out['mongo_awards']:,}. Run {out['run_at']}. "
         f"MongoDB {out['mongo_version']}, MySQL {out['mysql_version']}.", "",
         "Timings are this script's measured medians on this machine; MySQL filters the full "
         "database down to the subset, MongoDB holds only the subset, so they are not a like-for-like "
         "engine benchmark.", ""]
    k = out["ops"].get("1_keyword", {})
    if "mongo" in k:
        L += ["## 1. Keyword search", "",
              f"- MongoDB `$text`: {k['mongo']['matches']} awards, {fmt_t(k['mongo']['timing'])}; "
              f"stages {k['mongo'].get('explain', {}).get('stages')}",
              f"- MySQL `LIKE`: {k['mysql_like']['matches']} awards, {fmt_t(k['mysql_like']['timing'])}",
              f"- Overlap: {k['overlap']}", ""]
        if isinstance(k.get("mysql_fulltext"), dict):
            f = k["mysql_fulltext"]
            L += [f"- MySQL FULLTEXT: {f['matches']} awards, {fmt_t(f['timing'])}", ""]
    for key, title, cnt in (("2_funding_by_year", "2. Funding by year and agency", "groups"),
                            ("3_coinvestigator_pairs", "3. Co-investigator pairs", "distinct_pairs")):
        o = out["ops"].get(key, {})
        if "mongo" in o:
            L += [f"## {title}", "",
                  f"- Results match: **{o['results_match']}**",
                  f"- MongoDB: {o['mongo'][cnt]} {cnt}, {fmt_t(o['mongo']['timing'])}; "
                  f"stages {o['mongo']['explain'].get('stages')}, docs examined "
                  f"{o['mongo']['explain'].get('totalDocsExamined')}",
                  f"- MySQL: {o['mysql'][cnt]} {cnt}, {fmt_t(o['mysql']['timing'])}", ""]
    u = out["ops"].get("4_update_embedded_investigator", {})
    if "mongo" in u:
        L += ["## 4. Rename an investigator", "",
              f"- Investigator {u['investigator']['inv_id']} ({u['investigator']['name']})",
              f"- MongoDB: {u['mongo']['documents_modified']} documents rewritten, "
              f"{fmt_t(u['mongo']['timing'])} per rename+revert",
              f"- MySQL: {u['mysql']['rows_changed']} row, {fmt_t(u['mysql']['timing'])} per update+rollback", ""]
    for name, o in out["ops"].items():
        if "error" in o:
            L += [f"- **{name} failed:** {o['error']}"]
    (RESULTS / "compare.md").write_text("\n".join(L) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    built = {}
    if (RESULTS / "build.json").exists():  # default to the subset build_documents.py exported
        built = json.loads((RESULTS / "build.json").read_text())
    ap.add_argument("--state", default=built.get("state", "CA"))
    ap.add_argument("--since", default=built.get("since", "2021-10-01"))
    ap.add_argument("--topic", default="machine learning")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("ops", nargs="*", help="1 2 3 4 (default all)")
    args = ap.parse_args()
    p = {"state": args.state, "since": date.fromisoformat(args.since), "topic": args.topic}
    db = mongo_db()
    conn = connect()
    conn.autocommit(False)
    cur = conn.cursor()
    n = db.awards.count_documents({})
    if n == 0:
        sys.exit("MongoDB 'awards' is empty: run python mongo/build_documents.py first")
    cur.execute("SELECT VERSION()")
    mysql_version = cur.fetchone()[0]
    try:
        mongo_version = db.client.server_info().get("version")
    except Exception:
        mongo_version = "unknown"

    wanted = set(args.ops or ["1", "2", "3", "4"])
    ops = {}
    plan = [("1", "1_keyword", lambda: op_keyword(db, cur, p, args.runs)),
            ("2", "2_funding_by_year", lambda: op_funding(db, cur, p, args.runs)),
            ("3", "3_coinvestigator_pairs", lambda: op_pairs(db, cur, p, args.runs)),
            ("4", "4_update_embedded_investigator", lambda: op_update(db, cur, conn, p, args.runs))]
    for num_, name, fn in plan:
        if num_ not in wanted:
            continue
        print(f"running {name} ...", flush=True)
        try:
            ops[name] = fn()
        except Exception as e:
            ops[name] = {"error": f"{type(e).__name__}: {e}"}
            print(f"  failed: {e}")
        conn.rollback()

    out = {"run_at": datetime.now().isoformat(timespec="seconds"), "mysql_version": mysql_version,
           "mongo_version": mongo_version, "mongo_awards": n,
           "params": {**p, "since": str(p["since"]), "runs": args.runs}, "ops": ops}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "compare.json").write_text(json.dumps(out, indent=2, default=str))
    write_md(out)
    print((RESULTS / "compare.md").read_text())
    print("wrote mongo/results/compare.json and mongo/results/compare.md")
    conn.close()


if __name__ == "__main__":
    main()
