-- Final secondary indexes (physical design). MySQL 8.0+.
-- Not applied by schema.sql, so the experiments can start from the bare schema. Apply for the app with
--   mysql -u root fundedlabs < sql/indexes.sql
-- Each one is justified by a measured experiment (experiments/results/summary.csv).
--
-- InnoDB already created an index for every FK column that is not the leftmost column of an existing
-- key: award(inst_id), investigator(inst_id), award_investigator(inv_id), award_publication(pub_id),
-- outreach(inv_id), outreach(award_id). Those serve the joins (E4, E5).

-- Q1, Q2: university + active-date filter, ordered by end_date (E1: 3.78 -> 1.59 ms, no Sort).
-- InnoDB may then drop the separate award(inst_id) FK index, since this one starts with inst_id.
CREATE INDEX ix_award_inst_end ON award (inst_id, end_date);

-- Q5: awards ending within N months (E2: 12-month window 514.82 -> 222.73 ms).
CREATE INDEX ix_award_end ON award (end_date);

-- Q7: top-N by amount within a program; the index supplies the order (E3: 346.27 -> 0.79 ms).
CREATE INDEX ix_award_program_amount ON award (program, total_amount);

-- Q4: keyword search on title and abstract with MATCH ... AGAINST (E6: 6,262 -> 278 ms).
CREATE FULLTEXT INDEX ft_award_text ON award (title, abstract);

-- Rejected after measurement (not created):
--   award (end_date, inst_id)            E1: optimizer never chose it
--   publication (pub_year)               E5: 7.61 ms vs 5.77 ms baseline
--   award_publication (pub_id, award_id) redundant with the FK index (InnoDB appends the PK)
