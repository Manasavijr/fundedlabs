"""Parse NIH ExPORTER bulk CSVs into one record per core project.

Expected under data/raw/nih/ (plain .csv or the .zip files as downloaded):
  RePORTER_PRJ_C_FY{year}*.csv     projects, one row per application (per fiscal year)
  RePORTER_PRJABS_C_FY{year}*.csv  abstracts keyed by APPLICATION_ID
  RePORTER_PUB_C_{year}*.csv       publications (PMID, title, journal, year)
  RePORTER_PUBLNK_C_{year}*.csv    PMID <-> core project number links

ExPORTER publishes one record per application per fiscal year. Records are merged on
CORE_PROJECT_NUM (e.g. R01GM123456) into a single award with yearly funding rows.
"""

import re
from collections import Counter

from common import (RAW_DIR, SCOPE_START, clean_text, iter_csv_dicts, iter_source_files, log,
                    parse_amount, parse_date, parse_int, split_last_first)

NIH_DIR = RAW_DIR / "nih"


def classify(name):
    n = name.upper().rsplit("!", 1)[-1]
    if "PRJABS" in n:
        return "abstracts"
    if "PUBLNK" in n:
        return "links"
    if "_PRJ_" in n or n.startswith("REPORTER_PRJ"):
        return "projects"
    if "_PUB_" in n or n.startswith("REPORTER_PUB"):
        return "pubs"
    return None


def files_of(kind):
    return ((n, o) for n, o in iter_source_files(NIH_DIR, (".csv",)) if classify(n) == kind)


def parse_pi_list(ids_field, names_field):
    """PI_IDS '1234567 (contact); 7654321;' and PI_NAMEs 'SMITH, JOHN A. (contact); DOE, JANE;'
    are parallel semicolon lists. Returns [(profile_id, full_name, is_contact)]."""
    ids = [s.strip() for s in (ids_field or "").split(";") if s.strip()]
    names = [s.strip() for s in (names_field or "").split(";") if s.strip()]
    out = []
    for i, raw_id in enumerate(ids):
        pid = parse_int(raw_id.split("(")[0])
        if pid is None:
            continue
        name = names[i] if i < len(names) else ""
        is_contact = "(contact)" in raw_id.lower() or "(contact)" in name.lower()
        name = clean_text(name.replace("(contact)", "").replace("(CONTACT)", ""))
        out.append((pid, name, is_contact))
    return out


# Extramural grant core numbers: activity code (R01, K23, UG3, ...), institute code, 6-digit serial.
# Contracts and NIH's own intramural projects (Z..., N01...) don't fund university labs, so they're dropped.
EXTRAMURAL_GRANT = re.compile(r"^(?![NZ])[A-Z][0-9A-Z]{2}[A-Z]{2}\d{6}$")


def parse_projects(steplog):
    """Return {core_project_num: award dict}."""
    awards = {}
    with steplog.step("nih_parse_projects") as st:
        rows_in = skipped_sub = skipped_nocore = skipped_not_grant = 0
        for fname, opener in files_of("projects"):
            log(f"  reading {fname}")
            for row in iter_csv_dicts(opener):
                rows_in += 1
                if clean_text(row.get("SUBPROJECT_ID")):
                    skipped_sub += 1      # components of multi-project awards; parent row carries totals
                    continue
                core = clean_text(row.get("CORE_PROJECT_NUM"))
                if not core:
                    skipped_nocore += 1
                    continue
                if not EXTRAMURAL_GRANT.match(core):
                    skipped_not_grant += 1    # contracts (75N..., 26..., N01...) and intramural (Z...)
                    continue
                fy = parse_int(row.get("FY"))
                app_id = parse_int(row.get("APPLICATION_ID"))
                a = awards.get(core)
                if a is None:
                    a = awards[core] = {"agency": "NIH", "source_award_no": core, "funding": {},
                                        "pis": {}, "start": None, "end": None, "_latest": (-1, False, -1),
                                        "app_id": None}
                start, end = parse_date(row.get("PROJECT_START")), parse_date(row.get("PROJECT_END"))
                if start and (a["start"] is None or start < a["start"]):
                    a["start"] = start
                if end and (a["end"] is None or end > a["end"]):
                    a["end"] = end

                amount = parse_amount(row.get("TOTAL_COST"))
                if amount is None:
                    d, i = parse_amount(row.get("DIRECT_COST_AMT")), parse_amount(row.get("INDIRECT_COST_AMT"))
                    if d is not None or i is not None:
                        amount = (d or 0) + (i or 0)
                if fy is not None:
                    prev = a["funding"].get(fy)
                    # several applications (e.g. supplements) can share a fiscal year: sum them
                    a["funding"][fy] = amount if prev is None else (prev + amount if amount is not None else prev)

                for pid, name, is_contact in parse_pi_list(row.get("PI_IDS"), row.get("PI_NAMES")):
                    p = a["pis"].setdefault(pid, {"names": Counter(), "contact": False})
                    if name:
                        p["names"][name] += 1
                    p["contact"] |= is_contact

                # descriptive attributes come from the most recent fiscal year's record,
                # preferring the main award over administrative supplements (application type 3)
                is_supplement = clean_text(row.get("APPLICATION_TYPE")) == "3"
                key = (fy or -1, not is_supplement, app_id or -1)
                if key >= a["_latest"]:
                    a["_latest"] = key
                    a["app_id"] = app_id
                    a["title"] = clean_text(row.get("PROJECT_TITLE"), 600)
                    a["department"] = clean_text(row.get("ORG_DEPT"), 255)
                    a["program"] = clean_text(row.get("IC_NAME"), 255)
                    a["inst"] = (clean_text(row.get("ORG_NAME"), 255), clean_text(row.get("ORG_CITY"), 100),
                                 clean_text(row.get("ORG_STATE")), clean_text(row.get("ORG_COUNTRY"), 100))
        st["rows_in"] = rows_in
        st["rows_out"] = len(awards)
        st["skipped_subprojects"] = skipped_sub
        st["skipped_no_core_number"] = skipped_nocore
        st["skipped_contracts_and_intramural"] = skipped_not_grant

    # scope: active in FY2016+ (by end date, or by any funded fiscal year if dates are missing)
    for core in [c for c, a in awards.items()
                 if not ((a["end"] and a["end"] >= SCOPE_START) or any(fy >= 2016 for fy in a["funding"]))]:
        del awards[core]
    return awards


