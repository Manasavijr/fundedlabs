"""Physical-design experiments E1-E6: each query under several index configurations.

  python experiments/run_experiment.py --list          # what each experiment compares
  python experiments/run_experiment.py E1 E3           # run some
  python experiments/run_experiment.py all             # run all six
  python experiments/run_experiment.py E2 --runs 9     # more timed runs

For every (experiment, configuration) it:
  1. applies the configuration (creates its indexes, sets session variables), then ANALYZE TABLE;
  2. saves SHOW INDEX, EXPLAIN FORMAT=TREE and EXPLAIN ANALYZE;
  3. runs the query once to warm up, then --runs times (default 5), and records the median and spread;
  4. records session counters (Handler_read_*, Sort_*, Select_*, Created_tmp_*) for one run;
  5. for sort experiments, saves the optimizer trace's filesort summary;
  6. undoes the configuration, so every configuration starts from the schema.sql baseline.

Results go to experiments/results/<experiment>/<config>.json and experiments/results/summary.csv.
All published timings come from this script. Connection settings: same MYSQL_* variables as
ingest/pipeline.py. Query parameters (@today, @inst_id, @inv_id, ...) come from the SET lines at
the top of sql/queries.sql, so the experiments always match the workload file.
"""

import argparse
import csv
import json
import re
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
from pipeline import connect, sql_statements  # noqa: E402

RESULTS = ROOT / "experiments" / "results"

COUNTERS = ("Handler_read_first", "Handler_read_key", "Handler_read_last", "Handler_read_next",
            "Handler_read_prev", "Handler_read_rnd", "Handler_read_rnd_next", "Sort_merge_passes",
            "Sort_range", "Sort_rows", "Sort_scan", "Select_full_join", "Select_range", "Select_scan",
            "Created_tmp_tables", "Created_tmp_disk_tables")

SERVER_VARS = ("version", "innodb_buffer_pool_size", "sort_buffer_size", "join_buffer_size",
               "tmp_table_size", "innodb_ft_min_token_size")

# Every index any configuration creates: (name, table, definition).
INDEXES = {
    "ix_award_end":            ("award", "INDEX ix_award_end (end_date)"),
    "ix_award_inst_end":       ("award", "INDEX ix_award_inst_end (inst_id, end_date)"),
    "ix_award_end_inst":       ("award", "INDEX ix_award_end_inst (end_date, inst_id)"),
    "ix_award_program_amount": ("award", "INDEX ix_award_program_amount (program, total_amount)"),
    "ix_publication_year":     ("publication", "INDEX ix_publication_year (pub_year)"),
    "ft_award_text":           ("award", "FULLTEXT INDEX ft_award_text (title, abstract)"),
}

# ---------------------------------------------------------------------------------------------
# Queries. Same text as sql/queries.sql; {hint_*} placeholders let a configuration add index hints.
# ---------------------------------------------------------------------------------------------

Q1 = """SELECT a.award_id, a.source_award_no, a.title, a.start_date, a.end_date, a.total_amount
FROM award a
WHERE a.inst_id = @inst_id AND a.start_date <= @today AND a.end_date >= @today
ORDER BY a.end_date DESC
LIMIT 100"""

Q2 = """SELECT {hint_q2} i.inv_id, i.full_name, COUNT(*) AS active_awards,
       SUM(a.total_amount) AS active_award_total, MAX(a.end_date) AS latest_end
FROM award a
JOIN award_investigator ai {hint_ai} ON ai.award_id = a.award_id
JOIN investigator i {hint_i}         ON i.inv_id    = ai.inv_id
WHERE a.inst_id = @inst_id AND a.start_date <= @today AND a.end_date >= @today
GROUP BY i.inv_id, i.full_name
ORDER BY active_award_total DESC
LIMIT 25"""

Q3 = """SELECT p.pub_id, p.pmid, p.title, p.journal, p.pub_year,
       GROUP_CONCAT(DISTINCT a.source_award_no ORDER BY a.source_award_no) AS from_awards
FROM award_investigator ai
JOIN award a                       ON a.award_id  = ai.award_id
JOIN award_publication ap {hint_ap} ON ap.award_id = ai.award_id
JOIN publication p                 ON p.pub_id    = ap.pub_id
WHERE ai.inv_id = @inv_id AND p.pub_year >= YEAR(@today) - 2
GROUP BY p.pub_id, p.pmid, p.title, p.journal, p.pub_year
ORDER BY p.pub_year DESC, p.pub_id"""

