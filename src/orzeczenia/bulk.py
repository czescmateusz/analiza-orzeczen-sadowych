"""Bulk download of every SAOS judgment with civil (non-criminal) claims.

Uses the SAOS dump API (100 full judgments per request), walking judgment-date windows
month by month so the download can stop and resume. Records are stored zlib-compressed
in a separate database (data/saos_civil.db); the full corpus is several GB uncompressed.

What is kept:
- common courts: every division except criminal, penitentiary and misdemeanour ones
  (civil, commercial, labour/social insurance, family), tagged with a category;
- Supreme Court: civil and labour chambers, recognised by the case number
  (e.g. "II CSK 12/20", "I PK 3/19"); criminal ("IV KK") is skipped;
- the Constitutional Tribunal and the National Appeal Chamber (KIO) are skipped.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import zlib
from datetime import date, datetime, timezone
from pathlib import Path

from .saos import SaosClient

DEFAULT_BULK_DB = Path("data") / "saos_civil.db"
log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS divisions (
    id          INTEGER PRIMARY KEY,   -- SAOS ccDivision id
    name        TEXT,
    type        TEXT,                  -- e.g. 'Cywilny', 'Karny', 'Pracy i Ubezpieczeń Społecznych'
    court_name  TEXT,
    court_level TEXT                   -- APPEAL / REGIONAL / DISTRICT
);

CREATE TABLE IF NOT EXISTS raw_judgments (
    id            INTEGER PRIMARY KEY, -- SAOS id
    court_type    TEXT NOT NULL,
    case_number   TEXT,
    judgment_type TEXT,                -- SENTENCE, DECISION, REASONS, RESOLUTION, ...
    judgment_date TEXT,
    division_id   INTEGER,
    category      TEXT NOT NULL,       -- civil / commercial / labour / family / other
    raw_zlib      BLOB NOT NULL,       -- zlib-compressed SAOS JSON record
    fetched_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS raw_judgments_category ON raw_judgments (category, judgment_type);

CREATE TABLE IF NOT EXISTS dump_progress (
    window_start TEXT PRIMARY KEY,
    window_end   TEXT NOT NULL,
    seen         INTEGER NOT NULL,
    kept         INTEGER NOT NULL,
    done_at      TEXT NOT NULL
);
"""

_CRIMINAL_DIVISION = re.compile(r"karn|penitencj|wykrocze", re.I)
# Supreme Court case numbers: "II CSK 123/20" (civil), "I PK 5/19", "III UK 7/18" (labour).
_SN_CIVIL = re.compile(r"\b[IVX]+\s+C[A-Z]*\b")
_SN_LABOUR = re.compile(r"\b[IVX]+\s+(?:P|U)[A-Z]*\b")


def connect(path: Path | str = DEFAULT_BULK_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=120)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def category_for_division_type(division_type: str | None) -> str | None:
    """Map a common-court division type to a category; None means criminal (skip)."""
    t = (division_type or "").lower()
    if _CRIMINAL_DIVISION.search(t):
        return None
    if "gospodar" in t:
        return "commercial"
    if "pracy" in t or "ubezpiecze" in t:
        return "labour"
    if "rodzin" in t:
        return "family"
    if "cywil" in t:
        return "civil"
    return "other"


def category_for_supreme(case_number: str) -> str | None:
    if _SN_CIVIL.search(case_number):
        return "civil"
    if _SN_LABOUR.search(case_number):
        return "labour"
    return None


def month_windows(start: date, end: date) -> list[tuple[str, str]]:
    """Month-long [start, end] date windows, plus catch-alls for OCR-garbled years."""
    windows = [("0001-01-01", f"{start.year - 1}-12-31")]
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        last = (date(ny, nm, 1) - date.resolution).isoformat()
        windows.append((f"{y:04d}-{m:02d}-01", last))
        y, m = ny, nm
    windows.append((f"{end.year + 1}-01-01", "9999-12-31"))
    return windows


class DivisionCache:
    def __init__(self, conn: sqlite3.Connection, client: SaosClient):
        self.conn, self.client = conn, client
        self.types = {row[0]: row[1] for row in conn.execute("SELECT id, type FROM divisions")}

    def type_of(self, division_id: int) -> str | None:
        if division_id not in self.types:
            data = self.client._get(f"/ccDivisions/{division_id}")
            info = (data or {}).get("data", {})
            court = info.get("court") or {}
            self.conn.execute(
                "INSERT OR REPLACE INTO divisions VALUES (?, ?, ?, ?, ?)",
                (division_id, info.get("name"), info.get("type"), court.get("name"), court.get("type")),
            )
            self.types[division_id] = info.get("type")
        return self.types[division_id]


