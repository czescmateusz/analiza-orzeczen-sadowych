"""Collect the app's user-facing Polish texts into a spreadsheet for proofreading, and apply corrections.

    .venv/Scripts/python scripts/teksty.py export   # writes teksty/teksty_do_korekty.csv
    .venv/Scripts/python scripts/teksty.py apply    # applies the "nowy_tekst" column to the source files

The CSV (UTF-8 with BOM, ";"-separated, so Excel opens it with Polish characters) has one row per
text: id, plik, wiersz, tekst, nowy_tekst, uwagi. Only rows with "nowy_tekst" filled in are applied.
Texts come from
- the app's HTML pages (text between tags, and placeholder / title / aria-label / description attributes),
- string literals in the app's JavaScript (messages, headings of results),
- Polish labels in the Python code that end up in the app (injury categories, factors, model labels).
After `apply`, re-run `orzeczenia export` and `orzeczenia model` if a Python label changed.
"""
from __future__ import annotations

import csv
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / "teksty" / "teksty_do_korekty.csv"
HTML_FILES = ["app/index.html", "app/przegladarka.html", "app/metodologia.html"]
JS_FILES = ["app/app.js", "app/przegladarka.js", "app/metodologia.js", "app/logic.js"]
PY_FILES = ["src/orzeczenia/categories.py", "src/orzeczenia/export.py", "src/orzeczenia/model.py"]
ATTRS = ("placeholder", "title", "aria-label", "content")
POLISH = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]|\b(?:i|w|z|na|do|nie|od|lub|jest|się|oraz|dla)\b")
CODE_LIKE = re.compile(r"\\[a-z]|\(\?|\[\^|\|.*\||^[\w.-]+$|https?://|^#|\.json|\.html|\.js\b")


def looks_like_text(s: str, html: bool = False) -> bool:
    """In HTML every visible text is for the user; in code, only strings that read as Polish prose."""
    s = s.strip()
    if len(s) < 2 or not re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{2}", s):
        return False
    return html or (bool(POLISH.search(s)) and not CODE_LIKE.search(s))


class _Collector(HTMLParser):
    def __init__(self, source: str):
        super().__init__(convert_charrefs=False)
        self.source_lines = source.splitlines()
        self.items: list[tuple[int, str, str]] = []   # (line, text, note)
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        for k, v in attrs:
            if k in ATTRS and v and looks_like_text(v, html=True):
                self.items.append((self.getpos()[0], v, f"atrybut {k}"))

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1

    def handle_data(self, data):
        if self.skip:
            return
        text = " ".join(data.split())
        if looks_like_text(text, html=True):
            self.items.append((self.getpos()[0], text, ""))


_JS_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"|`((?:[^`\\]|\\.)*)`|\'((?:[^\'\\\n]|\\.)*)\'')
_PY_STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def collect() -> list[dict]:
    rows = []
    for rel in HTML_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        c = _Collector(src)
        c.feed(src)
        for line, text, note in c.items:
            rows.append({"plik": rel, "wiersz": line, "tekst": text, "uwagi": note})
    for rel in JS_FILES + PY_FILES:
        is_js = rel.endswith(".js")
        for n, line in enumerate((ROOT / rel).read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith(("//", "#", "import ", "from ")):
                continue
            for m in (_JS_STRING if is_js else _PY_STRING).finditer(line):
                text = next(g for g in m.groups() if g is not None)
                if looks_like_text(text):
                    note = "nie zmieniaj fragmentów ${…}" if "${" in text else ""
                    rows.append({"plik": rel, "wiersz": n, "tekst": text, "uwagi": note})
    seen, out = set(), []
    for r in rows:
        key = (r["plik"], r["tekst"])
        if key not in seen:
            seen.add(key)
            out.append(r)
    for i, r in enumerate(out, 1):
        r["id"] = f"T{i:04d}"
    return out


def export() -> None:
    rows = collect()
    CSV.parent.mkdir(exist_ok=True)
    with CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "plik", "wiersz", "tekst", "nowy_tekst", "uwagi"], delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({**r, "nowy_tekst": ""})
    print(f"{len(rows)} tekstów -> {CSV.relative_to(ROOT)}")


def apply() -> None:
    with CSV.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f, delimiter=";") if r["nowy_tekst"].strip()]
    changed, problems = 0, []
    for r in rows:
        path = ROOT / r["plik"]
        src = path.read_text(encoding="utf-8")
        old, new = r["tekst"], r["nowy_tekst"].strip()
        if "${" in old and re.findall(r"\$\{[^}]*\}", old) != re.findall(r"\$\{[^}]*\}", new):
            problems.append(f'{r["id"]}: zmieniono fragmenty ${{…}} – pominięto')
            continue
        if r["plik"].endswith(".html"):
            # HTML text was whitespace-normalised: match it with any whitespace in between words.
            pattern = re.compile(r"\s+".join(re.escape(w) for w in old.split()))
            src2, n = pattern.subn(new.replace("\\", "\\\\"), src, count=1)
        else:
            n = src.count(old)
            src2 = src.replace(old, new, 1)
        if not n:
            problems.append(f'{r["id"]}: nie znaleziono tekstu w {r["plik"]} (zmieniony w międzyczasie?)')
            continue
        path.write_text(src2, encoding="utf-8")
        changed += 1
    print(f"zastosowano {changed} z {len(rows)} poprawek")
    for p in problems:
        print("  !", p)


if __name__ == "__main__":
    {"export": export, "apply": apply}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: print(__doc__))()
