"""The SQL the app runs. Q1-Q8 are the same queries as sql/queries.sql, with %(name)s parameters in
place of the @session variables, so what the app shows is exactly the workload the experiments measure.
Every response carries the SQL text and its measured time (the "query inspector" in the UI).
"""

ACTIVE = "a.start_date <= %(today)s AND a.end_date >= %(today)s"

# --- Explore (map) -------------------------------------------------------------------------------

STATE_TOTALS = f"""
SELECT i.state, COUNT(*) AS active_awards, SUM(a.total_amount) AS active_total,
       SUM(a.agency_id = 1) AS nih_awards, SUM(a.agency_id = 2) AS nsf_awards
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE {ACTIVE} AND i.state <> ''
GROUP BY i.state
ORDER BY active_awards DESC"""

STATE_INSTITUTIONS = f"""
SELECT i.inst_id, i.name, i.city, COUNT(*) AS active_awards, SUM(a.total_amount) AS active_total,
       MAX(a.end_date) AS latest_end
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE {ACTIVE} AND i.state = %(state)s
GROUP BY i.inst_id, i.name, i.city
ORDER BY active_awards DESC
LIMIT %(limit)s"""

RECENT_AWARDS = """
SELECT a.award_id, g.code AS agency, a.title, i.name AS institution, i.state, a.start_date, a.total_amount
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE a.start_date <= %(today)s
ORDER BY a.start_date DESC
LIMIT %(limit)s"""

# --- Search ----------------------------------------------------------------------------------------

INSTITUTION_SEARCH = """
SELECT inst_id, name, city, state FROM institution
WHERE name LIKE CONCAT('%%', %(q)s, '%%')
ORDER BY (name = %(q)s) DESC, LENGTH(name), name
LIMIT 15"""

INVESTIGATOR_SEARCH = """
SELECT v.inv_id, v.full_name, i.name AS institution, i.state
FROM investigator v
LEFT JOIN institution i ON i.inst_id = v.inst_id
WHERE v.full_name LIKE CONCAT('%%', %(q)s, '%%')
ORDER BY v.full_name
LIMIT 15"""

INSTITUTION = "SELECT inst_id, name, city, state, country FROM institution WHERE inst_id = %(inst_id)s"

# --- Q1-Q8 ------------------------------------------------------------------------------------------

Q1 = """
SELECT a.award_id, a.source_award_no, a.title, a.start_date, a.end_date, a.total_amount
FROM award a
WHERE a.inst_id = %(inst_id)s
  AND a.start_date <= %(today)s
  AND a.end_date   >= %(today)s
ORDER BY a.end_date DESC
LIMIT 100"""

Q2 = """
SELECT i.inv_id, i.full_name,
       COUNT(*)            AS active_awards,
       SUM(a.total_amount) AS active_award_total,
       MAX(a.end_date)     AS latest_end
FROM award a
JOIN award_investigator ai ON ai.award_id = a.award_id
JOIN investigator i        ON i.inv_id    = ai.inv_id
WHERE a.inst_id = %(inst_id)s
  AND a.start_date <= %(today)s
  AND a.end_date   >= %(today)s
GROUP BY i.inv_id, i.full_name
ORDER BY active_award_total DESC
LIMIT 25"""

Q3 = """
SELECT p.pub_id, p.pmid, p.title, p.journal, p.pub_year,
       GROUP_CONCAT(DISTINCT a.source_award_no ORDER BY a.source_award_no) AS from_awards
FROM award_investigator ai
JOIN award a              ON a.award_id  = ai.award_id
JOIN award_publication ap ON ap.award_id = ai.award_id
JOIN publication p         ON p.pub_id    = ap.pub_id
WHERE ai.inv_id = %(inv_id)s
  AND p.pub_year >= YEAR(%(today)s) - 2
GROUP BY p.pub_id, p.pmid, p.title, p.journal, p.pub_year
ORDER BY p.pub_year DESC, p.pub_id"""

