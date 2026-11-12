# Experiment notes

Running log of unexpected optimizer behavior found during the experiments.

- **InnoDB drops the FK's own index when a new index can serve the FK.** Adding
  `INDEX(inst_id, end_date)` to `award` made InnoDB silently drop `fk_award_inst` (the index it
  created for the `inst_id` foreign key), since the new index also starts with `inst_id`. The
  composite index then can't be dropped (error 1553) until another index covers `inst_id`.
  `run_experiment.py` re-creates `fk_award_inst` before dropping candidates. So the "before" and
  "after" states in E1 differ by more than one index.
- **A reverse `(pub_id, award_id)` index on `award_publication` adds nothing.** Every InnoDB
  secondary index stores the primary key columns, so the FK index on `pub_id` is already
  `(pub_id, award_id)`. E5 compares the composite PK as a join index against ignoring it instead.
