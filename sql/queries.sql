-- FundedLabs query workload Q1-Q8 (MySQL 8.0+)
-- Run:  mysql -u root fundedlabs < sql/queries.sql
-- Each query reads its inputs from the session variables set below, so the same text can be
-- wrapped in EXPLAIN ANALYZE for the physical-design experiments (experiments/).
-- @today is a variable rather than CURDATE() so that plans and results are reproducible.

SET @today     = DATE('2026-10-12');
SET @inst_name = 'UNIVERSITY OF SOUTHERN CALIFORNIA';
SET @inst_id   = (SELECT inst_id FROM institution WHERE name = @inst_name ORDER BY inst_id LIMIT 1);
SET @inv_id    = (SELECT ai.inv_id FROM award_investigator ai JOIN award a ON a.award_id = ai.award_id
                  WHERE a.inst_id = @inst_id GROUP BY ai.inv_id ORDER BY COUNT(*) DESC, ai.inv_id LIMIT 1);
SET @topic     = 'machine learning';
SET @program   = 'NATIONAL CANCER INSTITUTE';
SET @months    = 12;

-- Q1. Active awards at a university, ending latest first.
--     Filter + ORDER BY. Experiments: INDEX(end_date) vs INDEX(inst_id, end_date);
--     index-provided order vs filesort.
SELECT a.award_id, a.source_award_no, a.title, a.start_date, a.end_date, a.total_amount
FROM award a
WHERE a.inst_id = @inst_id
  AND a.start_date <= @today
  AND a.end_date   >= @today
ORDER BY a.end_date DESC
LIMIT 100;

-- Q2. Which investigators at a university hold the most active funding?
--     3-table join + GROUP BY. Lifetime award amount of each active award, credited to every PI on it.
SELECT i.inv_id, i.full_name,
       COUNT(*)            AS active_awards,
       SUM(a.total_amount) AS active_award_total,
       MAX(a.end_date)     AS latest_end
FROM award a
JOIN award_investigator ai ON ai.award_id = a.award_id
JOIN investigator i        ON i.inv_id    = ai.inv_id
WHERE a.inst_id = @inst_id
  AND a.start_date <= @today
  AND a.end_date   >= @today
GROUP BY i.inv_id, i.full_name
ORDER BY active_award_total DESC
LIMIT 25;

-- Q3. What has a professor published from their awards in the last 3 years?
--     Join through two M:N tables (award_investigator, award_publication).
SELECT p.pub_id, p.pmid, p.title, p.journal, p.pub_year,
       GROUP_CONCAT(DISTINCT a.source_award_no ORDER BY a.source_award_no) AS from_awards
FROM award_investigator ai
JOIN award a              ON a.award_id  = ai.award_id
JOIN award_publication ap ON ap.award_id = ai.award_id
JOIN publication p         ON p.pub_id    = ap.pub_id
WHERE ai.inv_id = @inv_id
  AND p.pub_year >= YEAR(@today) - 2
GROUP BY p.pub_id, p.pmid, p.title, p.journal, p.pub_year
ORDER BY p.pub_year DESC, p.pub_id;

-- Q4. How has funding for a topic changed by year and agency?
--     Keyword search + GROUP BY.
-- Q4a: LIKE with a leading wildcard (cannot use a B+ tree index; full scan of award).
SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
FROM award a
JOIN award_funding f ON f.award_id = a.award_id
JOIN agency g        ON g.agency_id = a.agency_id
WHERE (a.title LIKE CONCAT('%', @topic, '%') OR a.abstract LIKE CONCAT('%', @topic, '%'))
  AND f.fiscal_year BETWEEN 2016 AND 2026
GROUP BY f.fiscal_year, g.code
ORDER BY f.fiscal_year, g.code;

