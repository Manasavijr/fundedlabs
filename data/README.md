# data/

Not in version control. `python ingest/pipeline.py download` fills `raw/nih/` and `raw/nsf/`,
`transform` writes `clean/*.tsv` and `clean/transform_log.json`, and `ingest/nsf_update.py`
saves its API responses under `raw/nsf_api/`.