Q4 = """
SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
FROM award a
JOIN award_funding f ON f.award_id = a.award_id
JOIN agency g        ON g.agency_id = a.agency_id
WHERE (a.title LIKE CONCAT('%%', %(topic)s, '%%') OR a.abstract LIKE CONCAT('%%', %(topic)s, '%%'))
  AND f.fiscal_year BETWEEN 2016 AND 2026
GROUP BY f.fiscal_year, g.code
ORDER BY f.fiscal_year, g.code"""

Q4_FULLTEXT = """
SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
FROM award a
JOIN award_funding f ON f.award_id = a.award_id
JOIN agency g        ON g.agency_id = a.agency_id
WHERE MATCH(a.title, a.abstract) AGAINST (CONCAT('"', %(topic)s, '"') IN BOOLEAN MODE)
  AND f.fiscal_year BETWEEN 2016 AND 2026
GROUP BY f.fiscal_year, g.code
ORDER BY f.fiscal_year, g.code"""

Q5 = """
SELECT a.award_id, a.source_award_no, a.title, i.name AS institution, a.end_date, a.total_amount
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE a.end_date BETWEEN %(today)s AND %(today)s + INTERVAL %(months)s MONTH
ORDER BY a.end_date"""

Q6 = """
SELECT co.inv_id, co.full_name, ci.name AS institution, COUNT(*) AS shared_awards,
       MAX(a.end_date) AS latest_shared_end
FROM award_investigator me
JOIN award_investigator other ON other.award_id = me.award_id AND other.inv_id <> me.inv_id
JOIN investigator co          ON co.inv_id = other.inv_id
LEFT JOIN institution ci      ON ci.inst_id = co.inst_id
JOIN award a                  ON a.award_id = me.award_id
WHERE me.inv_id = %(inv_id)s
GROUP BY co.inv_id, co.full_name, ci.name
ORDER BY shared_awards DESC, latest_shared_end DESC"""

Q7 = """
SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, i.name AS institution, a.total_amount
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE a.program = %(program)s
ORDER BY a.total_amount DESC
LIMIT 50"""

PROGRAMS = """
SELECT a.program, g.code AS agency, COUNT(*) AS awards
FROM award a JOIN agency g ON g.agency_id = a.agency_id
WHERE a.program IS NOT NULL
GROUP BY a.program, g.code
ORDER BY awards DESC"""

Q8_READ = """
SELECT o.outreach_id, o.inv_id, o.award_id, i.full_name, inst.name AS institution, o.status,
       o.sent_on, o.follow_up_on, o.notes, o.updated_at,
       (o.follow_up_on <= %(today)s AND o.status IN ('emailed', 'replied', 'meeting')) AS follow_up_due,
       COALESCE(f.active_awards, 0) AS active_awards,
       COALESCE(f.active_total, 0)  AS active_award_total
FROM outreach o
JOIN investigator i        ON i.inv_id = o.inv_id
LEFT JOIN institution inst ON inst.inst_id = i.inst_id
LEFT JOIN (
  SELECT ai.inv_id, COUNT(*) AS active_awards, SUM(a.total_amount) AS active_total
  FROM award_investigator ai
  JOIN award a ON a.award_id = ai.award_id
  WHERE a.start_date <= %(today)s AND a.end_date >= %(today)s
  GROUP BY ai.inv_id
) f ON f.inv_id = o.inv_id
ORDER BY follow_up_due DESC, o.follow_up_on IS NULL, o.follow_up_on, o.outreach_id DESC"""

Q8_INSERT = """
INSERT INTO outreach (inv_id, award_id, status, sent_on, follow_up_on, notes)
VALUES (%(inv_id)s, %(award_id)s, %(status)s, %(sent_on)s, %(follow_up_on)s, %(notes)s)"""

Q8_DELETE = "DELETE FROM outreach WHERE outreach_id = %(outreach_id)s"

# --- Profile ---------------------------------------------------------------------------------------

INVESTIGATOR = """
SELECT v.inv_id, v.full_name, v.nih_profile_id, v.nsf_id, i.inst_id, i.name AS institution, i.city, i.state
FROM investigator v LEFT JOIN institution i ON i.inst_id = v.inst_id
WHERE v.inv_id = %(inv_id)s"""

