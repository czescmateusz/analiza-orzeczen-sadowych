"""Side-by-side: gold label vs rule-parser output, with the operative clauses it read.

    .venv/Scripts/python scripts/debug_parse.py [saos_id ...]
"""
import sqlite3, sys
from orzeczenia.evaluate import load_gold
from orzeczenia.parse import parse, operative_clauses, plaintiffs

conn = sqlite3.connect("data/orzeczenia.db")
gold = load_gold()
ids = [int(a) for a in sys.argv[1:]] or sorted(gold)
fmt = lambda a: f"{a.type[:6]}{'/m' if a.is_monthly else ''} c={a.amount_claimed} ap={a.amount_appropriate} pd={a.amount_paid_earlier} aw={a.amount_awarded}"
for jid in ids:
    op, rs = conn.execute("select operative_part, reasoning from judgments where id=?", (jid,)).fetchone()
    g, r = gold[jid], parse(op, rs)
    p = r.extraction
    print(f"===== {jid} road gold={g.is_road_accident} parsed={p.is_road_accident} plaintiffs={plaintiffs(op)}")
    for c in g.claimants: print("  G", c.role, [fmt(a) for a in c.awards])
    for c in p.claimants: print("  P", c.role, [fmt(a) for a in c.awards])
    if len(sys.argv) > 1:
        for cl in operative_clauses(op): print("    |", cl[:200].replace("\n", " "))
