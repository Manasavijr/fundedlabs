"""Shared helpers: normalization, parsing, file access and MySQL-ready TSV output."""

import codecs
import csv
import io
import re
import sys
import time
import unicodedata
import zipfile
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"

# Scope: awards active in federal fiscal years 2016-2026 (FY2016 starts 2015-10-01).
SCOPE_START = date(2015, 10, 1)
SCOPE_FY = (2016, 2026)

csv.field_size_limit(2**31 - 1)


# ---------------------------------------------------------------------------
# Value cleaning
# ---------------------------------------------------------------------------

_WS = re.compile(r"\s+")


def clean_text(value, max_len=None, keep_newlines=False):
    """Collapse whitespace (keeping paragraph breaks if asked); return None for blank values."""
    if value is None:
        return None
    if keep_newlines:
        lines = (_WS.sub(" ", ln).strip() for ln in str(value).replace("\r\n", "\n").split("\n"))
        s = re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    else:
        s = _WS.sub(" ", str(value)).strip()
    if not s:
        return None
    if max_len and len(s) > max_len:
        s = s[:max_len]
    return s


_DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p",
                 "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


def parse_date(value):
    s = clean_text(value)
    if not s:
        return None
    s = s.split(".")[0].rstrip("Z")  # drop fractional seconds / UTC marker
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def parse_amount(value):
    s = clean_text(value)
    if not s:
        return None
    s = s.replace("$", "").replace(",", "")
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    return d.quantize(Decimal("0.01"))


def parse_int(value):
    s = clean_text(value)
    if not s:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fiscal_year(d):
    """US federal fiscal year: FY N runs Oct 1 of N-1 through Sep 30 of N."""
    return d.year + 1 if d.month >= 10 else d.year


def ascii_upper(s):
    s = unicodedata.normalize("NFKD", s or "")
    return s.encode("ascii", "ignore").decode("ascii").upper()


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_NAME_SUFFIXES = {"JR", "SR", "II", "III", "IV", "V", "MD", "PHD", "DR", "PROF", "DVM", "MPH"}


def _name_tokens(s):
    s = ascii_upper(s).replace("'", "").replace("`", "").replace("\u2019", "")
    s = re.sub(r"\([^)]*\)", " ", s)        # "(contact)"
    s = re.sub(r"[^A-Z ]", " ", s)
    return [t for t in s.split() if t not in _NAME_SUFFIXES]


def person_name_norm(last, first):
    """'last|first' in lower case: last name with spaces/hyphens/apostrophes removed,
    first token of the given name only (middle names and initials dropped).
    Returns None if either part is missing."""
    last_t = _name_tokens(last)
    first_t = _name_tokens(first)
    if not last_t or not first_t:
        return None
    return f"{''.join(last_t)}|{first_t[0]}".lower()


def split_last_first(full):
    """Split NIH-style 'LAST, FIRST MIDDLE' (or 'First Middle Last') into (last, first)."""
    full = re.sub(r"\([^)]*\)", " ", full or "").strip()
    if "," in full:
        last, first = full.split(",", 1)
        return last.strip(), first.strip()
    parts = full.split()
    if len(parts) < 2:
        return full, ""
    return parts[-1], " ".join(parts[:-1])


# ---------------------------------------------------------------------------
# Institution normalization
# ---------------------------------------------------------------------------

_INST_ABBREV = {"UNIV": "UNIVERSITY", "INST": "INSTITUTE", "CTR": "CENTER", "CNTR": "CENTER",
                "HOSP": "HOSPITAL", "COLL": "COLLEGE", "NATL": "NATIONAL", "MED": "MEDICAL",
                "ST": "SAINT", "MT": "MOUNT", "INCORPORATED": "INC", "CORPORATION": "CORP",
                "COMPANY": "CO", "LIMITED": "LTD"}


def inst_name_norm(name):
    s = ascii_upper(name).replace("&", " AND ")
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    toks = [_INST_ABBREV.get(t, t) for t in s.split()]
    if toks and toks[0] == "THE":
        toks = toks[1:]
    return " ".join(toks)