Q4_LIKE = """SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
FROM award a
JOIN award_funding f ON f.award_id = a.award_id
JOIN agency g        ON g.agency_id = a.agency_id
WHERE (a.title LIKE CONCAT('%', @topic, '%') OR a.abstract LIKE CONCAT('%', @topic, '%'))
  AND f.fiscal_year BETWEEN 2016 AND 2026
GROUP BY f.fiscal_year, g.code
ORDER BY f.fiscal_year, g.code"""

Q4_FT = """SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
FROM award a
JOIN award_funding f ON f.award_id = a.award_id
JOIN agency g        ON g.agency_id = a.agency_id
WHERE MATCH(a.title, a.abstract) AGAINST (CONCAT('"', @topic, '"') IN BOOLEAN MODE)
  AND f.fiscal_year BETWEEN 2016 AND 2026
GROUP BY f.fiscal_year, g.code
ORDER BY f.fiscal_year, g.code"""

Q5 = """SELECT a.award_id, a.source_award_no, a.title, i.name AS institution, a.end_date, a.total_amount
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE a.end_date BETWEEN @today AND @today + INTERVAL @months MONTH
ORDER BY a.end_date"""

Q7 = """SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, i.name AS institution, a.total_amount
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE a.program = @program
ORDER BY a.total_amount DESC
{limit}"""

# Ignoring every index on a table for joins forces MySQL to scan it and build a hash table.
NO_JOIN_IDX = "IGNORE INDEX FOR JOIN (PRIMARY, fk_ai_inv)"


def cfg(name, query, indexes=(), session=(), params=(), trace=False, **fmt):
    return {"name": name, "query": query.format(**fmt) if fmt else query, "indexes": list(indexes),
            "session": list(session), "params": list(params), "trace": trace}


EXPERIMENTS = {
    "E1": {
        "question": "Active awards at a university, ending latest first (Q1).",
        "compares": "no extra index vs INDEX(end_date) vs INDEX(inst_id, end_date) vs INDEX(end_date, inst_id)",
        "concepts": "B+ tree access paths, composite index column order, index-provided order vs filesort",
        "configs": [
            cfg("baseline", Q1),
            cfg("end", Q1, ["ix_award_end"]),
            cfg("inst_end", Q1, ["ix_award_inst_end"]),
            cfg("end_inst", Q1, ["ix_award_end_inst"]),
        ],
    },
    "E2": {
        "question": "Awards ending in the next N months, nationwide (Q5).",
        "compares": "INDEX(end_date) at 1, 12 and 60 month windows; no index at 12 months",
        "concepts": "predicate selectivity, range scan vs full table scan, cost-based access path choice",
        "configs": [
            cfg("noindex_12m", Q5, params=["SET @months = 12"]),
            cfg("end_1m", Q5, ["ix_award_end"], params=["SET @months = 1"]),
            cfg("end_12m", Q5, ["ix_award_end"], params=["SET @months = 12"]),
            cfg("end_60m", Q5, ["ix_award_end"], params=["SET @months = 60"]),
        ],
    },
    "E3": {
        "question": "The 50 largest awards in a field (Q7).",
        "compares": "no index vs INDEX(program, total_amount); LIMIT 50 vs no LIMIT; small vs default sort_buffer_size",
        "concepts": "top-N (priority queue) sort, internal vs external merge sort, index-provided order",
        "configs": [
            cfg("baseline_top50", Q7, trace=True, limit="LIMIT 50"),
            cfg("baseline_all", Q7, trace=True, limit=""),
            cfg("baseline_all_small_sortbuf", Q7, session=["SET SESSION sort_buffer_size = 32768"],
                trace=True, limit=""),
            cfg("program_amount_top50", Q7, ["ix_award_program_amount"], trace=True, limit="LIMIT 50"),
        ],
    },
    "E4": {
        "question": "Investigators at a university with the most active funding (Q2).",
        "compares": "indexed join keys vs join indexes ignored (hash join) vs ignored with hash join disabled",
        "concepts": "index nested-loop join vs hash join vs nested-loop scan, join order, GROUP BY temp table",
        "configs": [
            cfg("indexed", Q2, hint_q2="", hint_ai="", hint_i=""),
            cfg("no_join_index_hash", Q2, hint_q2="", hint_ai=NO_JOIN_IDX,
                hint_i="IGNORE INDEX FOR JOIN (PRIMARY)"),
            cfg("no_join_index_no_hash", Q2, hint_q2="/*+ NO_BNL(ai, i) */", hint_ai=NO_JOIN_IDX,
                hint_i="IGNORE INDEX FOR JOIN (PRIMARY)"),
        ],
    },
    "E5": {
        "question": "What a professor published from their awards in the last 3 years (Q3).",
        "compares": "composite PKs as join indexes vs award_publication PK ignored; with and without INDEX(pub_year)",
        "concepts": "composite primary key as a join index, InnoDB secondary indexes carrying the PK",
        "configs": [
            cfg("baseline", Q3, hint_ap=""),
            cfg("pub_year", Q3, ["ix_publication_year"], hint_ap=""),
            cfg("ap_pk_ignored", Q3, hint_ap="IGNORE INDEX FOR JOIN (PRIMARY)"),
        ],
    },
    "E6": {
        "question": "How funding for a topic changed by year and agency (Q4).",
        "compares": "LIKE '%term%' without and with a FULLTEXT index present vs MATCH ... AGAINST",
        "concepts": "why a leading wildcard defeats a B+ tree; inverted (full-text) index as another access method",
        "configs": [
            cfg("like", Q4_LIKE),
            cfg("like_ft_present", Q4_LIKE, ["ft_award_text"]),
            cfg("fulltext", Q4_FT, ["ft_award_text"]),
        ],
    },
}