-- Q4b: same question with FULLTEXT; needs ft_award_text from sql/indexes.sql, so it is left
--      commented out here to keep this file runnable on the base schema.
-- SELECT f.fiscal_year, g.code AS agency, COUNT(DISTINCT a.award_id) AS awards, SUM(f.amount) AS funding
-- FROM award a
-- JOIN award_funding f ON f.award_id = a.award_id
-- JOIN agency g        ON g.agency_id = a.agency_id
-- WHERE MATCH(a.title, a.abstract) AGAINST (CONCAT('"', @topic, '"') IN BOOLEAN MODE)
--   AND f.fiscal_year BETWEEN 2016 AND 2026
-- GROUP BY f.fiscal_year, g.code
-- ORDER BY f.fiscal_year, g.code;

-- Q5. Which awards end in the next @months months?
--     Date-range selectivity: vary @months (1, 12, 60) to see where MySQL abandons the index.
SELECT a.award_id, a.source_award_no, a.title, i.name AS institution, a.end_date, a.total_amount
FROM award a
JOIN institution i ON i.inst_id = a.inst_id
WHERE a.end_date BETWEEN @today AND @today + INTERVAL @months MONTH
ORDER BY a.end_date;

-- Q6. Who does a professor co-lead awards with?
--     Self-join of award_investigator.
SELECT co.inv_id, co.full_name, ci.name AS institution, COUNT(*) AS shared_awards,
       MAX(a.end_date) AS latest_shared_end
FROM award_investigator me
JOIN award_investigator other ON other.award_id = me.award_id AND other.inv_id <> me.inv_id
JOIN investigator co          ON co.inv_id = other.inv_id
LEFT JOIN institution ci      ON ci.inst_id = co.inst_id
JOIN award a                  ON a.award_id = me.award_id
WHERE me.inv_id = @inv_id
GROUP BY co.inv_id, co.full_name, ci.name
ORDER BY shared_awards DESC, latest_shared_end DESC;

-- Q7. The 50 largest awards in a field (NIH institute/center or NSF division).
--     Large sort + LIMIT: top-N with and without INDEX(program, total_amount).
SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, i.name AS institution, a.total_amount
FROM award a
JOIN agency g      ON g.agency_id = a.agency_id
JOIN institution i ON i.inst_id   = a.inst_id
WHERE a.program = @program
ORDER BY a.total_amount DESC
LIMIT 50;

-- Q8. Whom have I contacted, who needs a follow-up, and how funded are they?
--     Inserts and updates on outreach, then a join with an aggregate over active awards.

-- Q8 write path (the app runs these with real values; here they act on @inv_id as a demo).
INSERT INTO outreach (inv_id, award_id, status, sent_on, follow_up_on, notes)
SELECT @inv_id, ai.award_id, 'emailed', @today - INTERVAL 10 DAY, @today - INTERVAL 3 DAY,
       'Asked about RA openings for spring.'
FROM award_investigator ai
WHERE ai.inv_id = @inv_id
ORDER BY ai.award_id DESC
LIMIT 1;
SET @outreach_id = IF(ROW_COUNT() > 0, LAST_INSERT_ID(), NULL);

UPDATE outreach
SET status = 'replied', notes = CONCAT(COALESCE(notes, ''), ' Replied: send CV.'), follow_up_on = @today + INTERVAL 7 DAY
WHERE outreach_id = @outreach_id;

-- Q8 read path.
SELECT o.outreach_id, i.full_name, inst.name AS institution, o.status, o.sent_on, o.follow_up_on,
       (o.follow_up_on <= @today AND o.status IN ('emailed', 'replied', 'meeting')) AS follow_up_due,
       COALESCE(f.active_awards, 0) AS active_awards,
       COALESCE(f.active_total, 0)  AS active_award_total
FROM outreach o
JOIN investigator i        ON i.inv_id = o.inv_id
LEFT JOIN institution inst ON inst.inst_id = i.inst_id
LEFT JOIN (
  SELECT ai.inv_id, COUNT(*) AS active_awards, SUM(a.total_amount) AS active_total
  FROM award_investigator ai
  JOIN award a ON a.award_id = ai.award_id
  WHERE a.start_date <= @today AND a.end_date >= @today
  GROUP BY ai.inv_id
) f ON f.inv_id = o.inv_id
ORDER BY follow_up_due DESC, o.follow_up_on;

-- Remove the demo outreach row so repeated runs leave the table unchanged.
DELETE FROM outreach WHERE outreach_id = @outreach_id;
