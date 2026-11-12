-- FundedLabs historical bulk load (MySQL 8.0+)
--
-- Prerequisites:
--   1. python ingest/pipeline.py transform     (writes data/clean/*.tsv)
--   2. mysql -u root < sql/schema.sql          (fresh, empty database)
-- Then, from the repository root:
--   mysql --local-infile=1 -u root fundedlabs < sql/load.sql
-- or `python ingest/pipeline.py load`, which runs these same statements and records each one
-- in ingest_run / ingest_step.
--
-- The server must allow LOCAL loads:  SET GLOBAL local_infile = 1;
-- Files use MySQL's default LOAD DATA format: tab-separated, backslash escapes, \N for NULL.
-- Parents load before children so foreign keys stay checked during the load.

SET NAMES utf8mb4;
SET SESSION sql_mode = 'STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO,NO_ENGINE_SUBSTITUTION';

LOAD DATA LOCAL INFILE 'data/clean/institution.tsv'
  INTO TABLE institution CHARACTER SET utf8mb4
  (inst_id, name, city, state, country);

LOAD DATA LOCAL INFILE 'data/clean/investigator.tsv'
  INTO TABLE investigator CHARACTER SET utf8mb4
  (inv_id, full_name, name_norm, nih_profile_id, nsf_id, inst_id);

LOAD DATA LOCAL INFILE 'data/clean/award.tsv'
  INTO TABLE award CHARACTER SET utf8mb4
  (award_id, agency_id, inst_id, source_award_no, title, abstract, department, program,
   start_date, end_date, total_amount);

LOAD DATA LOCAL INFILE 'data/clean/award_funding.tsv'
  INTO TABLE award_funding CHARACTER SET utf8mb4
  (award_id, fiscal_year, amount);

LOAD DATA LOCAL INFILE 'data/clean/award_investigator.tsv'
  INTO TABLE award_investigator CHARACTER SET utf8mb4
  (award_id, inv_id, role);

LOAD DATA LOCAL INFILE 'data/clean/publication.tsv'
  INTO TABLE publication CHARACTER SET utf8mb4
  (pub_id, pmid, doi, title, journal, pub_year);

LOAD DATA LOCAL INFILE 'data/clean/award_publication.tsv'
  INTO TABLE award_publication CHARACTER SET utf8mb4
  (award_id, pub_id);

-- Refresh optimizer statistics so the first EXPLAIN plans reflect the loaded data.
ANALYZE TABLE institution, investigator, award, award_funding, award_investigator,
              publication, award_publication;

-- Row counts after the load.
SELECT 'institution' AS tbl, COUNT(*) AS n FROM institution
UNION ALL SELECT 'investigator', COUNT(*) FROM investigator
UNION ALL SELECT 'award', COUNT(*) FROM award
UNION ALL SELECT 'award_funding', COUNT(*) FROM award_funding
UNION ALL SELECT 'award_investigator', COUNT(*) FROM award_investigator
UNION ALL SELECT 'publication', COUNT(*) FROM publication
UNION ALL SELECT 'award_publication', COUNT(*) FROM award_publication;
