"""Export a subset of the MySQL database into MongoDB as one document per award.

  python mongo/build_documents.py                 # California awards active since FY2022 (default)
  python mongo/build_documents.py --state NY      # another state
  python mongo/build_documents.py --since 2019-10-01

Subset: awards at institutions in --state whose end_date is on or after --since (default
2021-10-01, the start of FY2022), i.e. California awards active at some point in the last five
fiscal years. This is the subset named in the proposal.

Document shape (collection `awards`):

  { _id: award_id, agency: "NIH", source_award_no, title, abstract, department, program,
    start_date, end_date, total_amount,
    institution:   { inst_id, name, city, state },                 <- embedded (N:1, copied)
    investigators: [ { inv_id, full_name, role, nih_profile_id, nsf_id } ],   <- embedded (M:N)
    funding:       [ { fiscal_year, amount } ],                    <- embedded (1:N, owned)
    pub_ids:       [ pub_id, ... ] }                               <- references

and collection `publications` ({ _id: pub_id, pmid, doi, title, journal, pub_year }) holding only
the publications the subset references. Embedding vs referencing is the main design
choice: the award "owns" its funding rows, so they are embedded; an investigator is shared by
many awards, so embedding copies them (see compare.py op 4); publications can be shared by many
awards and are large, so they are referenced.

Amounts are stored as Decimal128 so sums match MySQL DECIMAL exactly. Dates are stored as
BSON dates (midnight UTC).

MySQL connection: same MYSQL_* variables as ingest/pipeline.py.
MongoDB connection: MONGO_URI (default mongodb://localhost:27017), MONGO_DB (default fundedlabs).
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ingest"))
from pipeline import connect  # noqa: E402

RESULTS = ROOT / "mongo" / "results"
BATCH = 2000


def mongo_db(client=None):
    if client is None:
        from pymongo import MongoClient
        client = MongoClient(os.environ.get("MONGO_URI", "mongodb://localhost:27017"))
    return client[os.environ.get("MONGO_DB", "fundedlabs")]


def bson_value(v):
    from bson.decimal128 import Decimal128
    if isinstance(v, Decimal):
        return Decimal128(v)
    if isinstance(v, date) and not isinstance(v, datetime):
        return datetime(v.year, v.month, v.day)
    return v


def rows(cur, sql, params=()):
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def subset_award_ids(cur, state, since):
    cur.execute("""SELECT a.award_id FROM award a JOIN institution i ON i.inst_id = a.inst_id
                   WHERE i.state = %s AND a.end_date >= %s ORDER BY a.award_id""", (state, since))
    return [r[0] for r in cur.fetchall()]


def build_batch(cur, ids):
    ph = ",".join(["%s"] * len(ids))
    awards = rows(cur, f"""
        SELECT a.award_id, g.code AS agency, a.source_award_no, a.title, a.abstract, a.department,
               a.program, a.start_date, a.end_date, a.total_amount,
               i.inst_id, i.name AS inst_name, i.city, i.state
        FROM award a JOIN agency g ON g.agency_id = a.agency_id
        JOIN institution i ON i.inst_id = a.inst_id
        WHERE a.award_id IN ({ph})""", ids)
    invs, funding, pubs = {}, {}, {}
    for r in rows(cur, f"""
            SELECT ai.award_id, v.inv_id, v.full_name, ai.role, v.nih_profile_id, v.nsf_id
            FROM award_investigator ai JOIN investigator v ON v.inv_id = ai.inv_id
            WHERE ai.award_id IN ({ph}) ORDER BY ai.award_id, ai.role, v.inv_id""", ids):
        invs.setdefault(r.pop("award_id"), []).append(r)
    for r in rows(cur, f"""SELECT award_id, fiscal_year, amount FROM award_funding
                           WHERE award_id IN ({ph}) ORDER BY award_id, fiscal_year""", ids):
        funding.setdefault(r.pop("award_id"), []).append(r)
    for r in rows(cur, f"""SELECT award_id, pub_id FROM award_publication
                           WHERE award_id IN ({ph}) ORDER BY award_id, pub_id""", ids):
        pubs.setdefault(r["award_id"], []).append(r["pub_id"])

    docs = []
    for a in awards:
        aid = a["award_id"]
        doc = {
            "_id": aid,
            "agency": a["agency"],
            "source_award_no": a["source_award_no"],
            "title": a["title"],
            "abstract": a["abstract"],
            "department": a["department"],
            "program": a["program"],
            "start_date": a["start_date"],
            "end_date": a["end_date"],
            "total_amount": a["total_amount"],
            "institution": {"inst_id": a["inst_id"], "name": a["inst_name"], "city": a["city"],
                            "state": a["state"]},
            "investigators": invs.get(aid, []),
            "funding": funding.get(aid, []),
            "pub_ids": pubs.get(aid, []),
        }
        docs.append(convert(doc))
    return docs


def convert(obj):
    if isinstance(obj, dict):
        return {k: convert(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [convert(v) for v in obj]
    return bson_value(obj)


def build(db, state, since):
    from pymongo import ASCENDING, DESCENDING, TEXT
    conn = connect()
    cur = conn.cursor()
    t0 = time.perf_counter()
    ids = subset_award_ids(cur, state, since)
    print(f"subset: {len(ids):,} awards ({state}, end_date >= {since})")

    db.awards.drop()
    db.publications.drop()
    n_docs, pub_ids = 0, set()
    for i in range(0, len(ids), BATCH):
        docs = build_batch(cur, ids[i:i + BATCH])
        for d in docs:
            pub_ids.update(d.get("pub_ids", []))
        if docs:
            db.awards.insert_many(docs, ordered=False)
        n_docs += len(docs)
        print(f"  awards {n_docs:,}/{len(ids):,}", end="\r", flush=True)
    print()

    pub_list = sorted(pub_ids)
    for i in range(0, len(pub_list), BATCH):
        chunk = pub_list[i:i + BATCH]
        ph = ",".join(["%s"] * len(chunk))
        prs = rows(cur, f"SELECT pub_id, pmid, doi, title, journal, pub_year FROM publication "
                        f"WHERE pub_id IN ({ph})", chunk)
        docs = [convert({"_id": p.pop("pub_id"), **p}) for p in prs]
        if docs:
            db.publications.insert_many(docs, ordered=False)
    load_s = time.perf_counter() - t0
    print(f"publications: {len(pub_list):,}")

    # Indexes the four comparison operations use. _id (award_id) is indexed automatically.
    t1 = time.perf_counter()
    db.awards.create_index([("title", TEXT), ("abstract", TEXT)], name="text_title_abstract",
                           default_language="english")
    db.awards.create_index([("investigators.inv_id", ASCENDING)], name="ix_investigators_inv")
    db.awards.create_index([("institution.inst_id", ASCENDING), ("end_date", DESCENDING)],
                           name="ix_inst_end")
    index_s = time.perf_counter() - t1
    conn.close()
    return {"state": state, "since": str(since), "awards": n_docs, "publications": len(pub_list),
            "load_seconds": round(load_s, 2), "index_build_seconds": round(index_s, 2)}


def collection_stats(db):
    out = {}
    for name in ("awards", "publications"):
        try:
            s = db.command("collStats", name)
        except Exception as e:  # mongomock and some hosted tiers lack collStats
            out[name] = {"error": str(e)}
            continue
        out[name] = {k: s.get(k) for k in ("count", "size", "avgObjSize", "storageSize",
                                           "totalIndexSize", "indexSizes")}
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default="CA")
    ap.add_argument("--since", default="2021-10-01")
    args = ap.parse_args()
    db = mongo_db()
    info = build(db, args.state, date.fromisoformat(args.since))
    info["built_at"] = datetime.now().isoformat(timespec="seconds")
    info["collections"] = collection_stats(db)
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "build.json").write_text(json.dumps(info, indent=2, default=str))
    print(json.dumps(info, indent=2, default=str))
    print(f"wrote {(RESULTS / 'build.json').relative_to(ROOT)}")


if __name__ == "__main__":
    main()
