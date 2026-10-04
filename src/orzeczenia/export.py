"""Export the app's static data (app/data/) from the local database.

The app runs entirely in the browser, so everything it needs is precomputed here:
- cases.json: one record per (claimant, zadośćuczynienie award) with the court's total
  (the "odpowiednia suma"), the insurer's earlier payment, injury categories, factors,
  % uszczerbku and age; short keys keep the file small;
- judgments.json: citation data per judgment (sygnatura, court, date, link);
- quotes/NN.json: per judgment, the court's "too low" verdicts and factor sentences,
  sharded by judgment id so the app loads only what it shows;
- labels.json: categories (with search synonyms) and factor labels in Polish.
"""
from __future__ import annotations

import collections
import json
import logging
import re
import sqlite3
from pathlib import Path

from .categories import CATEGORIES, injury_categories
from .dates import fix_date
from .inflation import BASE_YEAR, SOURCES, factors as inflation_factors
from .outcome import outcome as judgment_outcome
from .reasons import REASONS_VERSION

log = logging.getLogger(__name__)
SHARDS = 64
# Totals outside this range are almost always parsing errors (costs, interest bases, typos).
MIN_TOTAL, MAX_TOTAL = 500, 3_000_000

FACTOR_LABELS = {
    "trwale_skutki": "Trwałe skutki, kalectwo, złe rokowania",
    "dlugie_leczenie": "Długie leczenie lub rehabilitacja",
    "bol_cierpienie": "Silny ból i cierpienie",
    "psychika": "Skutki psychiczne",
    "utrata_aktywnosci": "Utrata aktywności (praca, sport, pasje)",
    "zaleznosc_od_innych": "Zależność od pomocy innych",
    "mlody_wiek": "Młody wiek",
    "oszpecenie": "Blizny, oszpecenie",
    "zycie_rodzinne": "Wpływ na życie rodzinne i osobiste",
    "wiez_ze_zmarlym": "Silna więź ze zmarłym",
    "nagla_smierc": "Nagła, tragiczna śmierć",
}

# Relation of a relative to the deceased, from the parser's `relation` ("syn zmarłego" = the
# claimant is the deceased's son, so the deceased was a parent).
_RELATIONS = [
    ("dziecko", re.compile(r"\b(?:syn|córk|dzieci|dziecko)", re.I)),       # claimant is the deceased's child
    ("rodzic", re.compile(r"\b(?:matk|ojc|ojciec|rodzic)", re.I)),
    ("malzonek", re.compile(r"\b(?:żon|mąż|męż|małżon|partner)", re.I)),
    ("rodzenstwo", re.compile(r"\b(?:brat|siostr|rodzeństw)", re.I)),
    ("inny", re.compile(r"\b(?:wnu|babci|babk|dziad|wuj|ciot)", re.I)),
]
_APPEAL_CASE = re.compile(r"\bA?C[az]\b")


def saos_url(jid: int) -> str:
    """The judgment's readable page in SAOS. (The record's `source.judgmentUrl` points to the
    courts' portal API, which returns raw XML in the browser.)"""
    return f"https://www.saos.org.pl/judgments/{jid}"


def relation_group(relation: str | None) -> str | None:
    for group, rx in _RELATIONS:
        if relation and rx.search(relation):
            return group
    return None


# "śmierć syna" names the deceased; the claimant's relation is the converse
# (death of a son -> the claimant is a parent).
_DEATH_OF = re.compile(
    r"(?:śmier\w*|utrat\w*|odejści\w*|stra\w*)\s+(?:\w+\s+){0,2}?"
    r"(syna|córki|dziecka|dzieci|męża|żony|małżonk\w*|partner\w*|ojca|matki|rodzic\w*|brata|siostry|rodzeństwa|wnuk\w*|wnuczk\w*|babci|dziadka)\b",
    re.I,
)
_CONVERSE = {
    "syna": "rodzic", "córki": "rodzic", "dziecka": "rodzic", "dzieci": "rodzic",
    "męża": "malzonek", "żony": "malzonek", "małżonk": "malzonek", "partner": "malzonek",
    "ojca": "dziecko", "matki": "dziecko", "rodzic": "dziecko",
    "brata": "rodzenstwo", "siostry": "rodzenstwo", "rodzeństwa": "rodzenstwo",
    "wnuk": "inny", "wnuczk": "inny", "babci": "inny", "dziadka": "inny",
}