def attach_abstracts(awards, steplog):
    want = {a["app_id"]: a for a in awards.values() if a["app_id"] is not None}
    with steplog.step("nih_parse_abstracts") as st:
        rows_in = found = 0
        for fname, opener in files_of("abstracts"):
            log(f"  reading {fname}")
            for row in iter_csv_dicts(opener):
                rows_in += 1
                a = want.get(parse_int(row.get("APPLICATION_ID")))
                if a is not None:
                    a["abstract"] = clean_text(row.get("ABSTRACT_TEXT"), keep_newlines=True)
                    found += 1
        st["rows_in"], st["rows_out"] = rows_in, found


def parse_publications(core_numbers, steplog):
    """Return (links {core: set(pmid)}, pubs {pmid: (title, journal, year)}) for loaded awards only."""
    links = {}
    with steplog.step("nih_parse_pub_links") as st:
        rows_in = 0
        for fname, opener in files_of("links"):
            log(f"  reading {fname}")
            for row in iter_csv_dicts(opener):
                rows_in += 1
                core = clean_text(row.get("PROJECT_NUMBER"))
                pmid = parse_int(row.get("PMID"))
                if core in core_numbers and pmid:
                    links.setdefault(core, set()).add(pmid)
        st["rows_in"] = rows_in
        st["rows_out"] = sum(len(s) for s in links.values())

    wanted = set().union(*links.values()) if links else set()
    pubs = {}
    with steplog.step("nih_parse_publications") as st:
        rows_in = 0
        for fname, opener in files_of("pubs"):
            log(f"  reading {fname}")
            for row in iter_csv_dicts(opener):
                rows_in += 1
                pmid = parse_int(row.get("PMID"))
                if pmid in wanted:
                    pubs[pmid] = (clean_text(row.get("PUB_TITLE"), 1000),
                                  clean_text(row.get("JOURNAL_TITLE"), 300),
                                  parse_int(row.get("PUB_YEAR")))
        st["rows_in"], st["rows_out"] = rows_in, len(pubs)
    return links, pubs


def parse_all(steplog):
    awards = parse_projects(steplog)
    attach_abstracts(awards, steplog)
    links, pubs = parse_publications(set(awards), steplog)
    for a in awards.values():
        a["pi_list"] = [{"key": ("nih", pid), "nih_profile_id": pid,
                         "full_name": p["names"].most_common(1)[0][0] if p["names"] else None,
                         "last_first": split_last_first(p["names"].most_common(1)[0][0]) if p["names"] else (None, None),
                         # NIH multi-PI awards have equal PIs; all are recorded as role PI
                         "role": "PI"}
                        for pid, p in a["pis"].items()]
        a["total_amount"] = (sum(v for v in a["funding"].values() if v is not None)
                             if any(v is not None for v in a["funding"].values()) else None)
    return awards, links, pubs
