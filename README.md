# FundedLabs

FundedLabs puts NIH and NSF grant data into one MySQL database so students can find faculty with
active funding. Measured on MySQL 9.7.4; any MySQL 8.0+ works. The repository holds the ingest
pipeline (download, cleaning, cross-agency investigator matching, bulk load and an incremental NSF
update), the schema and the Q1–Q8 query workload, the index experiments, a MongoDB comparison, a
FastAPI service and a Next.js web app.

## Layout

```
ingest/                download, parse, clean, match and load the NIH and NSF data
sql/                   schema, bulk load, final indexes and the Q1–Q8 queries
experiments/           index and query-plan experiments with their measured results
mongo/                 the same data subset in MongoDB and a side-by-side comparison
api/                   FastAPI service running Q1–Q8 over the MySQL database
web/                   Next.js front end (Explore, Outreach, Pipeline, Ask tabs)
demo/                  live index demo (EXPLAIN ANALYZE before and after an index)
tests/                 synthetic fixtures and an end-to-end test
data/                  raw and cleaned data files (not in version control)

Key files:
ingest/download.py     bulk file download into data/raw/{nih,nsf}/
ingest/parse_nih.py    NIH ExPORTER CSVs -> one record per core project + yearly funding
ingest/parse_nsf.py    NSF award-by-year files (JSON; older XML files also accepted) -> award records
ingest/build_clean.py  validation, institution dedup, cross-agency investigator matching, TSV output
ingest/pipeline.py     download | transform | load | all  (load logs to ingest_run / ingest_step)
ingest/nsf_update.py   incremental update: recent awards from the NSF Award Search API, upserted
ingest/common.py       normalization, date/amount parsing, zip/CSV reading, TSV writer
sql/schema.sql         tables, keys, constraints (recreates the database)
sql/load.sql           LOAD DATA LOCAL INFILE for every table + ANALYZE TABLE
sql/indexes.sql        the final secondary indexes (apply after the load)
sql/queries.sql        Q1–Q8, parameterized with session variables
tests/                 synthetic fixtures + end-to-end test (transform -> load -> queries)
```

## Setup (macOS)

```bash
HOMEBREW_NO_AUTO_UPDATE=1 brew install mysql@9.7 && brew services start mysql@9.7   # skip if MySQL 8.0+ is already installed
cd FundedLabs
python3 -m venv .venv && source .venv/bin/activate
```

Then follow the steps below. If `download` fails on a URL, download the zips by hand from
https://reporter.nih.gov/exporter (Projects, Abstracts, Publications, Link Tables; FY2016-2025) and
https://www.nsf.gov/awardsearch/download-awards/ (2010-2026), put them in `data/raw/nih/` and
`data/raw/nsf/`, and skip to `transform`. File names don't matter; the parsers read the names
inside the zips. The raw files are large, so check free disk space first.

## Running a full historical load

```bash
pip install -r requirements.txt
python ingest/pipeline.py download        # or put the files in data/raw/nih and data/raw/nsf by hand
python ingest/pipeline.py transform       # -> data/clean/*.tsv and data/clean/transform_log.json
mysql -u root < sql/schema.sql            # drops and recreates `fundedlabs`
mysql -u root -e "SET GLOBAL local_infile = 1"
python ingest/pipeline.py load            # or: mysql --local-infile=1 -u root fundedlabs < sql/load.sql
mysql -u root fundedlabs < sql/queries.sql
```

`pipeline.py load` connects with `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`,
`MYSQL_DB` (defaults 127.0.0.1, 3306, root, empty, fundedlabs). It records one `ingest_run` row
and an `ingest_step` row for every transform and load step (rows in, rows out, duration). It marks
the run `failed` if any LOAD DATA produced warnings or loaded fewer rows than the file holds,
because LOCAL loads turn errors into warnings and skip the bad rows instead of stopping.

`data/clean/transform_log.json` holds the per-step counts, rejected records by reason, and the
investigator match rate.

## Tests

```bash
MYSQL_USER=... MYSQL_PASSWORD=... python -m pytest tests/
```

The test builds a tiny **synthetic** dataset in the source formats (`tests/make_fixtures.py`),
runs transform and load into a separate `fundedlabs_test` database, checks the cleaning rules,
and checks that each of Q1–Q8 returns the expected rows. It never touches `data/` or `fundedlabs`.

## Cleaning and integration rules

