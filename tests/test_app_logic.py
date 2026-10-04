"""Tests for the app's JavaScript (app/logic.js, app/app.js), run in QuickJS (no Node needed)."""
import json
import re
from pathlib import Path

import pytest

quickjs = pytest.importorskip("quickjs")
APP = Path(__file__).resolve().parents[1] / "app"


def _logic():
    """A QuickJS context with logic.js loaded (ES module syntax stripped)."""
    src = (APP / "logic.js").read_text(encoding="utf-8")
    ctx = quickjs.Context()
    ctx.eval(re.sub(r"^export ", "", src, flags=re.M))
    return ctx


def call(ctx, expr, **values):
    for name, value in values.items():
        ctx.eval(f"var {name} = {json.dumps(value, ensure_ascii=False)};")
    return json.loads(ctx.eval(f"JSON.stringify({expr})"))


def case(j, r="p", c=("noga",), u=None, f=(), t=50000, pd=None, y=2020, rel=None, how="a", age=None):
    return {"j": j, "r": r, "rel": rel, "c": list(c), "u": u, "age": age, "pc": None, "f": list(f),
            "t": t, "how": how, "pd": pd, "aw": t, "y": y, "i": 1, "q": 0}


def test_fold_and_search():
    ctx = _logic()
    assert call(ctx, 'fold("Złamana Noga, Łokieć")') == "zlamana noga, lokiec"
    cats = [{"id": "noga", "label": "Złamanie nogi", "synonyms": ["noga", "nogi", "kostk"]},
            {"id": "szyja", "label": "Kręgosłup szyjny", "synonyms": ["szyj", "kark", "whiplash"]}]
    assert call(ctx, 'searchCategories("złamana noga", cats)', cats=cats) == ["noga"]
    assert call(ctx, 'searchCategories("ból karku po wypadku", cats)', cats=cats) == ["szyja"]
    assert call(ctx, 'searchCategories("", cats)', cats=cats) == []


def test_find_similar_prefers_matching_injuries_and_damage():
    ctx = _logic()
    cases = [case(i, c=("noga",), u=10 + i % 3) for i in range(20)] + [case(100 + i, c=("glowa",), u=40) for i in range(20)]
    q = {"role": "p", "categories": ["noga"], "uszczerbek": 10, "age": None, "relation": None, "factors": [], "since": 2015}
    found = call(ctx, "findSimilar(cases, q)", cases=cases, q=q)
    assert {c["c"][0] for c in found["cases"]} == {"noga"} and not found["notes"]


def test_find_similar_widens_when_too_few_and_respects_years():
    ctx = _logic()
    cases = [case(i, r="b", rel="rodzic") for i in range(3)] + [case(10 + i, r="b", rel="malzonek") for i in range(20)]
    cases.append(case(99, r="b", rel="rodzic", y=2010))
    q = {"role": "b", "categories": [], "uszczerbek": None, "age": None, "relation": "rodzic", "factors": [], "since": 2015}
    found = call(ctx, "findSimilar(cases, q)", cases=cases, q=q)
    assert len(found["cases"]) == 23 and found["notes"]          # widened to all relatives
    assert 99 not in {c["j"] for c in found["cases"]}            # 2010 judgment excluded


