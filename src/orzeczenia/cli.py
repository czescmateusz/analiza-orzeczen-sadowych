"""Command-line entry point: `orzeczenia collect|fetch|reparse|classify|stats`.

Every step is resumable: re-running only does the work that is still missing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from . import db
from .classify import METHOD, classify
from .saos import ROAD_ACCIDENT_QUERIES, SEARCH_FILTERS, SaosClient

log = logging.getLogger("orzeczenia")


def cmd_collect(args) -> None:
    client = SaosClient(delay=args.delay)
    with db.connect(args.db) as conn:
        for query in args.query or ROAD_ACCIDENT_QUERIES:
            stored = conn.execute("SELECT COUNT(*) FROM search_hits WHERE query = ?", (query,)).fetchone()[0]
            if stored and stored >= client.count(query, **SEARCH_FILTERS):
                log.info("query %r: already collected (%d hits), skipping", query, stored)
                continue
            n = 0
            # SAOS search takes ~10 s per page, so commit every page to keep progress.
            for hit in client.search(query, **SEARCH_FILTERS):
                db.record_hit(conn, hit, query)
                n += 1
                if n % 100 == 0:
                    conn.commit()
                    log.debug("query %r: %d hits so far", query, n)
            conn.commit()
            log.info("query %r: stored %d hits", query, n)
        total = conn.execute("SELECT COUNT(DISTINCT judgment_id) FROM search_hits").fetchone()[0]
    log.info("%d distinct candidate judgments", total)


def cmd_fetch(args) -> None:
    client = SaosClient(delay=args.delay)
    with db.connect(args.db) as conn:
        ids = db.missing_judgment_ids(conn)[: args.limit]
        log.info("fetching %d judgments", len(ids))
        for i, judgment_id in enumerate(ids, 1):
            data = client.judgment(judgment_id)
            if data is None:
                log.warning("judgment %d not found in SAOS", judgment_id)
                continue
            db.upsert_judgment(conn, db.judgment_row(data))
            if i % 50 == 0:
                conn.commit()
                log.info("%d/%d fetched", i, len(ids))
        conn.commit()


def cmd_reparse(args) -> None:
    """Re-derive text columns from stored raw JSON (after changing text.py)."""
    with db.connect(args.db) as conn:
        rows = conn.execute("SELECT raw_json FROM judgments").fetchall()
        for row in rows:
            db.upsert_judgment(conn, db.judgment_row(json.loads(row["raw_json"])))
        conn.commit()
    log.info("reparsed %d judgments", len(rows))


def cmd_classify(args) -> None:
    with db.connect(args.db) as conn:
        rows = conn.execute("SELECT id, operative_part, reasoning, division_name FROM judgments").fetchall()
        for row in rows:
            result = classify(row["operative_part"], row["reasoning"], row["division_name"])
            db.save_classification(conn, row["id"], result, METHOD)
        conn.commit()
    log.info("classified %d judgments", len(rows))


def cmd_extract(args) -> None:
    # Imported here so the download steps don't need the LLM dependencies.
    from . import snippets
    from .extract import PROMPT_VERSION, Extractor

    extractor = Extractor(model=args.model, provider=args.provider, delay=args.delay)
    with db.connect(args.db) as conn:
        if args.gold_only:
            from .evaluate import load_gold

            ids = sorted(load_gold())
            scope = f"j.id IN ({','.join(map(str, ids))})" if ids else "0"
        else:
            scope = "c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''"
        # Cases without an extraction for this model and prompt, in a fixed
        # pseudo-random order so --limit gives a spread of courts and years.
        rows = conn.execute(
            "SELECT j.id, j.operative_part, j.reasoning FROM judgments j"
            " JOIN classification c ON c.judgment_id = j.id"
            f" WHERE {scope}"
            "   AND NOT EXISTS (SELECT 1 FROM extractions e WHERE e.judgment_id = j.id"
            "                   AND e.model = ? AND e.prompt_version = ? AND e.error IS NULL)"
            " ORDER BY (j.id * 2654435761) % 4294967296 LIMIT ?",
            (args.model, PROMPT_VERSION, args.limit or -1),
        ).fetchall()
        log.info("extracting %d judgments with %s", len(rows), args.model)
        failures = 0
        for i, row in enumerate(rows, 1):
            text = snippets.select(row["operative_part"], row["reasoning"])
            raw, result, error = extractor.extract(text)
            if error:
                failures += 1
                log.warning("judgment %d: %s", row["id"], error[:200])
            db.save_extraction(
                conn, row["id"], args.model, PROMPT_VERSION, len(text),
                raw, result.model_dump_json() if result else None, error,
            )
            conn.commit()
            if i % 10 == 0:
                log.info("%d/%d extracted (%d failed)", i, len(rows), failures)
        log.info("done: %d extracted, %d failed", len(rows) - failures, failures)


def cmd_evaluate(args) -> None:
    from .evaluate import evaluate, load_gold
    from .extract import PROMPT_VERSION, Extraction

    gold = load_gold()
    prompt_version = args.prompt_version or PROMPT_VERSION
    with db.connect(args.db) as conn:
        rows = conn.execute(
            "SELECT judgment_id, result FROM extractions"
            " WHERE model = ? AND prompt_version = ? AND result IS NOT NULL",
            (args.model, prompt_version),
        ).fetchall()
    predictions = {r[0]: Extraction.model_validate_json(r[1]) for r in rows if r[0] in gold}
    missing = len(gold) - len(predictions)
    print(f"model {args.model}, prompt {prompt_version}: {len(gold)} gold files, {missing} without extraction")
    print(evaluate(gold, predictions).format())


def cmd_parse(args) -> None:
    """Deterministic rule-based extraction (no LLM), stored like an extractor's output."""
    from dataclasses import asdict

    from .parse import PARSER_NAME, PARSER_VERSION, parse

    with db.connect(args.db) as conn:
        if args.show:
            for judgment_id in args.show:
                row = conn.execute("SELECT operative_part, reasoning FROM judgments WHERE id = ?", (judgment_id,)).fetchone()
                if row is None:
                    print(f"judgment {judgment_id}: not in the database")
                    continue
                show_parse(judgment_id, parse(row["operative_part"], row["reasoning"]))
            return
        if args.gold_only:
            from .evaluate import load_gold

            ids = sorted(load_gold())
            rows = conn.execute(
                f"SELECT id, operative_part, reasoning FROM judgments WHERE id IN ({','.join(map(str, ids))})"
            ).fetchall()
        else:
            rows = conn.execute("SELECT id, operative_part, reasoning FROM judgments WHERE reasoning != ''").fetchall()
        for row in rows:
            result = parse(row["operative_part"], row["reasoning"])
            # The per-claim source passages go in the `raw` column, where LLM extractors keep their reply.
            evidence = json.dumps([asdict(e) for e in result.evidence], ensure_ascii=False)
            db.save_extraction(conn, row["id"], PARSER_NAME, PARSER_VERSION, len(row["reasoning"]), evidence,
                               result.extraction.model_dump_json(), None)
        conn.commit()
    log.info("parsed %d judgments with %s %s", len(rows), PARSER_NAME, PARSER_VERSION)


