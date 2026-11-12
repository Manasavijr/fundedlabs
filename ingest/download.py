"""Download the bulk source files for the historical load into data/raw/.

  python ingest/download.py                 # everything in scope
  python ingest/download.py --only nsf      # one agency
  python ingest/download.py --years 2024 2025

Files that already exist are skipped, so the script can be re-run after an interruption.

The URL templates below follow the download pages' link patterns
(https://reporter.nih.gov/exporter). NSF's "Download Awards by Year" page gets signed links to
its yearly zips from NSF_FILE_LIST_URL, so the script asks that endpoint for them too.
If an agency changes its links, update the template or download the files by hand into
data/raw/nih/ or data/raw/nsf/; the parsers only care about the file names inside.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW_DIR, log  # noqa: E402

NIH_URLS = {
    # ExPORTER: one zip per fiscal year (projects, abstracts) or calendar year (publications, links)
    "projects":  "https://reporter.nih.gov/exporter/projects/download/{year}",
    "abstracts": "https://reporter.nih.gov/exporter/abstracts/download/{year}",
    "pubs":      "https://reporter.nih.gov/exporter/publications/download/{year}",
    "links":     "https://reporter.nih.gov/exporter/linktables/download/{year}",
}
# Returns {"files": [{"fileName": "2024.zip", "downloadUrl": ..., "size": ...}, ...]}.
# (The old ...awardsearch/download?DownloadFileName= links now redirect to a search page.)
NSF_FILE_LIST_URL = "https://api.nsf.gov/services/v2/s3/list-files"

NIH_YEARS = range(2016, 2027)
# NSF files are grouped by the year an award started; a 5-year award from 2011 is still active
# in FY2016, so pull earlier start years too (out-of-scope awards are dropped while parsing).
NSF_YEARS = range(2010, 2027)


def is_zip(path):
    with open(path, "rb") as f:
        return f.read(4) == b"PK\x03\x04"


def fetch(url, dest):
    if dest.exists() and dest.stat().st_size > 0:
        if is_zip(dest):
            log(f"  have {dest.relative_to(RAW_DIR)}")
            return True
        log(f"  {dest.relative_to(RAW_DIR)} is not a zip; downloading it again")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    for attempt in range(4):
        try:
            log(f"  get  {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "FundedLabs/1.0"})
            with urllib.request.urlopen(req, timeout=300) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f, 1 << 20)
            if not is_zip(tmp):
                # a redirect to an HTML page instead of the file
                log(f"  got a web page, not a zip, from {url}")
                tmp.unlink()
                return False
            tmp.replace(dest)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # e.g. NIH FY2026, which is not on the ExPORTER site yet
                log("  not published (404); skipping")
                return False
            log(f"  failed ({e}); retrying")
            time.sleep(2 ** (attempt + 1))
        except OSError as e:
            log(f"  failed ({e}); retrying")
            time.sleep(2 ** (attempt + 1))
    log(f"  could not download {url}")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["nih", "nsf"])
    ap.add_argument("--years", type=int, nargs="*")
    args = ap.parse_args(argv)
    missing = []
    if args.only in (None, "nih"):
        for year in args.years or NIH_YEARS:
            for kind, tmpl in NIH_URLS.items():
                if not fetch(tmpl.format(year=year), RAW_DIR / "nih" / f"{kind}_{year}.zip"):
                    missing.append(f"nih {kind} {year}")
                time.sleep(1)
    if args.only in (None, "nsf"):
        with urllib.request.urlopen(NSF_FILE_LIST_URL, timeout=60) as r:
            nsf_files = {f["fileName"]: f for f in json.load(r)["files"]}
        for year in args.years or NSF_YEARS:
            entry = nsf_files.get(f"{year}.zip")
            if entry is None or not fetch(entry["downloadUrl"], RAW_DIR / "nsf" / f"nsf_{year}.zip"):
                missing.append(f"nsf {year}")
            time.sleep(1)
    if missing:
        log("not downloaded: " + ", ".join(missing))

if __name__ == "__main__":
    main()
