"""Write a tiny SYNTHETIC raw dataset in the NIH ExPORTER and NSF download formats.

Used only to test the pipeline end to end; none of these awards, people or amounts are real.
It exercises: multi-year NIH merging with a same-year supplement, a multi-project subproject
row, a Windows-1252 file, an end-before-start record, NSF JSON and XML, a malformed file,
out-of-scope awards, and one person who appears in both agencies under different name formats.

  python tests/make_fixtures.py <raw_dir>
"""

import csv
import io
import json
import sys
import zipfile
from pathlib import Path

PRJ_COLS = ["APPLICATION_ID", "APPLICATION_TYPE", "ACTIVITY", "ADMINISTERING_IC", "CORE_PROJECT_NUM", "FY", "IC_NAME",
            "ORG_CITY", "ORG_COUNTRY", "ORG_DEPT", "ORG_NAME", "ORG_STATE", "PI_IDS", "PI_NAMEs",
            "PROJECT_START", "PROJECT_END", "PROJECT_TITLE", "SUBPROJECT_ID", "DIRECT_COST_AMT",
            "INDIRECT_COST_AMT", "TOTAL_COST", "TOTAL_COST_SUB_PROJECT"]

USC = ("LOS ANGELES", "UNITED STATES", "UNIVERSITY OF SOUTHERN CALIFORNIA", "CA")
UCLA = ("LOS ANGELES", "UNITED STATES", "UNIVERSITY OF CALIFORNIA LOS ANGELES", "CA")


def prj(app, core, fy, org, dept, ic, ids, names, start, end, title, total, sub="", app_type="5"):
    city, country, name, state = org
    return [app, app_type, core[:3], "GM", core, fy, ic, city, country, dept, name, state, ids, names,
            start, end, title, sub, "", "", total, ""]


FY2024 = [
    prj(9001, "R01GM000001", 2024, USC, "BIOCHEMISTRY", "NATIONAL INSTITUTE OF GENERAL MEDICAL SCIENCES",
        "1001 (contact); 1002;", "SMITH, JOHN A. (contact); NGUYEN, LINH;",
        "07/01/2023", "06/30/2028", "Machine learning for protein folding", "500000"),
    prj(9002, "P01CA000002", 2024, UCLA, "ONCOLOGY", "NATIONAL CANCER INSTITUTE", "1003;", "MULLER, KARL;",
        "09/01/2022", "08/31/2029", "Tumor microenvironment program", "2500000"),
    prj(9003, "P01CA000002", 2024, UCLA, "ONCOLOGY", "NATIONAL CANCER INSTITUTE", "1004;", "ROE, RICHARD;",
        "09/01/2022", "08/31/2029", "Core B imaging", "", sub="7001"),
    prj(9004, "R21MH000003", 2024, USC, "PSYCHIATRY", "NATIONAL INSTITUTE OF MENTAL HEALTH", "1002;",
        "NGUYEN, LINH;", "05/01/2025", "04/30/2024", "Record with end before start", "200000"),
    prj(9005, "R01CA000005", 2024, USC, "PATHOLOGY", "NATIONAL CANCER INSTITUTE", "1001;", "SMITH, JOHN A;",
        "01/01/2022", "12/31/2026", "Liquid biopsy markers", "400000"),
]
FY2025 = [
    prj(9101, "R01GM000001", 2025, USC, "BIOCHEMISTRY", "NATIONAL INSTITUTE OF GENERAL MEDICAL SCIENCES",
        "1001 (contact); 1002;", "SMITH, JOHN A. (contact); NGUYEN, LINH;",
        "07/01/2023", "06/30/2028", "Machine learning for protein folding (renewed title)", "520000"),
    prj(9102, "R01GM000001", 2025, USC, "BIOCHEMISTRY", "NATIONAL INSTITUTE OF GENERAL MEDICAL SCIENCES",
        "1001 (contact); 1002;", "SMITH, JOHN A. (contact); NGUYEN, LINH;",
        "07/01/2023", "06/30/2028", "Supplement", "50000", app_type="3"),
    prj(9103, "P01CA000002", 2025, UCLA, "ONCOLOGY", "NATIONAL CANCER INSTITUTE", "1003;", "MÜLLER, KARL;",
        "09/01/2022", "08/31/2029", "Tumor microenvironment program", "2600000"),
]

ABSTRACTS = [("APPLICATION_ID", "ABSTRACT_TEXT"), (9101, "Deep learning\tmodels for folding.\nSecond line."),
             (9103, "Program abstract."), (9005, "Liquid biopsy abstract.")]
PUBS = [("PMID", "PUB_TITLE", "JOURNAL_TITLE", "PUB_YEAR"), (1, "Folding with transformers", "Nature", 2024),
        (2, "Protein design", "Science", 2025), (3, "cfDNA markers", "Cancer Cell", 2026),
        (4, "Old paper", "Cell", 2019), (99, "Unlinked paper", "PLoS One", 2025)]
