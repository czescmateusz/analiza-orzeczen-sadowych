"""Round trip of the proofreading spreadsheet: export, fill in "nowy_tekst", apply."""
import csv
import importlib.util
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("teksty", Path(__file__).resolve().parents[1] / "scripts" / "teksty.py")


def _module(tmp_path, monkeypatch):
    mod = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(mod)
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "index.html").write_text(
        '<html><body><h1>Czy oferta jest\n   uczciwa?</h1><input placeholder="np. złamana noga"><nav>Metodologia</nav>'
        '<script>var x = "nie zbierać";</script></body></html>', encoding="utf-8")
    (tmp_path / "app" / "app.js").write_text(
        'const a = "Oferta wygląda na zaniżoną";\nconst b = `W ${p}% spraw sąd ustalił więcej`;\nconst c = "data/cases.json";\n', encoding="utf-8")
    monkeypatch.setattr(mod, "ROOT", tmp_path)
    monkeypatch.setattr(mod, "CSV", tmp_path / "teksty" / "k.csv")
    monkeypatch.setattr(mod, "HTML_FILES", ["app/index.html"])
    monkeypatch.setattr(mod, "JS_FILES", ["app/app.js"])
    monkeypatch.setattr(mod, "PY_FILES", [])
    return mod


def test_export_collects_user_texts_only(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    texts = [r["tekst"] for r in mod.collect()]
    assert texts == ["Czy oferta jest uczciwa?", "np. złamana noga", "Metodologia",
                     "Oferta wygląda na zaniżoną", "W ${p}% spraw sąd ustalił więcej"]


def test_apply_changes_only_filled_rows_and_protects_placeholders(tmp_path, monkeypatch):
    mod = _module(tmp_path, monkeypatch)
    mod.export()
    rows = list(csv.DictReader(mod.CSV.open(encoding="utf-8-sig"), delimiter=";"))
    fix = {"Czy oferta jest uczciwa?": "Czy oferta ubezpieczyciela jest uczciwa?",
           "Oferta wygląda na zaniżoną": "Oferta jest prawdopodobnie zaniżona",
           "W ${p}% spraw sąd ustalił więcej": "W ${x}% spraw więcej"}           # broken placeholder: skipped
    for r in rows:
        r["nowy_tekst"] = fix.get(r["tekst"], "")
    with mod.CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter=";")
        w.writeheader(); w.writerows(rows)
    mod.apply()
    html = (tmp_path / "app" / "index.html").read_text(encoding="utf-8")
    js = (tmp_path / "app" / "app.js").read_text(encoding="utf-8")
    assert "<h1>Czy oferta ubezpieczyciela jest uczciwa?</h1>" in html
    assert '"Oferta jest prawdopodobnie zaniżona"' in js and "W ${p}% spraw sąd ustalił więcej" in js