def show_parse(judgment_id: int, result) -> None:
    """Print each claim's numbers with the passages of the judgment they were read from."""
    x = result.extraction
    print(f"===== judgment {judgment_id}: road accident = {x.is_road_accident}")
    awards = [a for c in x.claimants for a in c.awards]
    for award, ev in zip(awards, result.evidence):
        print(f"\n--- {ev.claimant}: {ev.heading}{' (monthly)' if ev.is_monthly else ''}")
        print(f"    claimed={award.amount_claimed} appropriate={award.amount_appropriate}"
              f" paid earlier={award.amount_paid_earlier} awarded={award.amount_awarded}")
        for label, passages in (("operative", ev.operative), ("claimed", ev.claimed),
                                ("appropriate", ev.appropriate), ("paid", ev.paid)):
            for passage in passages:
                print(f"    [{label}] {' '.join(passage.split())[:400]}")


def cmd_dump(args) -> None:
    from .bulk import download

    download(args.bulk_db, delay=args.delay)


def cmd_import_bulk(args) -> None:
    """Copy road-accident compensation candidates from the bulk corpus into the main database."""
    from . import bulk

    source = bulk.connect(args.bulk_db)
    with db.connect(args.db) as conn:
        existing = {row[0] for row in conn.execute("SELECT id FROM judgments")}
        n = 0
        for item in bulk.road_candidates(source, skip_ids=existing):
            db.upsert_judgment(conn, db.judgment_row(item))
            n += 1
            if n % 500 == 0:
                conn.commit()
                log.info("%d imported", n)
        conn.commit()
    log.info("imported %d new candidate judgments (%d were already present)", n, len(existing))