LINKS = [("PMID", "PROJECT_NUMBER"), (1, "R01GM000001"), (2, "R01GM000001"), (3, "R01CA000005"),
         (4, "R01GM000001"), (5, "R01GM000001"), (6, "R99XX999999")]


def csv_bytes(rows, encoding="utf-8"):
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)
    return buf.getvalue().encode(encoding)


def nsf_json(awd_id, title, eff, exp, amount, inst, pis, div, oblg):
    return json.dumps({
        "awd_id": awd_id, "agcy_id": "NSF", "awd_titl_txt": title, "awd_eff_date": eff, "awd_exp_date": exp,
        "tot_intn_awd_amt": amount, "awd_amount": amount, "awd_abstract_narration": "Abstract.<br/>More text.",
        "org_div_long_name": div,
        "inst": {"inst_name": inst[0], "inst_city_name": inst[1], "inst_state_code": inst[2],
                 "inst_country_name": "United States"},
        "pi": [{"pi_role": r, "pi_first_name": f, "pi_last_name": l, "pi_full_name": f"{f} {l}", "nsf_id": i,
                "pi_end_date": None} for r, f, l, i in pis],
        "pgm_ele": [{"pgm_ele_code": "779600", "pgm_ele_name": "Algorithmic Foundations"}],
        "oblg_fy": [{"fund_oblg_fiscal_yr": y, "fund_oblg_amt": a} for y, a in oblg],
    })


NSF_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rootTag><Award><AwardTitle>{title}</AwardTitle><AwardEffectiveDate>{eff}</AwardEffectiveDate>
<AwardExpirationDate>{exp}</AwardExpirationDate><AwardAmount>{amt}</AwardAmount><AwardID>{id}</AwardID>
<AbstractNarration>Older abstract.</AbstractNarration>
<Organization><Division><LongName>Division of Biological Infrastructure</LongName></Division></Organization>
<Investigator><FirstName>Maria</FirstName><LastName>Garcia</LastName><PI_FULL_NAME>Maria Garcia</PI_FULL_NAME>
<NSF_ID>000000102</NSF_ID><StartDate>09/01/2012</StartDate><EndDate/><RoleCode>Principal Investigator</RoleCode></Investigator>
<Institution><Name>University of Southern California</Name><CityName>Los Angeles</CityName>
<StateCode>CA</StateCode><CountryName>United States</CountryName></Institution>
<FUND_OBLG>2012~300000</FUND_OBLG></Award></rootTag>"""


def main(raw_dir):
    raw = Path(raw_dir)
    (raw / "nih").mkdir(parents=True, exist_ok=True)
    (raw / "nsf").mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(raw / "nih" / "projects_2024.zip", "w") as z:
        z.writestr("RePORTER_PRJ_C_FY2024.csv", b"\xef\xbb\xbf" + csv_bytes([PRJ_COLS] + FY2024))
    (raw / "nih" / "RePORTER_PRJ_C_FY2025.csv").write_bytes(csv_bytes([PRJ_COLS] + FY2025, "cp1252"))
    with zipfile.ZipFile(raw / "nih" / "abstracts_2025.zip", "w") as z:
        z.writestr("RePORTER_PRJABS_C_FY2025.csv", csv_bytes(ABSTRACTS))
    (raw / "nih" / "RePORTER_PUB_C_2025.csv").write_bytes(csv_bytes(PUBS))
    (raw / "nih" / "RePORTER_PUBLNK_C_2025.csv").write_bytes(csv_bytes(LINKS))

    usc = ("University of Southern California", "LOS ANGELES", "CA")
    with zipfile.ZipFile(raw / "nsf" / "nsf_2024.zip", "w") as z:
        z.writestr("2400001.json", nsf_json("2400001", "Robust machine learning theory", "2024-09-01", "2027-08-31",
                                            600000.0, usc, [("Principal Investigator", "John", "Smith", "000000101"),
                                                            ("Co-Principal Investigator", "Maria", "Garcia", "000000102")],
                                            "Division of Computing and Communication Foundations",
                                            [(2024, 400000.0), (2025, 200000.0)]))
        z.writestr("2400002.json", nsf_json("2400002", "Sensor networks", "2023-12-01", "2026-11-30", 300000.0,
                                            ("Stanford University", "STANFORD", "CA"),
                                            [("Principal Investigator", "Linh", "Nguyen", "000000103")],
                                            "Division of Computer and Network Systems", []))
    with zipfile.ZipFile(raw / "nsf" / "nsf_2012.zip", "w") as z:
        z.writestr("1200001.xml", NSF_XML.format(title="Herbarium digitization", eff="09/01/2012",
                                                 exp="08/31/2017", amt=300000, id="1200001"))
        z.writestr("1200002.xml", NSF_XML.format(title="Expired before scope", eff="09/01/2011",
                                                 exp="08/31/2014", amt=100000, id="1200002"))
        z.writestr("1200003.xml", "<rootTag><Award><AwardTitle>truncated")


if __name__ == "__main__":
    main(sys.argv[1])
