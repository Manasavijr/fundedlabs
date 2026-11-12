"""Integrate parsed NIH and NSF records: validate awards, deduplicate institutions,
match investigators across agencies, assign surrogate keys, and write data/clean/*.tsv
in the column order sql/load.sql expects."""

from collections import Counter, defaultdict
from datetime import date

from common import CLEAN_DIR, TsvWriter, clean_text, inst_key, log, person_name_norm

AGENCY_ID = {"NIH": 1, "NSF": 2}


class Institutions:
    def __init__(self):
        self.by_key = {}

    def get_id(self, name, city, state, country):
        if not name:
            return None
        key = inst_key(name, city, state)
        rec = self.by_key.get(key)
        if rec is None:
            rec = self.by_key[key] = {"id": len(self.by_key) + 1, "names": Counter(),
                                      "cities": Counter(), "countries": Counter()}
        rec["names"][name] += 1
        rec["cities"][city or ""] += 1
        if country:
            rec["countries"][country.upper()] += 1
        return rec["id"]

    def rows(self):
        for (_, _, state), rec in sorted(self.by_key.items(), key=lambda kv: kv[1]["id"]):
            yield (rec["id"], rec["names"].most_common(1)[0][0], rec["cities"].most_common(1)[0][0],
                   state, rec["countries"].most_common(1)[0][0] if rec["countries"] else None)