def cmd_reasons(args) -> None:
    """Extract quotable passages (factors, 'insurer paid too little') from relevant judgments."""
    from .reasons import REASONS_VERSION, passages, save_passages

    with db.connect(args.db) as conn:
        # Judgments with no passages are re-read on the next run; that is cheap and harmless.
        rows = conn.execute(
            "SELECT j.id, j.reasoning FROM judgments j JOIN classification c ON c.judgment_id = j.id"
            " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''"
            "   AND NOT EXISTS (SELECT 1 FROM passages p WHERE p.judgment_id = j.id AND p.version = ?)"
            " LIMIT ?",
            (REASONS_VERSION, args.limit or -1),
        ).fetchall()
        log.info("reading %d judgments", len(rows))
        for i, row in enumerate(rows, 1):
            save_passages(conn, row["id"], passages(row["reasoning"]))
            if i % 500 == 0:
                conn.commit()
                log.info("%d/%d", i, len(rows))
        conn.commit()


def cmd_factors(args) -> None:
    from .reasons import factor_report

    with db.connect(args.db) as conn:
        print(factor_report(conn, args.parser_version))


def cmd_quotes(args) -> None:
    from .reasons import quotes

    with db.connect(args.db) as conn:
        for q in quotes(conn, args.factor, args.limit):
            print(f"\n{q['case_number']} — {q['court']}, {q['date']}  {q['url']}")
            print(f"  „{q['verdict']}”")
            for r in q["reasons"]:
                print(f"  [{', '.join(r['factors'])}] „{r['text']}”")


def cmd_export(args) -> None:
    from pathlib import Path

    from .export import export, export_browser

    with db.connect(args.db) as conn:
        print(export(conn, Path(args.out), args.parser_version))
        print(export_browser(conn, Path(args.out), args.parser_version))


def cmd_model(args) -> None:
    from pathlib import Path

    from .model import run

    with db.connect(args.db) as conn:
        print(run(conn, Path(args.out), args.parser_version))