# ---------------------------------------------------------------------------------------------

def rows(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def existing_indexes(cur, table):
    return {r[0] for r in rows(cur, "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
                                    f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'")}


# Indexes InnoDB created for foreign keys in schema.sql. Adding a candidate index that starts with the
# same column lets InnoDB silently drop the FK's own index (the candidate now serves the FK), and the
# candidate then can't be dropped until another index covers that column again.
FK_INDEXES = {"award": [("fk_award_inst", "inst_id")]}


def drop_candidates(cur):
    """Return every table to the schema.sql baseline."""
    for table, fks in FK_INDEXES.items():
        have = existing_indexes(cur, table)
        for fk_name, col in fks:
            if fk_name not in have:
                cur.execute(f"ALTER TABLE {table} ADD INDEX {fk_name} ({col})")
    for name, (table, _) in INDEXES.items():
        if name in existing_indexes(cur, table):
            cur.execute(f"ALTER TABLE {table} DROP INDEX {name}")


def set_params(cur):
    for stmt in sql_statements(ROOT / "sql" / "queries.sql"):
        if stmt.upper().startswith("SET @"):
            cur.execute(stmt)
        elif not stmt.upper().startswith("SET"):
            break


def session_status(cur):
    return {k: int(v) for k, v in rows(cur, "SHOW SESSION STATUS") if k in COUNTERS}


def timed(cur, query):
    t0 = time.perf_counter()
    cur.execute(query)
    n = len(cur.fetchall())
    return (time.perf_counter() - t0) * 1000, n


def filesort_summary(cur):
    trace = rows(cur, "SELECT TRACE FROM information_schema.OPTIMIZER_TRACE")
    if not trace:
        return None
    found = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("filesort_summary", "filesort_priority_queue_optimization"):
                    found.append({k: v})
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(json.loads(trace[0][0]))
    return found


def run_config(cur, exp_id, c, runs):
    drop_candidates(cur)
    built = {}
    tables = {"award"}
    for name in c["indexes"]:
        table, definition = INDEXES[name]
        t0 = time.perf_counter()
        cur.execute(f"ALTER TABLE {table} ADD {definition}")
        built[name] = round((time.perf_counter() - t0) * 1000)   # build cost, for the write-cost discussion
        tables.add(table)
    for t in sorted(tables):
        rows(cur, f"ANALYZE TABLE {t}")
    set_params(cur)
    for s in c["params"] + c["session"]:
        cur.execute(s)

    q = c["query"]
    result = {
        "experiment": exp_id, "config": c["name"], "when": datetime.now().isoformat(timespec="seconds"),
        "query": q, "indexes_added": c["indexes"], "index_build_ms": built,
        "session_settings": c["params"] + c["session"],
        "server": {k: v for k, v in rows(cur, "SHOW VARIABLES") if k in SERVER_VARS},
        "show_index": {t: [list(map(str, r[:5])) for r in rows(cur, f"SHOW INDEX FROM {t}")] for t in sorted(tables)},
        "explain_tree": rows(cur, "EXPLAIN FORMAT=TREE " + q)[0][0],
    }

    timed(cur, q)                                    # warm-up
    times, n = [], None
    for _ in range(runs):
        ms, n = timed(cur, q)
        times.append(ms)
    result["rows_returned"] = n
    result["times_ms"] = [round(t, 2) for t in times]
    result["median_ms"] = round(statistics.median(times), 2)
    result["min_ms"], result["max_ms"] = round(min(times), 2), round(max(times), 2)

    cur.execute("FLUSH STATUS")
    if c["trace"]:
        cur.execute("SET optimizer_trace = 'enabled=on', optimizer_trace_max_mem_size = 16777216")
    timed(cur, q)
    result["session_counters"] = session_status(cur)
    if c["trace"]:
        result["filesort"] = filesort_summary(cur)
        cur.execute("SET optimizer_trace = 'enabled=off'")

    result["explain_analyze"] = rows(cur, "EXPLAIN ANALYZE " + q)[0][0]

    cur.execute("SET SESSION sort_buffer_size = DEFAULT")
    drop_candidates(cur)
    return result


def access_summary(tree):
    """First line of the plan, for the CSV."""
    return re.sub(r"\s+", " ", tree.strip().splitlines()[0])[:160]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("experiments", nargs="*", help="E1 ... E6, or all")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list or not args.experiments:
        for k, e in EXPERIMENTS.items():
            print(f"{k}  {e['question']}\n    compares: {e['compares']}\n    concepts: {e['concepts']}\n"
                  f"    configs:  {', '.join(c['name'] for c in e['configs'])}")
        return
    ids = list(EXPERIMENTS) if args.experiments == ["all"] else [e.upper() for e in args.experiments]

    conn = connect()
    cur = conn.cursor()
    summary_path = RESULTS / "summary.csv"
    RESULTS.mkdir(parents=True, exist_ok=True)
    new_file = not summary_path.exists()
    with open(summary_path, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["when", "experiment", "config", "median_ms", "min_ms", "max_ms", "runs", "rows_returned",
                        "Handler_read_key", "Handler_read_next", "Handler_read_rnd_next", "Sort_merge_passes",
                        "Created_tmp_disk_tables", "plan_top"])
        try:
            for exp_id in ids:
                e = EXPERIMENTS[exp_id]
                print(f"{exp_id}: {e['question']}")
                for c in e["configs"]:
                    print(f"  {c['name']} ...", end="", flush=True)
                    r = run_config(cur, exp_id, c, args.runs)
                    out = RESULTS / exp_id / f"{c['name']}.json"
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_text(json.dumps(r, indent=2, default=str))
                    sc = r["session_counters"]
                    w.writerow([r["when"], exp_id, c["name"], r["median_ms"], r["min_ms"], r["max_ms"], args.runs,
                                r["rows_returned"], sc.get("Handler_read_key"), sc.get("Handler_read_next"),
                                sc.get("Handler_read_rnd_next"), sc.get("Sort_merge_passes"),
                                sc.get("Created_tmp_disk_tables"), access_summary(r["explain_tree"])])
                    f.flush()
                    print(f" median {r['median_ms']} ms, {r['rows_returned']} rows")
        finally:
            drop_candidates(cur)
            conn.close()
    print(f"results in {RESULTS.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