INVESTIGATOR_AWARDS = """
SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, ai.role, a.program,
       a.start_date, a.end_date, a.total_amount, i.name AS institution,
       (a.start_date <= %(today)s AND a.end_date >= %(today)s) AS active
FROM award_investigator ai
JOIN award a       ON a.award_id = ai.award_id
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id = a.inst_id
WHERE ai.inv_id = %(inv_id)s
ORDER BY a.end_date DESC"""

AWARD = """
SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, a.abstract, a.department, a.program,
       a.start_date, a.end_date, a.total_amount, i.inst_id, i.name AS institution, i.city, i.state
FROM award a JOIN agency g ON g.agency_id = a.agency_id JOIN institution i ON i.inst_id = a.inst_id
WHERE a.award_id = %(award_id)s"""

AWARD_PEOPLE = """
SELECT v.inv_id, v.full_name, ai.role FROM award_investigator ai JOIN investigator v ON v.inv_id = ai.inv_id
WHERE ai.award_id = %(award_id)s ORDER BY ai.role, v.full_name"""

AWARD_FUNDING = "SELECT fiscal_year, amount FROM award_funding WHERE award_id = %(award_id)s ORDER BY fiscal_year"

# --- Pipeline --------------------------------------------------------------------------------------

RUNS = """
SELECT r.run_id, r.source, r.started_at, r.finished_at, r.status,
       TIMESTAMPDIFF(SECOND, r.started_at, r.finished_at) AS seconds,
       COUNT(s.step) AS steps
FROM ingest_run r LEFT JOIN ingest_step s ON s.run_id = r.run_id
GROUP BY r.run_id
ORDER BY r.run_id DESC
LIMIT 20"""

STEPS = """
SELECT step, rows_in, rows_out, duration_ms FROM ingest_step WHERE run_id = %(run_id)s
ORDER BY FIELD(LEFT(step, 3), 'nih', 'nsf', 'mat', 'cle', 'wri', 'loa'), step"""

TABLE_SIZES = """
SELECT TABLE_NAME AS table_name, TABLE_ROWS AS approx_rows, DATA_LENGTH AS data_bytes, INDEX_LENGTH AS index_bytes
FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE() ORDER BY DATA_LENGTH DESC"""

# --- Ask: the workload as pickable questions ---------------------------------------------------------

ASK = {
    "Q1": {"question": "Which awards are active at a university right now, ending latest first?",
           "sql": Q1, "params": ["inst_id"], "concepts": "filter + ORDER BY; index-provided order vs filesort"},
    "Q2": {"question": "Which investigators at a university hold the most active funding?",
           "sql": Q2, "params": ["inst_id"], "concepts": "3-table join + GROUP BY; join algorithm and order"},
    "Q3": {"question": "What has a professor published from their awards in the last 3 years?",
           "sql": Q3, "params": ["inv_id"], "concepts": "join through two M:N tables; composite PK as join index"},
    "Q4": {"question": "How has funding for a topic changed by year and agency?",
           "sql": Q4, "params": ["topic"], "concepts": "leading-wildcard LIKE forces a full scan; FULLTEXT inverted index"},
    "Q5": {"question": "Which awards end in the next N months?",
           "sql": Q5, "params": ["months"], "concepts": "range predicate selectivity; range scan vs table scan"},
    "Q6": {"question": "Who does a professor co-lead awards with?",
           "sql": Q6, "params": ["inv_id"], "concepts": "self-join of award_investigator"},
    "Q7": {"question": "What are the 50 largest awards in a field?",
           "sql": Q7, "params": ["program"], "concepts": "top-N sort with LIMIT; INDEX(program, total_amount)"},
    "Q8": {"question": "Whom have I contacted, and who needs a follow-up?",
           "sql": Q8_READ, "params": [], "concepts": "LEFT JOIN with a derived aggregate table"},
}

# --- Explore list (filters from the UI: state, institutions, agency, keyword) ------------------------