def cmd_stats(args) -> None:
    with db.connect(args.db) as conn:
        q = lambda sql: conn.execute(sql).fetchall()  # noqa: E731
        print("candidates:", q("SELECT COUNT(DISTINCT judgment_id) FROM search_hits")[0][0])
        print("fetched:   ", q("SELECT COUNT(*) FROM judgments")[0][0])
        print("no reasoning:", q("SELECT COUNT(*) FROM judgments WHERE reasoning = ''")[0][0])
        print("\nclassification (road, injury, civil -> count):")
        for r in q(
            "SELECT is_road_accident, is_personal_injury, is_civil, COUNT(*) FROM classification"
            " GROUP BY 1, 2, 3 ORDER BY 4 DESC"
        ):
            print(f"  {r[0]} {r[1]} {r[2]} -> {r[3]}")
        print("\nrelevant (road + injury + civil) by year:")
        for r in q(
            "SELECT substr(j.judgment_date, 1, 4) AS year, COUNT(*) FROM judgments j"
            " JOIN classification c ON c.judgment_id = j.id"
            " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil"
            " GROUP BY year ORDER BY year"
        ):
            print(f"  {r[0]}: {r[1]}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="orzeczenia", description=__doc__)
    parser.add_argument("--db", default=db.DEFAULT_DB, help="SQLite path (default: %(default)s)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("collect", help="search SAOS and store candidate judgment ids")
    p.add_argument("--query", action="append", help="override the default queries (repeatable)")
    p.add_argument("--delay", type=float, default=0.3, help="seconds between API requests")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("fetch", help="download full text of collected judgments not yet stored")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--delay", type=float, default=0.3, help="seconds between API requests")
    p.set_defaults(func=cmd_fetch)

    sub.add_parser("reparse", help="rebuild text columns from stored raw JSON").set_defaults(func=cmd_reparse)
    sub.add_parser("classify", help="run rule-based road-accident filter").set_defaults(func=cmd_classify)
    p = sub.add_parser("extract", help="LLM extraction of claimants, injuries and awards")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--model", default="speakleash/Bielik-11B-v3.0-Instruct")
    p.add_argument("--provider", default="publicai", help="Hugging Face inference provider")
    p.add_argument("--delay", type=float, default=3.0, help="seconds between LLM requests")
    p.add_argument("--gold-only", action="store_true", help="only judgments with a gold label")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("parse", help="deterministic rule-based claim extraction (no LLM)")
    p.add_argument("--gold-only", action="store_true", help="only judgments with a gold label")
    p.add_argument("--show", type=int, nargs="+", metavar="ID",
                   help="print the claims and their source passages for these judgments instead of storing")
    p.set_defaults(func=cmd_parse)

    p = sub.add_parser("dump", help="bulk-download every SAOS judgment with civil claims (resumable)")
    p.add_argument("--bulk-db", default="data/saos_civil.db")
    p.add_argument("--delay", type=float, default=0.5, help="seconds between API requests")
    p.set_defaults(func=cmd_dump)

    p = sub.add_parser("import-bulk", help="copy road-accident candidates from the bulk corpus into --db")
    p.add_argument("--bulk-db", default="data/saos_civil.db")
    p.set_defaults(func=cmd_import_bulk)

    p = sub.add_parser("reasons", help="extract quotable passages: valuation factors and 'insurer paid too little'")
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=cmd_reasons)

    p = sub.add_parser("factors", help="compare factors where the court raised vs. didn't raise the award")
    p.add_argument("--parser-version", default="p2")
    p.set_defaults(func=cmd_factors)

    p = sub.add_parser("quotes", help="print citable court findings that the insurer paid too little")
    p.add_argument("--factor", default=None, help="e.g. psychika, trwale_skutki, wiez_ze_zmarlym")
    p.add_argument("--limit", type=int, default=10)
    p.set_defaults(func=cmd_quotes)

    p = sub.add_parser("export", help="write the app's static data (cases, citations, quotes, labels)")
    p.add_argument("--out", default="app/data")
    p.add_argument("--parser-version", default="p2")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("model", help="fit the statistical model and time trend (needs the 'analysis' extra)")
    p.add_argument("--out", default="app/data/model.json")
    p.add_argument("--parser-version", default="p2")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("evaluate", help="score stored extractions against evaluation/gold")
    p.add_argument("--model", default="speakleash/Bielik-11B-v3.0-Instruct")
    p.add_argument("--prompt-version", default=None, help="default: current PROMPT_VERSION")
    p.set_defaults(func=cmd_evaluate)

    sub.add_parser("stats", help="print corpus statistics").set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )
    args.func(args)


if __name__ == "__main__":
    main()
