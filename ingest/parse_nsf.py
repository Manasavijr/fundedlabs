"""Parse NSF "Award Search: Download Awards by Year" files.

Expected under data/raw/nsf/: the per-year .zip files (or extracted files). Each archive holds
one file per award: JSON for downloads from 2025 on, XML before that. Both are handled.
"""

import json
import re
import xml.etree.ElementTree as ET

from common import (RAW_DIR, SCOPE_START, clean_text, fiscal_year, iter_source_files, log,
                    parse_amount, parse_date, parse_int)

NSF_DIR = RAW_DIR / "nsf"

_TAGS = re.compile(r"<[^>]+>")


def clean_abstract(s):
    if not s:
        return None
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    return clean_text(_TAGS.sub(" ", s), keep_newlines=True)


def first(d, *keys):
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v not in (None, "", []):
            return v
    return None


def norm_role(role):
    r = (role or "").lower()
    return "CO_PI" if "co" in r else "PI"


def from_json(doc):
    inst = first(doc, "inst") or {}
    pgm = first(doc, "pgm_ele") or []
    pis = []
    for p in first(doc, "pi") or []:
        pis.append({"nsf_id": clean_text(first(p, "nsf_id")),
                    "first": clean_text(first(p, "pi_first_name")),
                    "last": clean_text(first(p, "pi_last_name")),
                    "full_name": clean_text(first(p, "pi_full_name")),
                    "role": norm_role(first(p, "pi_role")),
                    "ended": bool(clean_text(first(p, "pi_end_date")))})
    funding = {}
    for o in first(doc, "oblg_fy") or []:
        fy, amt = parse_int(first(o, "fund_oblg_fiscal_yr")), parse_amount(first(o, "fund_oblg_amt"))
        if fy:
            funding[fy] = (funding.get(fy) or 0) + (amt or 0)
    return {
        "source_award_no": clean_text(first(doc, "awd_id")),
        "title": clean_text(first(doc, "awd_titl_txt"), 600),
        "abstract": clean_abstract(first(doc, "awd_abstract_narration")),
        "program": clean_text(first(doc, "org_div_long_name"), 255)
                   or clean_text(first(pgm[0], "pgm_ele_name") if pgm else None, 255),
        "start": parse_date(first(doc, "awd_eff_date")),
        "end": parse_date(first(doc, "awd_exp_date")),
        "total_amount": parse_amount(first(doc, "awd_amount", "tot_intn_awd_amt")),
        "inst": (clean_text(first(inst, "inst_name"), 255), clean_text(first(inst, "inst_city_name"), 100),
                 clean_text(first(inst, "inst_state_code")), clean_text(first(inst, "inst_country_name"), 100)),
        "pis": pis,
        "funding": funding,
    }


def _x(el, path):
    found = el.find(path)
    return found.text if found is not None else None


def from_xml(root):
    award = root.find("Award") if root.tag != "Award" else root
    if award is None:
        return None
    pis = []
    for inv in award.findall("Investigator"):
        pis.append({"nsf_id": clean_text(_x(inv, "NSF_ID")),
                    "first": clean_text(_x(inv, "FirstName")),
                    "last": clean_text(_x(inv, "LastName")),
                    "full_name": clean_text(_x(inv, "PI_FULL_NAME")),
                    "role": norm_role(_x(inv, "RoleCode")),
                    "ended": bool(clean_text(_x(inv, "EndDate")))})
    funding = {}
    for fo in award.findall("FUND_OBLG"):          # e.g. "2019~249999" (present in newer XML years)
        m = re.match(r"\s*(\d{4})\s*~\s*([\d.,-]+)", fo.text or "")
        if m:
            fy, amt = int(m.group(1)), parse_amount(m.group(2))
            funding[fy] = (funding.get(fy) or 0) + (amt or 0)
    return {
        "source_award_no": clean_text(_x(award, "AwardID")),
        "title": clean_text(_x(award, "AwardTitle"), 600),
        "abstract": clean_abstract(_x(award, "AbstractNarration")),
        "program": clean_text(_x(award, "Organization/Division/LongName"), 255)
                   or clean_text(_x(award, "ProgramElement/Text"), 255),
        "start": parse_date(_x(award, "AwardEffectiveDate")),
        "end": parse_date(_x(award, "AwardExpirationDate")),
        "total_amount": parse_amount(_x(award, "AwardAmount") or _x(award, "AwardTotalIntnAmount")),
        "inst": (clean_text(_x(award, "Institution/Name"), 255), clean_text(_x(award, "Institution/CityName"), 100),
                 clean_text(_x(award, "Institution/StateCode")), clean_text(_x(award, "Institution/CountryName"), 100)),
        "pis": pis,
        "funding": funding,
    }


def parse_all(steplog):
    """Return {award_no: award dict}. Later files win for the same award ID (amendments)."""
    awards = {}
    with steplog.step("nsf_parse_awards") as st:
        rows_in = bad = out_of_scope = 0
        for fname, opener in iter_source_files(NSF_DIR, (".json", ".xml")):
            rows_in += 1
            try:
                with opener() as f:
                    data = f.read()
                rec = from_json(json.loads(data)) if fname.lower().endswith(".json") else from_xml(ET.fromstring(data))
            except (ValueError, ET.ParseError) as e:
                bad += 1
                log(f"  unparseable {fname}: {e}")
                continue
            if not rec or not rec["source_award_no"]:
                bad += 1
                continue
            if rec["end"] and rec["end"] < SCOPE_START:
                out_of_scope += 1
                continue
            awards[rec["source_award_no"]] = rec
        st["rows_in"], st["rows_out"] = rows_in, len(awards)
        st["unparseable"], st["out_of_scope"] = bad, out_of_scope

    for a in awards.values():
        a["agency"] = "NSF"
        a["department"] = None
        if not a["funding"]:
            # no per-year obligations in the file: record the amount under the start fiscal year
            fy = fiscal_year(a["start"]) if a["start"] else None
            if fy:
                a["funding"] = {fy: a["total_amount"]}
        current = [p for p in a["pis"] if not p["ended"]] or a["pis"]   # drop PIs removed from the award
        a["pi_list"] = [{"key": ("nsf", p["nsf_id"]) if p["nsf_id"] else None,
                         "nsf_id": p["nsf_id"],
                         "full_name": p["full_name"] or clean_text(f"{p['first'] or ''} {p['last'] or ''}"),
                         "last_first": (p["last"], p["first"]),
                         "role": p["role"]}
                        for p in current]
    return awards
