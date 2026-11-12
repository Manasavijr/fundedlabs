"""FundedLabs ingestion pipeline.

  python ingest/pipeline.py download   # fetch bulk files into data/raw/ (see download.py)
  python ingest/pipeline.py transform  # data/raw -> data/clean/*.tsv + data/clean/transform_log.json
  python ingest/pipeline.py load       # run sql/load.sql against MySQL; log the run in ingest_run/ingest_step
  python ingest/pipeline.py all        # transform + load (schema.sql must already have been applied)

MySQL connection settings come from the environment: MYSQL_HOST (default 127.0.0.1),
MYSQL_PORT (3306), MYSQL_USER (root), MYSQL_PASSWORD (empty), MYSQL_DB (fundedlabs).
"""

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_clean  # noqa: E402
import parse_nih    # noqa: E402
import parse_nsf    # noqa: E402
from common import CLEAN_DIR, ROOT, StepLog, log  # noqa: E402

TRANSFORM_LOG = CLEAN_DIR / "transform_log.json"


def transform():
    steplog = StepLog()
    started = datetime.now()
    nih_awards, nih_links, nih_pubs = parse_nih.parse_all(steplog)
    nsf_awards = parse_nsf.parse_all(steplog)
    counts, match_stats = build_clean.build(nih_awards, nih_links, nih_pubs, nsf_awards, steplog)
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    TRANSFORM_LOG.write_text(json.dumps({
        "started_at": started.isoformat(timespec="milliseconds"),
        "finished_at": datetime.now().isoformat(timespec="milliseconds"),
        "steps": steplog.steps,
        "clean_row_counts": counts,
        "investigator_matching": match_stats,
    }, indent=2, default=str))
    log(f"wrote {TRANSFORM_LOG.relative_to(ROOT)}")


def sql_statements(path):
    """Split a .sql file into statements. Drops '-- ' comments; assumes no ';' or '-- '
    inside string literals, which holds for the files in sql/."""
    text = re.sub(r"(^|\s)--(\s[^\n]*)?$", r"\1", Path(path).read_text(), flags=re.M)
    return [s.strip() for s in text.split(";") if s.strip()]


def connect():
    import pymysql
    return pymysql.connect(host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
                           port=int(os.environ.get("MYSQL_PORT", "3306")),
                           user=os.environ.get("MYSQL_USER", "root"),
                           password=os.environ.get("MYSQL_PASSWORD", ""),
                           database=os.environ.get("MYSQL_DB", "fundedlabs"),
                           charset="utf8mb4", local_infile=True, autocommit=True)


def count_lines(path):
    with open(path, "rb") as f:
        return sum(1 for _ in f)


def load():
    missing = [t for t in ("institution", "investigator", "award") if not (CLEAN_DIR / f"{t}.tsv").exists()]
    if missing:
        log("data/clean/ has no output yet; run `python ingest/pipeline.py transform` first")
        return False
    conn = connect()
    cur = conn.cursor()
    cur.execute("INSERT INTO ingest_run (source, started_at, status) VALUES ('bulk', NOW(3), 'running')")
    run_id = cur.lastrowid
    log(f"ingest_run {run_id}")

    def record(step, rows_in, rows_out, ms):
        cur.execute("INSERT INTO ingest_step (run_id, step, rows_in, rows_out, duration_ms) VALUES (%s,%s,%s,%s,%s)",
                    (run_id, step[:64], rows_in, rows_out, ms))

    try:
        if TRANSFORM_LOG.exists():
            for s in json.loads(TRANSFORM_LOG.read_text())["steps"]:
                record(s["step"], s["rows_in"], s["rows_out"], s["duration_ms"])
        os.chdir(ROOT)   # LOAD DATA LOCAL paths in load.sql are relative to the repo root
        problems = []
        for stmt in sql_statements(ROOT / "sql" / "load.sql"):
            m = re.match(r"LOAD DATA LOCAL INFILE '([^']+)'\s+INTO TABLE (\w+)", stmt, re.I)
            t0 = time.perf_counter()
            affected = cur.execute(stmt)
            ms = int((time.perf_counter() - t0) * 1000)
            if m:
                file_rows = count_lines(ROOT / m.group(1))
                warnings = conn.show_warnings()
                record(f"load_{m.group(2)}", file_rows, affected, ms)
                log(f"  {m.group(2)}: {affected}/{file_rows} rows, {ms} ms, {len(warnings)} warnings")
                for w in warnings[:5]:
                    log(f"    {w}")
                # LOCAL loads turn errors into warnings and skip bad rows, so check explicitly
                if warnings or affected != file_rows:
                    problems.append(m.group(2))
            elif stmt.upper().startswith("SELECT"):
                for row in cur.fetchall():
                    log(f"  {row[0]}: {row[1]}")
            else:
                cur.fetchall()
        status = "failed" if problems else "success"
        if problems:
            log(f"load finished with warnings or skipped rows in: {', '.join(problems)}")
    except Exception:
        cur.execute("UPDATE ingest_run SET status='failed', finished_at=NOW(3) WHERE run_id=%s", (run_id,))
        raise
    cur.execute("UPDATE ingest_run SET status=%s, finished_at=NOW(3) WHERE run_id=%s", (status, run_id))
    conn.close()
    return status == "success"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["download", "transform", "load", "all"])
    args = ap.parse_args()
    if args.command == "download":
        import download
        download.main([])
    if args.command in ("transform", "all"):
        transform()
    if args.command in ("load", "all"):
        if not load():
            sys.exit(1)


if __name__ == "__main__":
    main()
