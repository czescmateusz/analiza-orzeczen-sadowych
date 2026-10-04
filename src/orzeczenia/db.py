"""Local SQLite store. The schema sticks to portable types so it can move to Postgres later."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .text import html_to_text, split_sections

DEFAULT_DB = Path("data") / "orzeczenia.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS search_hits (
    judgment_id   INTEGER NOT NULL,
    query         TEXT    NOT NULL,
    judgment_date TEXT,
    PRIMARY KEY (judgment_id, query)
);

CREATE TABLE IF NOT EXISTS judgments (
    id                     INTEGER PRIMARY KEY,  -- SAOS id
    case_number            TEXT,
    court_type             TEXT,
    court_id               INTEGER,
    court_name             TEXT,
    division_name          TEXT,
    judgment_type          TEXT,
    judgment_date          TEXT,                 -- as published; SAOS has some OCR-garbled years
    keywords               TEXT,                 -- JSON array
    legal_bases            TEXT,                 -- JSON array
    referenced_regulations TEXT,                 -- JSON array
    source_url             TEXT,
    operative_part         TEXT,                 -- sentencja
    reasoning              TEXT,                 -- uzasadnienie
    raw_json               TEXT NOT NULL,        -- full SAOS record, kept so parsing can be redone
    fetched_at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classification (
    judgment_id        INTEGER PRIMARY KEY REFERENCES judgments(id),
    is_road_accident   INTEGER NOT NULL,
    is_personal_injury INTEGER NOT NULL,
    is_civil           INTEGER NOT NULL,
    signals            TEXT NOT NULL,            -- JSON {signal: match count}
    method             TEXT NOT NULL,
    classified_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS extractions (
    judgment_id    INTEGER NOT NULL REFERENCES judgments(id),
    model          TEXT    NOT NULL,
    prompt_version TEXT    NOT NULL,
    input_chars    INTEGER NOT NULL,
    raw_response   TEXT,
    result         TEXT,                 -- validated JSON (extract.Extraction); NULL on failure
    error          TEXT,
    created_at     TEXT    NOT NULL,
    PRIMARY KEY (judgment_id, model, prompt_version)
);

-- Quotable passages of the court's reasoning (reasons.py): valuation factors and
-- findings that the insurer's payment was too low.
CREATE TABLE IF NOT EXISTS passages (
    judgment_id  INTEGER NOT NULL REFERENCES judgments(id),
    version      TEXT    NOT NULL,      -- reasons.REASONS_VERSION
    sentence     INTEGER NOT NULL,      -- sentence index in the reasoning
    section      TEXT    NOT NULL,      -- facts / assessment
    insurer_view TEXT,                  -- too_low or NULL
    factors      TEXT    NOT NULL,      -- JSON array of factor names
    text         TEXT    NOT NULL,      -- verbatim sentence
    PRIMARY KEY (judgment_id, version, sentence)
);
CREATE INDEX IF NOT EXISTS passages_view ON passages (version, insurer_view);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Generous busy timeout: collect and fetch may run concurrently.
    conn = sqlite3.connect(path, timeout=120)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def record_hit(conn: sqlite3.Connection, hit: dict, query: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO search_hits (judgment_id, query, judgment_date) VALUES (?, ?, ?)",
        (hit["id"], query, hit.get("judgmentDate")),
    )


def missing_judgment_ids(conn: sqlite3.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT DISTINCT judgment_id FROM search_hits"
        " WHERE judgment_id NOT IN (SELECT id FROM judgments) ORDER BY judgment_id"
    )
    return [row[0] for row in rows]


def judgment_row(data: dict) -> dict:
    """Flatten a SAOS judgment record into a `judgments` row."""
    division = data.get("division") or {}
    court = division.get("court") or {}
    operative, reasoning = split_sections(html_to_text(data.get("textContent", "")))
    return {
        "id": data["id"],
        "case_number": "; ".join(c["caseNumber"] for c in data.get("courtCases", [])),
        "court_type": data.get("courtType"),
        "court_id": court.get("id"),
        "court_name": court.get("name"),
        "division_name": division.get("name"),
        "judgment_type": data.get("judgmentType"),
        "judgment_date": data.get("judgmentDate"),
        "keywords": json.dumps(data.get("keywords", []), ensure_ascii=False),
        "legal_bases": json.dumps(data.get("legalBases", []), ensure_ascii=False),
        "referenced_regulations": json.dumps(
            [r["text"] for r in data.get("referencedRegulations", [])], ensure_ascii=False
        ),
        "source_url": (data.get("source") or {}).get("judgmentUrl"),
        "operative_part": operative,
        "reasoning": reasoning,
        "raw_json": json.dumps(data, ensure_ascii=False),
        "fetched_at": _now(),
    }


def upsert_judgment(conn: sqlite3.Connection, row: dict) -> None:
    columns = ", ".join(row)
    placeholders = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT OR REPLACE INTO judgments ({columns}) VALUES ({placeholders})", row)


def save_extraction(
    conn: sqlite3.Connection,
    judgment_id: int,
    model: str,
    prompt_version: str,
    input_chars: int,
    raw_response: str | None,
    result_json: str | None,
    error: str | None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO extractions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (judgment_id, model, prompt_version, input_chars, raw_response, result_json, error, _now()),
    )


def save_classification(conn: sqlite3.Connection, judgment_id: int, result, method: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO classification VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            judgment_id,
            int(result.is_road_accident),
            int(result.is_personal_injury),
            int(result.is_civil),
            json.dumps(result.signals, ensure_ascii=False),
            method,
            _now(),
        ),
    )
