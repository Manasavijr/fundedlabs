# MongoDB vs MySQL comparison

Subset: state CA, end_date >= 2021-10-01. Mongo documents: 30,726. Run 2026-11-11T15:48:42. MongoDB 9.0.8, MySQL 9.7.4.

Timings are this script's measured medians on this machine; MySQL filters the full database down to the subset, MongoDB holds only the subset, so they are not a like-for-like engine benchmark.

## 1. Keyword search

- MongoDB `$text`: 2038 awards, 26.27 ms (min 25.95, max 36.58, n=5); stages ['PROJECTION_SIMPLE', 'TEXT_MATCH', 'FETCH', 'OR', 'IXSCAN']
- MySQL `LIKE`: 2038 awards, 452.37 ms (min 450.15, max 455.89, n=5)
- Overlap: {'both': 2038, 'mongo_only': 0, 'mysql_only': 0, 'mongo_only_examples': [], 'mysql_only_examples': []}

## 2. Funding by year and agency

- Results match: **True**
- MongoDB: 21 groups, 47.38 ms (min 46.95, max 47.54, n=5); stages ['PROJECTION_SIMPLE', 'COLLSCAN'], docs examined 30726
- MySQL: 21 groups, 341.72 ms (min 339.89, max 346.29, n=5)

## 3. Co-investigator pairs

- Results match: **True**
- MongoDB: 18886 distinct_pairs, 38.53 ms (min 38.08, max 41.48, n=5); stages ['PROJECTION_DEFAULT', 'COLLSCAN'], docs examined 30726
- MySQL: 18886 distinct_pairs, 304.29 ms (min 298.82, max 330.76, n=5)

## 4. Rename an investigator

- Investigator 143954 (Terry B Appelgate)
- MongoDB: 28 documents rewritten, 1.63 ms (min 1.5, max 1.68, n=5) per rename+revert
- MySQL: 1 row, 1.25 ms (min 0.99, max 1.32, n=5) per update+rollback

