"""Fetch a reproducible sample of recent first-instance road-accident judgments for gold labelling.

The first gold batch was all 2011-2013 appeal judgments (SAOS returns the oldest first).
This samples regional (Sąd Okręgowy) and district (Sąd Rejonowy) court judgments per year,
stores them in the local DB, classifies them and prints candidates in a fixed order.

    .venv/Scripts/python scripts/sample_gold_candidates.py --years 2018-2025 --per-year 10
"""
from __future__ import annotations

import argparse
import logging

from orzeczenia import db
from orzeczenia.classify import METHOD, classify
from orzeczenia.saos import SEARCH_FILTERS, SaosClient

QUERY = "wypadek komunikacyjny zadośćuczynienie"
log = logging.getLogger("sample_gold")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", default="2018-2025")
    parser.add_argument("--per-year", type=int, default=10, help="search hits per court level and year")
    parser.add_argument("--db", default=db.DEFAULT_DB)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    first, last = map(int, args.years.split("-"))
    client = SaosClient(delay=0.5)
    with db.connect(args.db) as conn:
        for level in ("REGIONAL", "DISTRICT"):
            for year in range(first, last + 1):
                filters = {**SEARCH_FILTERS, "ccCourtType": level,
                           "judgmentDateFrom": f"{year}-01-01", "judgmentDateTo": f"{year}-12-31"}
                label = f"gold-sample {level} {year}: {QUERY}"
                hits = []
                for hit in client.search(QUERY, page_size=args.per_year, **filters):
                    hits.append(hit)
                    if len(hits) >= args.per_year:
                        break
                for hit in hits:
                    db.record_hit(conn, hit, label)
                conn.commit()
                for hit in hits:
                    if conn.execute("SELECT 1 FROM judgments WHERE id = ?", (hit["id"],)).fetchone():
                        continue
                    data = client.judgment(hit["id"])
                    if data:
                        db.upsert_judgment(conn, db.judgment_row(data))
                conn.commit()
                log.info("%s %d: %d hits", level, year, len(hits))

        rows = conn.execute(
            "SELECT j.id, j.case_number, j.court_name, j.judgment_date, j.operative_part, j.reasoning, j.division_name"
            " FROM judgments j JOIN search_hits h ON h.judgment_id = j.id"
            " WHERE h.query LIKE 'gold-sample %' GROUP BY j.id"
        ).fetchall()
        relevant = []
        for row in rows:
            result = classify(row["operative_part"], row["reasoning"], row["division_name"])
            db.save_classification(conn, row["id"], result, METHOD)
            if result.is_road_accident and result.is_personal_injury and result.is_civil and row["reasoning"]:
                relevant.append(row)
        conn.commit()

    # Same pseudo-random order as `orzeczenia extract`, so the sample is reproducible.
    relevant.sort(key=lambda r: (r["id"] * 2654435761) % 4294967296)
    print(f"{len(relevant)} relevant of {len(rows)} sampled")
    for r in relevant:
        print(r["id"], r["judgment_date"], r["case_number"], r["court_name"], len(r["reasoning"]), sep="\t")


if __name__ == "__main__":
    main()
