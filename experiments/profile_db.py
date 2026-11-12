"""Measured facts about the loaded database (size, row counts) and the experiment baseline.

  python experiments/profile_db.py

Writes experiments/results/profile.json (row counts, data and index size per table, buffer pool
size, null rates, awards per year and agency, funding skew, cross-agency match rate) and
experiments/baseline_indexes.txt (SHOW INDEX for every table before any experiment index).
Run it right after a full load, before any experiment.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
from pipeline import connect  # noqa: E402

TABLES = ("agency", "institution", "investigator", "award", "award_funding", "award_investigator",
          "publication", "award_publication", "outreach")


def q(cur, sql):
    cur.execute(sql)
    return cur.fetchall()


def main():
    conn = connect()
    cur = conn.cursor()
    q(cur, "ANALYZE TABLE " + ", ".join(TABLES))
    p = {"server": dict(q(cur, "SHOW VARIABLES WHERE Variable_name IN "
                               "('version','innodb_buffer_pool_size','innodb_page_size')"))}

    p["tables"] = {}
    for name, est_rows, data, index in q(cur, """
            SELECT TABLE_NAME, TABLE_ROWS, DATA_LENGTH, INDEX_LENGTH FROM information_schema.TABLES
            WHERE TABLE_SCHEMA = DATABASE() AND TABLE_TYPE = 'BASE TABLE' ORDER BY TABLE_NAME"""):
        exact = q(cur, f"SELECT COUNT(*) FROM `{name}`")[0][0]
        p["tables"][name] = {"rows": exact, "rows_estimated_by_innodb": est_rows,
                             "data_mb": round(data / 2**20, 1), "index_mb": round(index / 2**20, 1)}
    total = sum(t["data_mb"] + t["index_mb"] for t in p["tables"].values())
    p["total_mb"] = round(total, 1)
    p["fits_in_buffer_pool"] = total * 2**20 < int(p["server"]["innodb_buffer_pool_size"])

    p["awards_by_year_agency"] = [list(map(str, r)) for r in q(cur, """
        SELECT YEAR(a.start_date) AS y, g.code, COUNT(*) FROM award a JOIN agency g USING (agency_id)
        GROUP BY y, g.code ORDER BY y, g.code""")]
    p["null_rates"] = {k: float(v) for k, v in zip(
        ("award.abstract", "award.department", "award.start_date", "award.end_date", "award.total_amount"),
        q(cur, """SELECT AVG(abstract IS NULL), AVG(department IS NULL), AVG(start_date IS NULL),
                         AVG(end_date IS NULL), AVG(total_amount IS NULL) FROM award""")[0])}
    p["null_rates"]["publication.doi"] = float(q(cur, "SELECT AVG(doi IS NULL) FROM publication")[0][0])
    p["null_rates"]["investigator.inst_id"] = float(q(cur, "SELECT AVG(inst_id IS NULL) FROM investigator")[0][0])

    # Funding skew: share of all award dollars held by the top 1% / 10% of institutions.
    totals = [float(r[0]) for r in q(cur, """SELECT SUM(total_amount) s FROM award GROUP BY inst_id
                                              ORDER BY s DESC""") if r[0] is not None]
    grand = sum(totals)
    p["funding_skew"] = {"institutions_with_awards": len(totals),
                         "top_1pct_share": round(sum(totals[:max(1, len(totals) // 100)]) / grand, 4),
                         "top_10pct_share": round(sum(totals[:max(1, len(totals) // 10)]) / grand, 4)}
    p["nih_years_per_award"] = float(q(cur, """SELECT AVG(n) FROM (SELECT COUNT(*) n FROM award_funding f
        JOIN award a USING (award_id) WHERE a.agency_id = 1 GROUP BY f.award_id) t""")[0][0])
    p["investigators_with_both_agency_ids"] = q(cur, """SELECT COUNT(*) FROM investigator
        WHERE nih_profile_id IS NOT NULL AND nsf_id IS NOT NULL""")[0][0]

    out = ROOT / "experiments" / "results" / "profile.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(p, indent=2, default=str))

    lines = []
    for t in TABLES:
        lines.append(f"## {t}")
        for r in q(cur, f"SHOW INDEX FROM `{t}`"):
            lines.append(f"  {r[2]:<28} seq={r[3]} col={r[4]:<16} non_unique={r[1]} type={r[10]}")
    (ROOT / "experiments" / "baseline_indexes.txt").write_text("\n".join(lines) + "\n")
    conn.close()

    for name, t in p["tables"].items():
        print(f"{name:<20} {t['rows']:>10} rows  {t['data_mb']:>8} MB data  {t['index_mb']:>7} MB index")
    print(f"total {p['total_mb']} MB; buffer pool {int(p['server']['innodb_buffer_pool_size']) // 2**20} MB; "
          f"fits in memory: {p['fits_in_buffer_pool']}")
    print("wrote experiments/results/profile.json and experiments/baseline_indexes.txt")


if __name__ == "__main__":
    main()
