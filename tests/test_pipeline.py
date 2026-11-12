"""End-to-end test on the synthetic fixtures: transform -> schema -> load -> Q1-Q8.

Needs a MySQL 8 server with local_infile enabled and a user that may create databases:
  MYSQL_USER=... MYSQL_PASSWORD=... python -m pytest tests/
The test works in a temporary copy of the repo and a separate database (fundedlabs_test),
so it never touches data/ or the real fundedlabs database.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TEST_DB = "fundedlabs_test"


@pytest.fixture(scope="module")
def loaded(tmp_path_factory):
    pymysql = pytest.importorskip("pymysql")
    work = tmp_path_factory.mktemp("repo")
    for d in ("ingest", "sql", "tests"):
        shutil.copytree(REPO / d, work / d, ignore=shutil.ignore_patterns("__pycache__"))
    subprocess.run([sys.executable, "tests/make_fixtures.py", "data/raw"], cwd=work, check=True)
    subprocess.run([sys.executable, "ingest/pipeline.py", "transform"], cwd=work, check=True)

    env = dict(os.environ, MYSQL_DB=TEST_DB)
    conn_args = dict(host=env.get("MYSQL_HOST", "127.0.0.1"), port=int(env.get("MYSQL_PORT", "3306")),
                     user=env.get("MYSQL_USER", "root"), password=env.get("MYSQL_PASSWORD", ""),
                     charset="utf8mb4", autocommit=True)
    try:
        conn = pymysql.connect(**conn_args)
    except pymysql.err.OperationalError as e:
        pytest.skip(f"no MySQL server available: {e}")
    sys.path.insert(0, str(work / "ingest"))
    from pipeline import sql_statements
    schema = (work / "sql" / "schema.sql").read_text().replace("fundedlabs", TEST_DB)
    (work / "sql" / "schema.sql").write_text(schema)
    with conn.cursor() as cur:
        for stmt in sql_statements(work / "sql" / "schema.sql"):
            cur.execute(stmt)
    subprocess.run([sys.executable, "ingest/pipeline.py", "load"], cwd=work, env=env, check=True)
    conn.select_db(TEST_DB)
    yield conn, work, sql_statements
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE {TEST_DB}")
    conn.close()


def scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchone()[0]


def test_transform_log(loaded):
    _, work, _ = loaded
    tlog = json.loads((work / "data" / "clean" / "transform_log.json").read_text())
    steps = {s["step"]: s for s in tlog["steps"]}
    assert steps["nih_parse_projects"]["skipped_subprojects"] == 1
    assert steps["nsf_parse_awards"]["unparseable"] == 1
    assert steps["nsf_parse_awards"]["out_of_scope"] == 1
    assert steps["clean_awards"]["rejected"] == {"end_before_start": 1}
    m = tlog["investigator_matching"]
    assert (m["nsf_investigators"], m["matched"]) == (3, 1)


def test_load_counts(loaded):
    conn, _, _ = loaded
    counts = {t: scalar(conn, f"SELECT COUNT(*) FROM {t}") for t in
              ("institution", "investigator", "award", "award_funding", "award_investigator",
               "publication", "award_publication")}
    assert counts == {"institution": 3, "investigator": 5, "award": 6, "award_funding": 9,
                      "award_investigator": 8, "publication": 4, "award_publication": 4}
    assert scalar(conn, "SELECT status FROM ingest_run ORDER BY run_id DESC LIMIT 1") == "success"
    assert scalar(conn, "SELECT COUNT(*) FROM ingest_step") >= 10


def test_cleaning(loaded):
    conn, _, _ = loaded
    # NIH fiscal-year records merged into one award, same-year supplement summed
    assert scalar(conn, "SELECT amount FROM award_funding f JOIN award a USING (award_id) "
                        "WHERE a.source_award_no='R01GM000001' AND fiscal_year=2025") == 570000
    assert scalar(conn, "SELECT total_amount FROM award WHERE source_award_no='R01GM000001'") == 1070000
    assert scalar(conn, "SELECT title FROM award WHERE source_award_no='R01GM000001'") == "Machine learning for protein folding (renewed title)"
    # paragraph breaks are kept and survive the TSV round trip; tabs are collapsed to spaces
    assert "folding.\nSecond" in scalar(conn, "SELECT abstract FROM award WHERE source_award_no='R01GM000001'")
    # cp1252 file decoded
    assert scalar(conn, "SELECT COUNT(*) FROM investigator WHERE full_name LIKE 'M_LLER, KARL'") == 1
    # NIH upper-case and NSF mixed-case spellings of USC became one institution
    assert scalar(conn, "SELECT COUNT(*) FROM institution WHERE name LIKE '%southern california%'") == 1
    # the same person on NIH and NSF awards is one investigator with both IDs
    assert scalar(conn, "SELECT COUNT(*) FROM investigator WHERE nih_profile_id=1001 AND nsf_id='000000101'") == 1


def run_workload(conn, work, sql_statements):
    results = []
    with conn.cursor() as cur:
        for stmt in sql_statements(work / "sql" / "queries.sql"):
            cur.execute(stmt)
            if stmt.upper().startswith("SELECT"):
                results.append(cur.fetchall())
    return results


def test_queries(loaded):
    conn, work, sql_statements = loaded
    q1, q2, q3, q4, q5, q6, q7, q8 = run_workload(conn, work, sql_statements)
    assert [r[1] for r in q1] == ["R01GM000001", "2400001", "R01CA000005"]
    assert q2[0][1] == "John Smith" and q2[0][2] == 3
    assert [r[1] for r in q3] == [3, 2, 1]
    assert {(r[0], r[1]) for r in q4} == {(2024, "NIH"), (2025, "NIH"), (2024, "NSF"), (2025, "NSF")}
    assert [r[1] for r in q5] == ["2400002", "R01CA000005", "2400001"]
    assert {r[1] for r in q6} == {"NGUYEN, LINH", "Maria Garcia"}
    assert [r[2] for r in q7] == ["P01CA000002", "R01CA000005"]
    assert len(q8) == 1 and q8[0][3] == "replied" and q8[0][7] == 3
    assert scalar(conn, "SELECT COUNT(*) FROM outreach") == 0
