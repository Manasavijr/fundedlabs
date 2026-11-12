-- FundedLabs schema (MySQL 8.0+)
-- Usage: mysql -u root < sql/schema.sql
-- Recreates the database from scratch; run it before a full historical load.
--
-- Physical design note: this file declares only the indexes that keys and
-- constraints require (PKs, UNIQUEs, and the indexes InnoDB needs for FKs).
-- Candidate indexes for the workload live in sql/indexes.sql so that every
-- query can be measured before and after each one is added.

DROP DATABASE IF EXISTS fundedlabs;
CREATE DATABASE fundedlabs CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
USE fundedlabs;

-- ---------------------------------------------------------------------------
-- Reference data
-- ---------------------------------------------------------------------------

CREATE TABLE agency (
  agency_id TINYINT UNSIGNED NOT NULL,
  code      VARCHAR(8)       NOT NULL,
  name      VARCHAR(100)     NOT NULL,
  PRIMARY KEY (agency_id),
  UNIQUE KEY uq_agency_code (code)
) ENGINE=InnoDB;

INSERT INTO agency (agency_id, code, name) VALUES
  (1, 'NIH', 'National Institutes of Health'),
  (2, 'NSF', 'National Science Foundation');

CREATE TABLE institution (
  inst_id INT UNSIGNED NOT NULL,
  name    VARCHAR(255) NOT NULL,
  city    VARCHAR(100) NOT NULL DEFAULT '',   -- '' (not NULL) so UNIQUE below stays strict
  state   CHAR(2)      NOT NULL DEFAULT '',   -- US state / territory code, '' if foreign
  country VARCHAR(100) NULL,
  PRIMARY KEY (inst_id),
  UNIQUE KEY uq_institution (name, city, state)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- People and awards
-- ---------------------------------------------------------------------------

CREATE TABLE investigator (
  inv_id         INT UNSIGNED NOT NULL,
  full_name      VARCHAR(255) NOT NULL,
  name_norm      VARCHAR(160) NOT NULL,      -- 'last|first', ASCII lower case; used for cross-agency matching
  nih_profile_id INT UNSIGNED NULL,          -- NIH RePORTER PI profile ID
  nsf_id         VARCHAR(16)  NULL,          -- NSF investigator ID (zero-padded string)
  inst_id        INT UNSIGNED NULL,          -- institution of the person's most recent award
  PRIMARY KEY (inv_id),
  UNIQUE KEY uq_investigator_nih (nih_profile_id),
  UNIQUE KEY uq_investigator_nsf (nsf_id),
  CONSTRAINT fk_investigator_inst FOREIGN KEY (inst_id) REFERENCES institution (inst_id)
) ENGINE=InnoDB;

-- Named "award" because GRANT is a reserved word in MySQL.
CREATE TABLE award (
  award_id        INT UNSIGNED     NOT NULL,
  agency_id       TINYINT UNSIGNED NOT NULL,
  inst_id         INT UNSIGNED     NOT NULL,
  source_award_no VARCHAR(32)      NOT NULL,  -- NIH core project number / NSF award ID
  title           VARCHAR(600)     NOT NULL,
  abstract        TEXT             NULL,
  department      VARCHAR(255)     NULL,      -- NIH ORG_DEPT; NSF publishes none
  program         VARCHAR(255)     NULL,      -- NIH institute/center name; NSF division name
  start_date      DATE             NULL,
  end_date        DATE             NULL,
  total_amount    DECIMAL(14,2)    NULL,      -- NIH: sum of yearly total cost; NSF: amount obligated to date
  PRIMARY KEY (award_id),
  UNIQUE KEY uq_award_source (agency_id, source_award_no),
  CONSTRAINT fk_award_agency FOREIGN KEY (agency_id) REFERENCES agency (agency_id),
  CONSTRAINT fk_award_inst   FOREIGN KEY (inst_id)   REFERENCES institution (inst_id),
  CONSTRAINT ck_award_dates  CHECK (end_date >= start_date),
  CONSTRAINT ck_award_amount CHECK (total_amount >= 0)
) ENGINE=InnoDB;

-- NIH publishes one record per fiscal year; those become rows here instead of duplicate awards.
CREATE TABLE award_funding (
  award_id    INT UNSIGNED      NOT NULL,
  fiscal_year SMALLINT UNSIGNED NOT NULL,
  amount      DECIMAL(14,2)     NULL,        -- NULL when the source reports no amount for that year
  PRIMARY KEY (award_id, fiscal_year),
  CONSTRAINT fk_funding_award FOREIGN KEY (award_id) REFERENCES award (award_id),
  CONSTRAINT ck_funding_fy    CHECK (fiscal_year BETWEEN 1950 AND 2100)
) ENGINE=InnoDB;

CREATE TABLE award_investigator (
  award_id INT UNSIGNED        NOT NULL,
  inv_id   INT UNSIGNED        NOT NULL,
  role     ENUM('PI', 'CO_PI') NOT NULL,
  PRIMARY KEY (award_id, inv_id),
  CONSTRAINT fk_ai_award FOREIGN KEY (award_id) REFERENCES award (award_id),
  CONSTRAINT fk_ai_inv   FOREIGN KEY (inv_id)   REFERENCES investigator (inv_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- Publications
-- ---------------------------------------------------------------------------

CREATE TABLE publication (
  pub_id   INT UNSIGNED      NOT NULL,
  pmid     INT UNSIGNED      NULL,
  doi      VARCHAR(255)      NULL,           -- NIH ExPORTER does not publish DOIs; NULL for those rows
  title    VARCHAR(1000)     NULL,
  journal  VARCHAR(300)      NULL,
  pub_year SMALLINT UNSIGNED NULL,
  PRIMARY KEY (pub_id),
  UNIQUE KEY uq_publication_pmid (pmid),
  UNIQUE KEY uq_publication_doi (doi)
) ENGINE=InnoDB;

CREATE TABLE award_publication (
  award_id INT UNSIGNED NOT NULL,
  pub_id   INT UNSIGNED NOT NULL,
  PRIMARY KEY (award_id, pub_id),
  CONSTRAINT fk_ap_award FOREIGN KEY (award_id) REFERENCES award (award_id),
  CONSTRAINT fk_ap_pub   FOREIGN KEY (pub_id)   REFERENCES publication (pub_id)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- Student outreach tracker (the student's own records; not part of shared data)
-- ---------------------------------------------------------------------------

CREATE TABLE outreach (
  outreach_id  INT UNSIGNED NOT NULL AUTO_INCREMENT,
  inv_id       INT UNSIGNED NOT NULL,
  award_id     INT UNSIGNED NULL,
  status       ENUM('to_contact','emailed','replied','meeting','offer','declined')
               NOT NULL DEFAULT 'to_contact',
  sent_on      DATE         NULL,
  follow_up_on DATE         NULL,
  notes        TEXT         NULL,
  created_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at   TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (outreach_id),
  CONSTRAINT fk_outreach_inv   FOREIGN KEY (inv_id)   REFERENCES investigator (inv_id),
  CONSTRAINT fk_outreach_award FOREIGN KEY (award_id) REFERENCES award (award_id),
  CONSTRAINT ck_outreach_dates CHECK (follow_up_on IS NULL OR sent_on IS NULL OR follow_up_on >= sent_on)
) ENGINE=InnoDB;

-- ---------------------------------------------------------------------------
-- Pipeline logging (feeds the ingestion monitor)
-- ---------------------------------------------------------------------------

CREATE TABLE ingest_run (
  run_id      INT UNSIGNED NOT NULL AUTO_INCREMENT,
  source      VARCHAR(32)  NOT NULL,          -- e.g. 'bulk', 'nih_api', 'nsf_api'
  started_at  DATETIME(3)  NOT NULL,
  finished_at DATETIME(3)  NULL,
  status      ENUM('running','success','failed') NOT NULL DEFAULT 'running',
  PRIMARY KEY (run_id)
) ENGINE=InnoDB;

CREATE TABLE ingest_step (
  run_id      INT UNSIGNED    NOT NULL,
  step        VARCHAR(64)     NOT NULL,
  rows_in     BIGINT UNSIGNED NULL,
  rows_out    BIGINT UNSIGNED NULL,
  duration_ms INT UNSIGNED    NULL,
  PRIMARY KEY (run_id, step),
  CONSTRAINT fk_step_run FOREIGN KEY (run_id) REFERENCES ingest_run (run_id)
) ENGINE=InnoDB;