def classify_record(item: dict, divisions: DivisionCache) -> str | None:
    """Return the category to store the record under, or None to skip it."""
    court_type = item.get("courtType")
    case_number = "; ".join(c.get("caseNumber", "") for c in item.get("courtCases", []))
    if court_type == "COMMON":
        division_id = (item.get("division") or {}).get("id")
        return category_for_division_type(divisions.type_of(division_id)) if division_id else "other"
    if court_type == "SUPREME":
        return category_for_supreme(case_number)
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def download(db_path: Path | str = DEFAULT_BULK_DB, delay: float = 0.5, start: date = date(1990, 1, 1)) -> None:
    client = SaosClient(delay=delay, timeout=180)
    conn = connect(db_path)
    divisions = DivisionCache(conn, client)
    done = {row[0] for row in conn.execute("SELECT window_start FROM dump_progress")}
    for window_start, window_end in month_windows(start, date.today()):
        if window_start in done:
            continue
        seen = kept = page = 0
        while True:
            data = client._get(
                "/dump/judgments",
                {"pageSize": 100, "pageNumber": page, "withGenerated": "true",
                 "judgmentStartDate": window_start, "judgmentEndDate": window_end},
            )
            items = (data or {}).get("items", [])
            for item in items:
                seen += 1
                category = classify_record(item, divisions)
                if category is None:
                    continue
                kept += 1
                conn.execute(
                    "INSERT OR REPLACE INTO raw_judgments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        item["id"], item.get("courtType"),
                        "; ".join(c.get("caseNumber", "") for c in item.get("courtCases", [])),
                        item.get("judgmentType"), item.get("judgmentDate"),
                        (item.get("division") or {}).get("id"), category,
                        zlib.compress(json.dumps(item, ensure_ascii=False).encode("utf-8"), 6), _now(),
                    ),
                )
            conn.commit()
            if not items or not any(link["rel"] == "next" for link in data.get("links", [])):
                break
            page += 1
        conn.execute("INSERT OR REPLACE INTO dump_progress VALUES (?, ?, ?, ?, ?)",
                     (window_start, window_end, seen, kept, _now()))
        conn.commit()
        log.info("%s..%s: %d judgments, %d kept", window_start, window_end, seen, kept)


def load_record(conn: sqlite3.Connection, judgment_id: int) -> dict | None:
    row = conn.execute("SELECT raw_zlib FROM raw_judgments WHERE id = ?", (judgment_id,)).fetchone()
    return json.loads(zlib.decompress(row[0])) if row else None


# Cheap pre-filter on the raw HTML; the real decision is classify.py / parse.is_road_accident.
_ROAD = re.compile(r"wypad\w* (?:komunikacyjn|drogow|samochodow)|kolizj|potrąc|zderzeni|ruchu drogow", re.I)
_COMPENSATION = re.compile(r"zadośćuczyn|odszkodowa|\brent[ayę]\b", re.I)


def road_candidates(conn: sqlite3.Connection, categories=("civil", "commercial", "other"),
                    judgment_types=("SENTENCE", "REASONS"), skip_ids: set[int] = frozenset()):
    """Yield SAOS records that mention a road accident and compensation, with division names filled in."""
    divisions = {r[0]: r[1:] for r in conn.execute("SELECT id, name, court_name FROM divisions")}
    marks = ", ".join("?" * len(categories)), ", ".join("?" * len(judgment_types))
    rows = conn.execute(
        f"SELECT id, raw_zlib FROM raw_judgments WHERE category IN ({marks[0]}) AND judgment_type IN ({marks[1]})",
        (*categories, *judgment_types),
    )
    for judgment_id, blob in rows:
        if judgment_id in skip_ids:
            continue
        item = json.loads(zlib.decompress(blob))
        text = item.get("textContent", "")
        if not (_ROAD.search(text) and _COMPENSATION.search(text)):
            continue
        division = item.get("division") or {}
        name, court_name = divisions.get(division.get("id"), (None, None))
        division.setdefault("name", name)
        division.setdefault("court", {"name": court_name})
        item["division"] = division
        yield item
