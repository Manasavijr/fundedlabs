"""Live before/after index demo.

  python demo/index_demo.py              # full demo: drop -> run Q1 -> create -> run Q1, side by side
  python demo/index_demo.py before       # just drop the index (baseline state)
  python demo/index_demo.py after        # just create it
  python demo/index_demo.py status       # which indexes award has right now
  python demo/index_demo.py --query Q7   # same demo for Q7 with INDEX(program, total_amount)

Each "run" prints EXPLAIN ANALYZE plus the median of 5 timed runs after a warm-up and the
Handler_read_* counters, so the plan change and the work saved are both visible. The numbers are
measured live on whatever machine runs this; the saved numbers are in experiments/results.

Q1 uses the same text and parameters as sql/queries.sql (USC, @today = 2026-10-12).
InnoDB detail (experiments/NOTES.md): adding INDEX(inst_id, end_date) lets InnoDB drop the
foreign key's own index on award(inst_id); "before" re-creates it first so the composite index can
be dropped and the baseline matches schema.sql.
"""

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
from pipeline import connect, sql_statements  # noqa: E402

DEMOS = {
    "Q1": {
        "question": "Active awards at USC, ending latest first",
        "index": ("ix_award_inst_end", "award", "(inst_id, end_date)"),
        "sql": """SELECT a.award_id, a.source_award_no, a.title, a.start_date, a.end_date, a.total_amount
FROM award a
WHERE a.inst_id = @inst_id
  AND a.start_date <= @today
  AND a.end_date   >= @today
ORDER BY a.end_date DESC
LIMIT 100""",
        "watch": "before: filter on inst_id via the FK index, then Sort. after: range scan on "
                 "ix_award_inst_end read backwards, no Sort step.",
    },
    "Q7": {
        "question": "50 largest National Cancer Institute awards",
        "index": ("ix_award_program_amount", "award", "(program, total_amount)"),
        "sql": """SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, i.name AS institution, a.total_amount
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE a.program = @program
ORDER BY a.total_amount DESC
LIMIT 50""",
        "watch": "before: table scan of award + top-N sort. after: index scan in total_amount order, "
                 "stops after 50 rows.",
    },
}
COUNTERS = ("Handler_read_key", "Handler_read_next", "Handler_read_prev", "Handler_read_rnd_next",
            "Sort_rows", "Sort_scan", "Sort_range")
BOLD, DIM, END = "\033[1m", "\033[2m", "\033[0m"


def q(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def indexes(cur, table="award"):
    return sorted({r[0] for r in q(cur, "SELECT INDEX_NAME FROM information_schema.STATISTICS "
                                        f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'")})


def set_params(cur):
    for stmt in sql_statements(ROOT / "sql" / "queries.sql"):
        if stmt.upper().startswith("SET @"):
            cur.execute(stmt)
        elif not stmt.upper().startswith("SET"):
            break


def before(cur, demo):
    name, table, _ = demo["index"]
    have = indexes(cur, table)
    if "fk_award_inst" not in have:
        print(f"{DIM}re-creating fk_award_inst (InnoDB dropped it when the composite index took over the FK){END}")
        cur.execute("ALTER TABLE award ADD INDEX fk_award_inst (inst_id)")
    if name in have:
        print(f"DROP INDEX {name} ON {table};")
        cur.execute(f"DROP INDEX {name} ON {table}")
    cur.execute(f"ANALYZE TABLE {table}")
    cur.fetchall()


def after(cur, demo):
    name, table, cols = demo["index"]
    if name not in indexes(cur, table):
        print(f"CREATE INDEX {name} ON {table} {cols};")
        t = time.perf_counter()
        cur.execute(f"CREATE INDEX {name} ON {table} {cols}")
        print(f"{DIM}  built in {time.perf_counter() - t:.1f} s{END}")
    cur.execute(f"ANALYZE TABLE {table}")
    cur.fetchall()


def run(cur, demo, runs=5):
    sql = demo["sql"]
    plan = "\n".join(r[0] for r in q(cur, "EXPLAIN ANALYZE " + sql))
    q(cur, sql)  # warm-up
    times = []
    for _ in range(runs):
        t = time.perf_counter()
        rows = q(cur, sql)
        times.append((time.perf_counter() - t) * 1000)
    q(cur, "FLUSH STATUS")
    q(cur, sql)
    counters = {k: int(v) for k, v in q(cur, "SHOW SESSION STATUS") if k in COUNTERS}
    return {"plan": plan, "median_ms": statistics.median(times), "min_ms": min(times),
            "max_ms": max(times), "rows": len(rows), "counters": counters}


def show(label, r):
    print(f"\n{BOLD}{label}{END}: median {r['median_ms']:.1f} ms (min {r['min_ms']:.1f}, max {r['max_ms']:.1f}), "
          f"{r['rows']} rows returned")
    print("  " + ", ".join(f"{k}={v:,}" for k, v in r["counters"].items() if v))
    print(DIM + "\n".join("  " + line for line in r["plan"].splitlines()) + END)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", nargs="?", default="demo", choices=["demo", "before", "after", "status", "run"])
    ap.add_argument("--query", default="Q1", choices=sorted(DEMOS))
    ap.add_argument("--keep", action="store_true", help="after the demo, leave the index in place")
    args = ap.parse_args()
    demo = DEMOS[args.query]
    conn = connect()
    cur = conn.cursor()
    set_params(cur)
    if args.step == "status":
        print("award indexes:", ", ".join(indexes(cur)))
        return
    if args.step == "before":
        before(cur, demo)
        print("award indexes:", ", ".join(indexes(cur)))
        return
    if args.step == "after":
        after(cur, demo)
        print("award indexes:", ", ".join(indexes(cur)))
        return
    if args.step == "run":
        show("current indexes", run(cur, demo))
        return

    print(f"{BOLD}{args.query}: {demo['question']}{END}\n{demo['sql']}\n")
    before(cur, demo)
    b = run(cur, demo)
    show("BEFORE (no " + demo["index"][0] + ")", b)
    print()
    after(cur, demo)
    a = run(cur, demo)
    show("AFTER (" + demo["index"][0] + " " + demo["index"][2] + ")", a)
    print(f"\n{BOLD}median {b['median_ms']:.1f} ms -> {a['median_ms']:.1f} ms{END}")
    print(f"What to point at: {demo['watch']}")
    if not args.keep:
        print()
        before(cur, demo)
        print(f"{DIM}back to the schema.sql baseline (use --keep to leave the index){END}")


if __name__ == "__main__":
    main()