def inst_key(name, city, state):
    """Matching key for an institution: (normalized name, normalized city, state code)."""
    city_n = re.sub(r"[^A-Z0-9 ]", " ", ascii_upper(city))
    return (inst_name_norm(name), " ".join(city_n.split()), ascii_upper(state).strip()[:2])


# ---------------------------------------------------------------------------
# Reading raw files (plain or inside .zip archives)
# ---------------------------------------------------------------------------

def iter_source_files(directory, suffixes):
    """Yield (name, opener) for every file with one of `suffixes`, looking inside zips too.
    `opener()` returns a binary file object; call it before advancing the generator."""
    directory = Path(directory)
    if not directory.exists():
        return
    for path in sorted(directory.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".zip":
            # the archive stays open while the caller consumes this generator lazily
            with zipfile.ZipFile(path) as zf:
                for info in sorted(zf.infolist(), key=lambda i: i.filename):
                    if not info.is_dir() and info.filename.lower().endswith(suffixes):
                        yield f"{path.name}!{info.filename}", (lambda zf=zf, info=info: zf.open(info))
        elif path.is_file() and path.name.lower().endswith(suffixes):
            yield path.name, (lambda path=path: open(path, "rb"))


def detect_encoding(opener):
    """ExPORTER files are UTF-8 in recent years and Windows-1252 in older ones.
    Stream the bytes through an incremental UTF-8 decoder; fall back to cp1252."""
    dec = codecs.getincrementaldecoder("utf-8")()
    with opener() as f:
        try:
            while True:
                chunk = f.read(1 << 20)
                if not chunk:
                    dec.decode(b"", final=True)
                    return "utf-8-sig"
                dec.decode(chunk)
        except UnicodeDecodeError:
            return "cp1252"


def iter_csv_dicts(opener):
    """Yield rows as dicts with upper-cased, stripped header names."""
    enc = detect_encoding(opener)
    with opener() as raw:
        text = io.TextIOWrapper(raw, encoding=enc, errors="replace", newline="")
        reader = csv.reader(text)
        header = next(reader, None)
        if header is None:
            return
        header = [h.strip().lstrip("\ufeff").upper() for h in header]
        for row in reader:
            if len(row) < len(header):
                row = row + [""] * (len(header) - len(row))
            yield dict(zip(header, row))


# ---------------------------------------------------------------------------
# MySQL LOAD DATA output (default format: tab-separated, backslash escapes, \N = NULL)
# ---------------------------------------------------------------------------

def _tsv_field(v):
    if v is None:
        return "\\N"
    if isinstance(v, date):
        return v.isoformat()
    s = str(v)
    return (s.replace("\\", "\\\\").replace("\t", "\\t").replace("\n", "\\n")
             .replace("\r", "\\r").replace("\0", "\\0"))


class TsvWriter:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = open(self.path, "w", encoding="utf-8", newline="\n")
        self.count = 0

    def write(self, *fields):
        self._f.write("\t".join(_tsv_field(v) for v in fields) + "\n")
        self.count += 1

    def close(self):
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


# ---------------------------------------------------------------------------
# Step timing / logging
# ---------------------------------------------------------------------------

class StepLog:
    """Collects per-step rows_in / rows_out / duration_ms for ingest_step."""

    def __init__(self):
        self.steps = []
        self.stats = {}

    @contextmanager
    def step(self, name):
        rec = {"step": name, "rows_in": None, "rows_out": None}
        t0 = time.perf_counter()
        log(f"[{name}] start")
        yield rec
        rec["duration_ms"] = int((time.perf_counter() - t0) * 1000)
        self.steps.append(rec)
        log(f"[{name}] rows_in={rec['rows_in']} rows_out={rec['rows_out']} {rec['duration_ms']} ms")


def log(msg):
    print(msg, file=sys.stderr, flush=True)