def explore_where(state=None, inst_ids=None, agency=None, q=None):
    """WHERE clause + params for the Explore filters. Only fixed SQL fragments are added; every
    user value goes through a %(name)s parameter."""
    where, p = [ACTIVE], {}
    if state:
        where.append("i.state = %(state)s"); p["state"] = state
    if inst_ids:
        names = [f"%(inst{k})s" for k in range(len(inst_ids))]
        where.append(f"a.inst_id IN ({', '.join(names)})")
        p.update({f"inst{k}": v for k, v in enumerate(inst_ids)})
    if agency in ("NIH", "NSF"):
        where.append("a.agency_id = %(agency_id)s"); p["agency_id"] = 1 if agency == "NIH" else 2
    if q:
        where.append("(a.title LIKE CONCAT('%%', %(q)s, '%%') OR a.department LIKE CONCAT('%%', %(q)s, '%%')"
                     " OR a.program LIKE CONCAT('%%', %(q)s, '%%'))")
        p["q"] = q
    return " AND ".join(where), p


def explore_awards(where):
    return f"""
SELECT a.award_id, g.code AS agency, a.title, a.start_date, a.end_date, a.total_amount,
       i.inst_id, i.name AS institution, i.state,
       (SELECT ai.inv_id FROM award_investigator ai
         WHERE ai.award_id = a.award_id AND ai.role = 'PI' ORDER BY ai.inv_id LIMIT 1) AS pi_id
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE {where}
ORDER BY a.end_date DESC, a.award_id
LIMIT %(limit)s OFFSET %(offset)s"""


def explore_totals(where):
    return f"""
SELECT COUNT(*) AS active_awards, COALESCE(SUM(a.total_amount), 0) AS active_total
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE {where}"""


def explore_leaders(where):
    return f"""
SELECT v.inv_id, v.full_name, MIN(i.name) AS institution, COUNT(*) AS active_awards,
       SUM(a.total_amount) AS active_award_total
FROM award a
JOIN institution i         ON i.inst_id   = a.inst_id
JOIN award_investigator ai ON ai.award_id = a.award_id AND ai.role = 'PI'
JOIN investigator v        ON v.inv_id    = ai.inv_id
WHERE {where}
GROUP BY v.inv_id, v.full_name
ORDER BY active_award_total DESC
LIMIT 5"""


INVESTIGATOR_NAMES = "SELECT inv_id, full_name FROM investigator WHERE inv_id IN ({ids})"

TOP_INSTITUTIONS = f"""
SELECT i.inst_id, i.name, i.city, i.state, COUNT(*) AS active_awards
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE {ACTIVE}
GROUP BY i.inst_id, i.name, i.city, i.state
ORDER BY active_awards DESC
LIMIT %(limit)s"""

INVESTIGATOR_FY = """
SELECT f.fiscal_year, SUM(f.amount) AS amount
FROM award_investigator ai
JOIN award_funding f ON f.award_id = ai.award_id
WHERE ai.inv_id = %(inv_id)s AND ai.role = 'PI'
GROUP BY f.fiscal_year
ORDER BY f.fiscal_year"""

# Funded people at an institution who are not on the outreach list yet, longest runway first.
OUTREACH_SUGGEST = f"""
SELECT v.inv_id, v.full_name, i.name AS institution, MAX(a.end_date) AS funded_to,
       MIN(a.start_date) AS since, COUNT(*) AS active_awards, SUM(a.total_amount) AS active_total,
       MAX(a.award_id) AS award_id
FROM award a
JOIN institution i         ON i.inst_id   = a.inst_id
JOIN award_investigator ai ON ai.award_id = a.award_id AND ai.role = 'PI'
JOIN investigator v        ON v.inv_id    = ai.inv_id
WHERE {ACTIVE} AND a.inst_id = %(inst_id)s
  AND NOT EXISTS (SELECT 1 FROM outreach o WHERE o.inv_id = v.inv_id)
GROUP BY v.inv_id, v.full_name, i.name
ORDER BY funded_to DESC, active_total DESC
LIMIT 12"""
