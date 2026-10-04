"""Print a labelling view of a judgment: full operative part + every reasoning sentence with an
amount, %, injury, age or key compensation term. Wider than snippets.select() on purpose
(it is for the human labeller, who checks the full text whenever this view is ambiguous).

    .venv/Scripts/python scripts/gold_view.py <saos_id>
"""
import re
import sqlite3
import sys

from orzeczenia.snippets import sentences

KEY = re.compile(
    r"\d[\d .]*(?:,\d\d)?[-,\s]*(?:zł|zl|złotych)|\d+\s*%|zadośćuczyn|odszkodow|rent[ayę]|uszczerb|przyczyni"
    r"|doznał|obraże|złaman|uraz|zmarł|zgin|śmier|ur\.|urodzi|\blat\b|lata\b|pasażer|piesz|potrąc|apelac|oddal",
    re.I,
)
conn = sqlite3.connect("data/orzeczenia.db")
for jid in sys.argv[1:]:
    case, court, date, op, rs = conn.execute(
        "SELECT case_number, court_name, judgment_date, operative_part, reasoning FROM judgments WHERE id = ?", (int(jid),)
    ).fetchone()
    print(f"##### {jid} | {case} | {court} | {date} | reasoning {len(rs)} chars\n{op}\n--- KEY SENTENCES:")
    for i, s in enumerate(sentences(rs)):
        if KEY.search(s):
            print(f"[{i}] {s[:700]}")