def test_summarize_and_verdict():
    ctx = _logic()
    similar = [case(i, t=t, pd=t // 4) for i, t in enumerate([10000, 20000, 30000, 40000, 50000])]
    stats = call(ctx, "summarize(similar, 15000)", similar=similar)
    assert stats["median"] == 30000 and stats["p25"] == 20000 and stats["offerShareBelow"] == 0.8
    assert stats["ratioMedian"] == pytest.approx(4.0, rel=0.01)
    assert call(ctx, "verdict(stats, 15000)", stats=stats) == {"level": "low", "pct": 80}
    assert call(ctx, "verdict(stats, 35000)", stats=stats)["level"] == "typical"
    assert call(ctx, 'formatPLN(1234567)') == "1 234 567 zł"


def test_app_js_compiles():
    src = (APP / "app.js").read_text(encoding="utf-8")
    body = re.sub(r"^import .*$", "", src, flags=re.M)
    ctx = quickjs.Context()
    ctx.set("src", body)
    ctx.eval("new Function(src)")  # parses without running (no DOM here)


@pytest.mark.skipif(not (APP / "data" / "cases.json").exists(), reason="run `orzeczenia export` first")
def test_real_data_query_is_plausible():
    ctx = _logic()
    cases = json.loads((APP / "data" / "cases.json").read_text(encoding="utf-8"))
    ctx.set("raw", json.dumps(cases))
    ctx.eval("var cases = JSON.parse(raw);")
    q = {"role": "p", "categories": ["noga"], "uszczerbek": 10, "age": 35, "relation": None,
         "factors": ["dlugie_leczenie"], "since": 2015}
    found = call(ctx, "findSimilar(cases, q)", q=q)
    stats = call(ctx, "summarize(findSimilar(cases, q).cases, 15000)", q=q)
    assert len(found["cases"]) >= 30
    assert all("noga" in c["c"] for c in found["cases"])
    assert 10_000 < stats["median"] < 200_000


def test_browser_search_helpers():
    ctx = _logic()
    assert call(ctx, 'queryStems("Pomocy osób trzecich, rażąco")') == ["pomocy", "osob", "trzeci", "razaco"]
    assert call(ctx, "decodePostings([3, 2, 5])") == [3, 5, 10]
    shards = {"po": {"pomocy": [1, 2, 4], "pomoc": [9]}, "os": {"osob": [3, 4]}}  # rows {1,3,7}, {9}, {3,7}
    assert sorted(call(ctx, '[...matchRows(["pomocy", "osob"], shards)]', shards=shards)) == [3, 7]
    assert sorted(call(ctx, '[...matchRows(["pomo"], shards)]', shards=shards)) == [1, 3, 7, 9]   # short word = prefix
    assert call(ctx, '[...matchRows(["brak"], shards)]', shards=shards) == []


def test_mark_amounts_highlights_the_parsed_value():
    ctx = _logic()
    parts = call(ctx, 'markAmounts("wypłacił 5.000 zł, a sąd uznał 20 000 zł za odpowiednie", 20000)')
    assert [p["text"] for p in parts if p["mark"]] == ["20 000"]
    assert "".join(p["text"] for p in parts) == "wypłacił 5.000 zł, a sąd uznał 20 000 zł za odpowiednie"
    assert call(ctx, 'markAmounts("kwota 1.500,50 zł", 1500.5)')[1] == {"text": "1.500,50", "mark": True}


def test_in_prices_converts_totals_and_payments():
    ctx = _logic()
    cases = [case(1, t=10000, pd=2000, y=2015), case(2, t=10000, y=2026)]
    out = call(ctx, 'inPrices(cases, {"2015": 1.5, "2026": 1})', cases=cases)
    assert (out[0]["t"], out[0]["pd"], out[0]["tn"]) == (15000, 3000, 10000)
    assert (out[1]["t"], out[1]["pd"]) == (10000, None)


def test_success_rate_filters_role_year_and_injuries():
    ctx = _logic()
    rows = [["p", ["noga"], 2020, "f"]] * 10 + [["p", ["noga"], 2020, "l"]] * 10 + [["p", ["glowa"], 2020, "p"]] * 20 \
        + [["b", [], 2020, "p"]] * 40 + [["p", ["noga"], 2010, "l"]] * 50
    q = {"role": "p", "categories": ["noga"], "since": 2015}
    r = call(ctx, "successRate(rows, q, 20)", rows=rows, q=q)
    assert (r["n"], r["full"], r["loss"], r["widened"]) == (20, 0.5, 0.5, False)
    q2 = {"role": "p", "categories": ["oko"], "since": 2015}      # too few with this category: all injured
    r2 = call(ctx, "successRate(rows, q2, 20)", rows=rows, q2=q2)
    assert (r2["n"], r2["widened"]) == (40, True)
    assert call(ctx, 'successRate(rows, {"role": "b", "categories": [], "since": 2021}, 20)', rows=rows) is None