**NIH.** ExPORTER has one row per application per fiscal year. Rows are grouped by
`CORE_PROJECT_NUM` (the award's `source_award_no`). Each fiscal year becomes an `award_funding`
row; same-year applications (supplements) are summed. `TOTAL_COST` is used, falling back to
direct + indirect cost. Start and end dates are the earliest `PROJECT_START` and latest
`PROJECT_END`. Title, department, institute and institution come from the most recent fiscal
year's main record (supplements are passed over). Sub-project rows (`SUBPROJECT_ID` set) are
skipped because the parent row already carries the total. Contracts, interagency agreements and NIH intramural projects (core numbers starting `Z`, `N`, `75N`, `26x`/`27x`) are dropped: they don't fund university labs, and they otherwise dominate Q7's largest awards. All PIs on a multi-PI award get role
`PI`; NIH has no co-PI role. Abstracts and publication links are joined in from the
PRJABS and PUBLNK files; only publications linked to loaded awards are kept. ExPORTER has no DOIs.

**NSF.** One file per award. `program` is the NSF division (used as the "field" in Q7);
`total_amount` is the amount obligated to date. Per-fiscal-year obligations become
`award_funding` rows; when a file has none, the whole amount is recorded under the start date's
fiscal year. PIs whose role ended are dropped unless no current PI remains.

**Scope.** Awards active in FY2016–2026: NIH files for FY2016–2026 (FY2026 not yet published), NSF start years 2010–2026,
and anything that ended before 2015-10-01 is dropped.

**Rejected records** (counted in the transform log): missing title, end date before start date
(would violate the CHECK constraint), negative total, no institution.

**Institutions** are deduplicated on normalized name + city + state (ASCII upper case, punctuation
removed, `&` -> `AND`, leading `THE` dropped, common abbreviations expanded).

**Investigators.** Within NIH a person is a PI profile ID; within NSF an NSF ID. Across agencies,
an NSF person is merged with an NIH person when their normalized name (`last|first`, middle names
dropped) matches at an institution they both have awards at, and only when the match is
one-to-one; anything ambiguous is left unmerged. The log reports the match rate both over all
NSF investigators and over NSF investigators at institutions that also have NIH awards. An
investigator's `inst_id` is the institution of their most recent award.

## API

```bash
uvicorn api.app:app --reload --port 8000     # then open http://localhost:8000/docs
```

FastAPI over the same MySQL database. `api/queries.py` holds Q1-Q8 with `%(name)s` parameters in
place of the `@` variables in `sql/queries.sql`. Every endpoint returns the rows together with the
SQL text and the measured time; `/api/ask/{Q1..Q8}` also returns `EXPLAIN FORMAT=TREE`
(`?analyze=true` for `EXPLAIN ANALYZE`). Outreach (Q8) supports insert, update and delete.
`?today=YYYY-MM-DD` pins the date (default: the server's current date).

## Web app

```bash
brew install node                  # once, if `node -v` fails
cd web && npm install              # once
uvicorn api.app:app --port 8000    # terminal 1, from the FundedLabs folder
npm run dev                        # terminal 2, from web/  -> http://localhost:3000
```

Next.js page built from the design prototype (same markup and CSS in `web/app/globals.css` and
`web/lib/markup.js`); `web/lib/fundedlabs.js` loads everything from the API, which Next.js proxies
at `/api/*` to port 8000. Tabs: Explore (map shaded by active awards per state, filters, award
list, professor profile with Q3/Q6), Outreach (Q8 insert/update), Pipeline (ingest_run and
ingest_step), Ask (Q1-Q8 with SQL, rows, measured time and EXPLAIN / EXPLAIN ANALYZE).

## MongoDB comparison

```bash
brew tap mongodb/brew && brew install mongodb-community && brew services start mongodb-community
pip install -r requirements.txt
python mongo/build_documents.py     # CA awards active since FY2022 -> MongoDB `awards` + `publications`
python mongo/compare.py             # 4 operations in both systems on the same subset
```

`build_documents.py` embeds institution, investigators and yearly funding in each award document
and references publications by id. `compare.py` runs keyword search ($text vs LIKE/FULLTEXT),
funding by year ($unwind+$group vs GROUP BY), co-investigator pairs (double $unwind vs self-join)
and renaming an investigator (updateMany over every embedding document vs one UPDATE, both
reverted). It checks that both systems return the same answer and writes
`mongo/results/compare.json` and `compare.md`. Connection: `MONGO_URI` (default
mongodb://localhost:27017), `MONGO_DB` (fundedlabs).

## Live index demo

```bash
python demo/index_demo.py               # Q1: drop -> EXPLAIN ANALYZE + timing -> create INDEX(inst_id, end_date) -> again
python demo/index_demo.py --query Q7    # Q7 with INDEX(program, total_amount)
```

Prints both plans, median of 5 runs and Handler_read_* counters, then returns to the schema.sql
baseline (`--keep` leaves the index).

## Incremental NSF update

```bash
python ingest/nsf_update.py --dry-run
python ingest/nsf_update.py
```

Fetches awards made in the last 30 days (`--since`, `--until`, `--max` change that) from the NSF
Award Search API, maps them to the same fields `parse_nsf.py` takes from the bulk files, and
upserts them in one transaction: institutions match on the bulk load's name + city + state key,
awards on (agency, award number) and are updated if present, yearly funding rows are replaced,
and investigators match first on the people already linked to the award, then on normalized name
at the same institution (the API's PI id is a different identifier from the bulk files' NSF ID, so
it is not used). `--dry-run` shows what would be new or changed and writes nothing; running twice
is safe. Each run is logged as an `ingest_run` row with source `nsf_api` plus its `ingest_step`
rows, so it shows in the Pipeline tab. Restart the API afterwards: it caches the map totals.

## Notes for the experiments

- `schema.sql` declares only key and constraint indexes. InnoDB adds an index for each FK column
  that is not already the leading column of a key: `award(inst_id)`, `investigator(inst_id)`,
  `award_investigator(inv_id)`, `award_publication(pub_id)` and the two on `outreach`. Unindexed
  join experiments need the FK dropped first.
- `queries.sql` fixes `@today` so plans and results are reproducible; change it there.
- `sql/indexes.sql` holds the four indexes kept after the experiments, each with the measured
  reason for keeping it (and the candidates rejected after measurement).

## Limitations

- NIH has not yet published the FY2026 ExPORTER files, so NIH data covers FY2016–2025.
  `download.py` requests FY2026 and will pick the files up once they appear.
- Only NSF has an incremental update (`ingest/nsf_update.py`); NIH data changes only with a new
  bulk load.
- Q2 credits a multi-PI award's full amount to each of its PIs, so per-investigator totals add
  up to more than the award totals.
- NIH and NSF investigators are matched on normalized name plus a shared institution, one-to-one
  only. About 5% of NSF investigators are merged with an NIH record; people who moved
  institutions or whose names are recorded differently stay as separate records.