def relation_from_text(reasoning: str) -> str | None:
    """The claimants' relation to the deceased when the judgment names one dominant relation."""
    counts: dict[str, int] = {}
    for m in _DEATH_OF.finditer(reasoning):
        word = m.group(1).lower()
        group = next((g for k, g in _CONVERSE.items() if word.startswith(k)), None)
        if group:
            counts[group] = counts.get(group, 0) + 1
    if not counts:
        return None
    group, n = max(counts.items(), key=lambda kv: kv[1])
    return group if n >= 2 and n >= 0.7 * sum(counts.values()) else None


def court_total(award: dict) -> tuple[float | None, str]:
    """The court's valuation of the claim and how it was derived."""
    appropriate, paid, awarded = award["amount_appropriate"], award["amount_paid_earlier"], award["amount_awarded"]
    if appropriate:
        return appropriate, "a"          # stated by the court
    if awarded and paid:
        return awarded + paid, "s"       # awarded + already paid
    if awarded:
        return awarded, "w"              # awarded only; an earlier payment may be missing
    return None, ""


def export(conn: sqlite3.Connection, out: Path, parser_version: str = "p2") -> dict:
    out.mkdir(parents=True, exist_ok=True)
    (out / "quotes").mkdir(exist_ok=True)

    factors: dict[int, set[str]] = {}
    quotes: dict[int, dict] = {}
    for jid, section, view, fjson, text in conn.execute(
        "SELECT judgment_id, section, insurer_view, factors, text FROM passages WHERE version = ? ORDER BY judgment_id, sentence",
        (REASONS_VERSION,),
    ):
        f = json.loads(fjson)
        factors.setdefault(jid, set()).update(f)
        q = quotes.setdefault(jid, {"v": [], "f": []})
        if view == "too_low" and len(q["v"]) < 2:
            q["v"].append(text)
        elif f and len(q["f"]) < 6:
            q["f"].append([text, f, section == "assessment"])

    rows = conn.execute(
        "SELECT j.id, j.case_number, j.court_name, j.judgment_date, j.operative_part, j.reasoning, e.result"
        " FROM judgments j JOIN classification c ON c.judgment_id = j.id"
        " JOIN extractions e ON e.judgment_id = j.id AND e.model = 'rules-parser' AND e.prompt_version = ?"
        " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''",
        (parser_version,),
    )
    cases, judgments = [], {}
    for jid, case_number, court, date, operative, reasoning, result in rows:
        x = json.loads(result)
        if not x["is_road_accident"]:
            continue
        date, _ = fix_date(date, case_number, operative)
        cats = text_relation = None
        # The text names one relation per judgment, so it is used only with a single relative
        # (on gold families with several plaintiffs it was wrong in 6 of 7 cases).
        single_relative = sum(cl["role"] != "poszkodowany" for cl in x["claimants"]) == 1
        for claimant in x["claimants"]:
            injured = claimant["role"] == "poszkodowany"
            for a in claimant["awards"]:
                if a["type"] != "zadośćuczynienie" or a["is_monthly"]:
                    continue
                total, how = court_total(a)
                if total is None or not MIN_TOTAL <= total <= MAX_TOTAL:
                    continue
                if injured and cats is None:
                    cats = injury_categories(reasoning)
                relation = None
                if not injured:
                    relation = relation_group(claimant["relation"])
                    if relation is None and single_relative:
                        if text_relation is None:
                            text_relation = relation_from_text(reasoning) or ""
                        relation = text_relation or None
                cases.append({
                    "j": jid,
                    "r": "p" if injured else "b",
                    "rel": relation,
                    "c": (cats or []) if injured else [],
                    "u": claimant["permanent_damage_percent"],
                    "age": claimant["age_at_accident"],
                    "pc": claimant["contributory_negligence_percent"],
                    "f": sorted(factors.get(jid, ())),
                    "t": round(total),
                    "how": how,
                    "pd": round(a["amount_paid_earlier"]) if a["amount_paid_earlier"] else None,
                    "aw": round(a["amount_awarded"]) if a["amount_awarded"] is not None else None,
                    "y": int(date[:4]) if date and date[:4].isdigit() else None,
                    "i": 2 if _APPEAL_CASE.search(case_number or "") else 1,
                    "q": len(quotes.get(jid, {}).get("v", [])),  # "too low" verdicts available to quote
                })
                judgments[jid] = [case_number, court, date, saos_url(jid)]

    shards: dict[int, dict] = {}
    for jid in judgments:
        if jid in quotes:
            shards.setdefault(jid % SHARDS, {})[str(jid)] = quotes[jid]
    for n in range(SHARDS):
        (out / "quotes" / f"{n:02d}.json").write_text(json.dumps(shards.get(n, {}), ensure_ascii=False), encoding="utf-8")
    (out / "cases.json").write_text(json.dumps(cases, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "judgments.json").write_text(json.dumps(judgments, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    labels = {
        "categories": [{"id": c["id"], "label": c["label"], "synonyms": c["synonyms"]} for c in CATEGORIES],
        "factors": FACTOR_LABELS,
        "shards": SHARDS,
        "inflation": {"base": BASE_YEAR, "factors": inflation_factors(), "sources": SOURCES},
        "parser": parser_version,
        "reasons": REASONS_VERSION,
    }
    (out / "labels.json").write_text(json.dumps(labels, ensure_ascii=False, indent=1), encoding="utf-8")
    summary = {"cases": len(cases), "judgments": len(judgments),
               "injured": sum(c["r"] == "p" for c in cases), "relatives": sum(c["r"] == "b" for c in cases)}
    log.info("exported %s to %s", summary, out)
    return summary


# ---------------------------------------------------------------- parser browser (app/przegladarka.html)

BROWSER_SHARDS = 128
_FOLD = str.maketrans("ąćęłńóśźż", "acelnoszz")
_SEARCH_TOKEN = re.compile(r"[a-z]{3,}")
SEARCH_STEM = 6
# Stems found in more than this share of judgments are not indexed (they match almost everything).
SEARCH_MAX_SHARE = 0.4


def fold(text: str) -> str:
    """Lower-case without Polish diacritics; the app folds typed queries the same way."""
    return text.lower().translate(_FOLD)


def _claims_with_evidence(result: dict, evidence: list[dict]) -> list[dict]:
    """Join the parser's claimants (awards) with the evidence list (same order of awards)."""
    names = list(dict.fromkeys(e["claimant"] for e in evidence))
    out = []
    for k, cl in enumerate(result["claimants"]):
        name = names[k] if k < len(names) else "?"
        evs = [e for e in evidence if e["claimant"] == name]
        awards = []
        for i, a in enumerate(cl["awards"]):
            e = evs[i] if i < len(evs) else {}
            awards.append({
                "type": a["type"], "monthly": a["is_monthly"],
                "claimed": a["amount_claimed"], "appropriate": a["amount_appropriate"],
                "paid": a["amount_paid_earlier"], "awarded": a["amount_awarded"],
                "src": {"op": e.get("operative", []), "cl": e.get("claimed", []), "ap": e.get("appropriate", []), "pd": e.get("paid", [])},
            })
        out.append({"who": name, "role": cl["role"], "relation": cl["relation"], "died": cl["victim_died"],
                    "age": cl["age_at_accident"], "u": cl["permanent_damage_percent"],
                    "pc": cl["contributory_negligence_percent"], "injuries": cl["injuries"], "awards": awards})
    return out


def export_browser(conn: sqlite3.Connection, out: Path, parser_version: str = "p2") -> dict:
    """Everything the parser read from each relevant judgment, for the app's searchable browser."""
    (out / "parser").mkdir(parents=True, exist_ok=True)
    (out / "index").mkdir(exist_ok=True)
    passages: dict[int, list] = collections.defaultdict(list)
    for jid, section, view, fjson, text in conn.execute(
        "SELECT judgment_id, section, insurer_view, factors, text FROM passages WHERE version = ? ORDER BY judgment_id, sentence",
        (REASONS_VERSION,),
    ):
        passages[jid].append([section, view, json.loads(fjson), text])

    rows = conn.execute(
        "SELECT j.id, j.case_number, j.court_name, j.judgment_date, j.operative_part, j.reasoning, e.result, e.raw_response"
        " FROM judgments j JOIN classification c ON c.judgment_id = j.id"
        " JOIN extractions e ON e.judgment_id = j.id AND e.model = 'rules-parser' AND e.prompt_version = ?"
        " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''"
        " ORDER BY j.judgment_date DESC",  # the app sorts again after date correction
        (parser_version,),
    )
    browse, shards, texts, outcomes = [], collections.defaultdict(dict), [], []
    for jid, case_number, court, saos_date, operative, reasoning, result, raw in rows:
        date, corrected = fix_date(saos_date, case_number, operative)
        x = json.loads(result)
        evidence = json.loads(raw) if raw else []
        claims = _claims_with_evidence(x, evidence)
        injured = any(c["role"] == "poszkodowany" for c in claims)
        cats = injury_categories(reasoning) if injured else []
        totals = []
        for c in claims:
            for a in c["awards"]:
                if a["type"] == "zadośćuczynienie" and not a["monthly"]:
                    t, _ = court_total({"amount_appropriate": a["appropriate"], "amount_paid_earlier": a["paid"], "amount_awarded": a["awarded"]})
                    if t and MIN_TOTAL <= t <= MAX_TOTAL:
                        totals.append(t)
        ps = passages.get(jid, [])
        roles = "".join(sorted({"p" if c["role"] == "poszkodowany" else "b" for c in claims}))
        instance = 2 if _APPEAL_CASE.search(case_number or "") else 1
        url = saos_url(jid)
        # browse row: id, sygnatura, court, date, instance, parser says road accident, roles,
        # max court total (zadośćuczynienie), injury categories, has a "too low" verdict, number of claims,
        # SAOS's original date when it was an obvious typo that we corrected, and the first-instance
        # outcome for the plaintiff ("full" / "partial" / "loss" / None)
        result_code = judgment_outcome(operative, case_number)
        browse.append([jid, case_number, court, date, instance, int(x["is_road_accident"]), roles,
                       round(max(totals)) if totals else None, cats, int(any(p[1] == "too_low" for p in ps)),
                       sum(len(c["awards"]) for c in claims), saos_date if corrected else None, result_code])
        if result_code and x["is_road_accident"] and date and date[:4].isdigit():
            outcomes.append([roles, cats, int(date[:4]), result_code[0]])   # f / p / l
        shards[jid % BROWSER_SHARDS][str(jid)] = {"url": url, "analysis": x.get("analysis", ""), "claims": claims, "passages": ps}
        sentences = [s for c in claims for a in c["awards"] for v in a["src"].values() for s in v] + [p[3] for p in ps]
        texts.append(" ".join(dict.fromkeys(sentences)))

    for n in range(BROWSER_SHARDS):
        (out / "parser" / f"{n:03d}.json").write_text(json.dumps(shards.get(n, {}), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (out / "browse.json").write_text(json.dumps(browse, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    # First-instance outcomes for the comparison page: [roles, injury categories, year, f|p|l]
    (out / "outcomes.json").write_text(json.dumps(outcomes, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # Inverted index over the source sentences: folded 6-letter stem -> delta-encoded row numbers
    # in browse.json, sharded by the stem's first two letters.
    postings: dict[str, list[int]] = collections.defaultdict(list)
    for row, text in enumerate(texts):
        for stem in sorted({t[:SEARCH_STEM] for t in _SEARCH_TOKEN.findall(fold(text))}):
            postings[stem].append(row)
    limit = SEARCH_MAX_SHARE * len(texts)
    index: dict[str, dict] = collections.defaultdict(dict)
    for stem, ids in postings.items():
        if len(ids) <= limit:
            index[stem[:2]][stem] = [ids[0]] + [b - a for a, b in zip(ids, ids[1:])]
    for prefix, part in index.items():
        (out / "index" / f"{prefix}.json").write_text(json.dumps(part, separators=(",", ":")), encoding="utf-8")
    skipped = sorted(s for s, ids in postings.items() if len(ids) > limit)
    meta = {"rows": len(browse), "shards": BROWSER_SHARDS, "stem": SEARCH_STEM, "prefixes": sorted(index),
            "unindexed": skipped, "parser": parser_version, "reasons": REASONS_VERSION}
    (out / "browse-meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    log.info("browser: %d judgments, %d index stems", len(browse), sum(len(v) for v in index.values()))
    return {"browser_judgments": len(browse), "index_stems": sum(len(v) for v in index.values())}