def build(nih_awards, nih_links, nih_pubs, nsf_awards, steplog):
    insts = Institutions()
    awards = []
    award_id_by_source = {}

    # ---- awards -----------------------------------------------------------
    with steplog.step("clean_awards") as st:
        rejected = Counter()
        sources = [nih_awards[k] for k in sorted(nih_awards)] + [nsf_awards[k] for k in sorted(nsf_awards)]
        for a in sources:
            if not a.get("title"):
                rejected["no_title"] += 1
                continue
            if a["start"] and a["end"] and a["end"] < a["start"]:
                rejected["end_before_start"] += 1
                continue
            if a["total_amount"] is not None and a["total_amount"] < 0:
                rejected["negative_amount"] += 1
                continue
            inst_id = insts.get_id(*a["inst"])
            if inst_id is None:
                rejected["no_institution"] += 1
                continue
            a["inst_id"] = inst_id
            a["award_id"] = len(awards) + 1
            award_id_by_source[(a["agency"], a["source_award_no"])] = a["award_id"]
            awards.append(a)
        st["rows_in"], st["rows_out"] = len(sources), len(awards)
        st["rejected"] = dict(rejected)
        st["institutions"] = len(insts.by_key)

    # ---- investigators ------------------------------------------------------
    with steplog.step("match_investigators") as st:
        persons = {}                       # person key -> record
        links = []                         # (award_id, person key, role)
        unnamed = 0
        for a in awards:
            when = a["end"] or a["start"] or date.min
            for p in a["pi_list"]:
                last, first = p["last_first"]
                norm = person_name_norm(last, first)
                if norm is None:
                    unnamed += 1
                    continue
                key = p["key"] or ("nsf-name", norm, a["inst_id"])   # NSF record without an NSF ID
                rec = persons.get(key)
                if rec is None:
                    rec = persons[key] = {"agency": a["agency"], "nih_profile_id": p.get("nih_profile_id"),
                                          "nsf_id": p.get("nsf_id"), "names": Counter(), "norms": Counter(),
                                          "insts": {}}
                if p["full_name"]:
                    rec["names"][p["full_name"]] += 1
                rec["norms"][norm] += 1
                # an award with no dates still counts as one of the person's institutions
                if a["inst_id"] not in rec["insts"] or when > rec["insts"][a["inst_id"]]:
                    rec["insts"][a["inst_id"]] = when
                links.append((a["award_id"], key, p["role"]))

        for rec in persons.values():
            rec["norm"] = rec["norms"].most_common(1)[0][0]
            rec["home_inst"] = max(rec["insts"].items(), key=lambda kv: kv[1])[0]

        # Cross-agency match on normalized name + any shared institution, one-to-one only.
        nih_index = defaultdict(set)
        nih_insts = set()
        for key, rec in persons.items():
            if rec["agency"] == "NIH":
                for inst_id in rec["insts"]:
                    nih_index[(rec["norm"], inst_id)].add(key)
                    nih_insts.add(inst_id)
        candidate = {}
        for key, rec in persons.items():
            if rec["agency"] == "NSF":
                cands = set()
                for inst_id in rec["insts"]:
                    cands |= nih_index.get((rec["norm"], inst_id), set())
                if cands:
                    candidate[key] = cands
        claimed = Counter(next(iter(c)) for c in candidate.values() if len(c) == 1)
        merged_into = {}
        ambiguous = 0
        for key, cands in candidate.items():
            if len(cands) == 1 and claimed[next(iter(cands))] == 1:
                merged_into[key] = next(iter(cands))
            else:
                ambiguous += 1

        nsf_people = [k for k, r in persons.items() if r["agency"] == "NSF"]
        nsf_at_nih_inst = [k for k in nsf_people if persons[k]["insts"].keys() & nih_insts]
        stats = {
            "nih_investigators": sum(1 for r in persons.values() if r["agency"] == "NIH"),
            "nsf_investigators": len(nsf_people),
            "nsf_at_nih_funded_institutions": len(nsf_at_nih_inst),
            "matched": len(merged_into),
            "ambiguous_not_merged": ambiguous,
            "pi_entries_without_usable_name": unnamed,
        }
        stats["match_rate_all_nsf"] = round(len(merged_into) / len(nsf_people), 4) if nsf_people else None
        stats["match_rate_nsf_at_nih_institutions"] = (round(len(merged_into) / len(nsf_at_nih_inst), 4)
                                                       if nsf_at_nih_inst else None)

        # assign inv_ids: NIH people first, then unmatched NSF people
        inv_id = {}
        inv_rows = {}
        for key in sorted((k for k, r in persons.items() if r["agency"] == "NIH"), key=str):
            rec = persons[key]
            inv_id[key] = len(inv_rows) + 1
            inv_rows[inv_id[key]] = rec
        for key in sorted(nsf_people, key=str):
            rec = persons[key]
            if key in merged_into:
                target = inv_id[merged_into[key]]
                inv_id[key] = target
                base = inv_rows[target]
                base["nsf_id"] = rec["nsf_id"]
                base["nsf_names"] = rec["names"]
                # home institution = most recent across both agencies
                for i, when in rec["insts"].items():
                    if when > base["insts"].get(i, date.min):
                        base["insts"][i] = when
                base["home_inst"] = max(base["insts"].items(), key=lambda kv: kv[1])[0]
            else:
                inv_id[key] = len(inv_rows) + 1
                inv_rows[inv_id[key]] = rec

        ai = {}
        for award_id, key, role in links:
            k = (award_id, inv_id[key])
            if ai.get(k) != "PI":          # same person listed twice: keep the PI role
                ai[k] = role
        st["rows_in"], st["rows_out"] = len(persons), len(inv_rows)
        st.update(stats)
        log(f"  investigator matching: {stats}")

    # ---- write ------------------------------------------------------------
    counts = {}
    with steplog.step("write_clean_files") as st:
        CLEAN_DIR.mkdir(parents=True, exist_ok=True)
        with TsvWriter(CLEAN_DIR / "institution.tsv") as w:
            for row in insts.rows():
                w.write(*row)
            counts["institution"] = w.count

        with TsvWriter(CLEAN_DIR / "investigator.tsv") as w:
            for i, rec in sorted(inv_rows.items()):
                names = rec.get("nsf_names") or rec["names"]     # NSF names are mixed case; prefer them
                full = names.most_common(1)[0][0] if names else rec["norm"].replace("|", ", ").upper()
                w.write(i, clean_text(full, 255), rec["norm"][:160], rec["nih_profile_id"], rec["nsf_id"],
                        rec["home_inst"])
            counts["investigator"] = w.count

        with TsvWriter(CLEAN_DIR / "award.tsv") as wa, \
             TsvWriter(CLEAN_DIR / "award_funding.tsv") as wf:
            for a in awards:
                wa.write(a["award_id"], AGENCY_ID[a["agency"]], a["inst_id"], a["source_award_no"], a["title"],
                         a.get("abstract"), a.get("department"), a.get("program"), a["start"], a["end"],
                         a["total_amount"])
                for fy in sorted(a["funding"]):
                    if 1950 <= fy <= 2100:
                        wf.write(a["award_id"], fy, a["funding"][fy])
            counts["award"], counts["award_funding"] = wa.count, wf.count

        with TsvWriter(CLEAN_DIR / "award_investigator.tsv") as w:
            for (award_id, iid), role in sorted(ai.items()):
                w.write(award_id, iid, role)
            counts["award_investigator"] = w.count

        pub_id = {pmid: n for n, pmid in enumerate(sorted(nih_pubs), start=1)}
        with TsvWriter(CLEAN_DIR / "publication.tsv") as w:
            for pmid, n in pub_id.items():
                title, journal, year = nih_pubs[pmid]
                w.write(n, pmid, None, title, journal, year)
            counts["publication"] = w.count

        with TsvWriter(CLEAN_DIR / "award_publication.tsv") as w:
            pairs = set()
            for core, pmids in nih_links.items():
                aid = award_id_by_source.get(("NIH", core))
                if aid is None:
                    continue
                for pmid in pmids:
                    if pmid in pub_id:              # link files can cite PMIDs absent from the pub files
                        pairs.add((aid, pub_id[pmid]))
            for aid, pid in sorted(pairs):
                w.write(aid, pid)
            counts["award_publication"] = w.count

        st["rows_in"] = len(awards)
        st["rows_out"] = sum(counts.values())
        st["files"] = counts
    return counts, stats
