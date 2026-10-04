"""Statistical model of the courts' valuations (zadośćuczynienie) and their trend over time.

Output: `app/data/model.json`, read by the app's methodology page (app/metodologia.html).

Keyword models control for the reasoning's vocabulary size (log of distinct stems); without
that control 2,302 of 4,000 stems came out significant, mostly because long reasonings
(serious cases) contain more words.

1. Structured models (OLS on log of the court's total, one row per claim, standard errors
   clustered by judgment), separately for injured persons and for relatives after a death:
   injury categories, % uszczerbku bands, age bands, factors stated by the court, contributory
   negligence found, second instance, several plaintiffs, and year fixed effects.
   A coefficient b reads as "×exp(b)" on the total, reported as a % change.
2. Keywords and phrases: for each word stem and each 2-, 3- and 4-word phrase of stems in the
   court's part of the reasoning, its partial association with the log total after
   controlling for the same covariates. Method: Frisch–Waugh–Lovell on judgment-level data,
   HC1 errors, Benjamini–Hochberg FDR over all features tested. Phrases never cross
   sentence punctuation. Exploratory: words co-occur with circumstances; they don't cause
   amounts.
3. Trend: per year, median and quartiles of court totals and insurers' earlier payments,
   plus the model's year effects (a composition-adjusted index).
4. Success: first-instance outcome per judgment (outcome.py: full / partial win, loss).
   Logistic regression of "won anything" vs "claim dismissed", reported as average
   marginal effects in percentage points (HC1 errors), plus the words and phrases that are
   more frequent in lost cases (linear probability model, same method as point 2).
"""
from __future__ import annotations

import collections
import json
import logging
import math
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats as sstats

from .categories import CATEGORIES, injury_categories
from .dates import fix_date
from .outcome import outcome as judgment_outcome
from .export import FACTOR_LABELS, MAX_TOTAL, MIN_TOTAL, court_total, relation_from_text, relation_group
from .reasons import REASONS_VERSION

log = logging.getLogger(__name__)

FIRST_YEAR, BASE_YEAR = 2012, 2015
_APPEAL_CASE = re.compile(r"\bA?C[az]\b")
_COURT_PART = re.compile(r"ustalił\w*,? (?:co następuje|następując)|stan\w* faktyczn|Sąd (?:\w+ ){0,2}ustalił", re.I)
_TOKEN = re.compile(r"[a-ząćęłńóśźż]{3,}|[.;:!?()]")   # words of 3+ letters; punctuation breaks phrases
STEM = 7
MAX_N = 4
# Tested per phrase length (the most frequent first) and minimum share of judgments.
NGRAM_CAP = {1: 3000, 2: 3000, 3: 2000, 4: 1500}
NGRAM_MIN_SHARE = {1: 0.02, 2: 0.02, 3: 0.01, 4: 0.01}
# Stems that encode the amount itself or the court level (first-instance jurisdiction depends on
# the claim's value: district courts take smaller claims; "SSR/SSO/SSA" are judges' titles in the
# signature), so they would only restate the outcome.
_EXCLUDED_STEMS = re.compile(
    r"^(?:tysi|milio|złot|grosz|kwot|sum|dwadz|trzyd|czter|pięćd|sześć|siedem|osiem|dziew|dzies|jeden|dwie|dwóch|trzech|pięci|setek"
    r"|rejono|okręgo|apelac|ssr$|sso$|ssa$|sędzia|zasądz|oddal|odset|procen|stycz|lutego|marca|kwiet|maja|czerw|lipca|sierp|wrześ|paźdz|listop|grudn)"
)
AGE_BANDS = [("0–17", 0, 17), ("18–30", 18, 30), ("31–50", 31, 50), ("51–65", 51, 65), ("66+", 66, 200)]
U_BANDS = [("0%", 0, 0), ("1–5%", 0.01, 5), ("6–10%", 5.01, 10), ("11–20%", 10.01, 20), ("21–40%", 20.01, 40), ("41%+", 40.01, 1000)]
RELATION_LABELS = {"dziecko": "śmierć rodzica", "rodzic": "śmierć dziecka", "malzonek": "śmierć małżonka/partnera",
                   "rodzenstwo": "śmierć brata/siostry", "inny": "śmierć innej bliskiej osoby", "nieznana": "relacja nieznana"}


class Corpus:
    """The court's part of each reasoning as integer token ids (stems and surface words); 0 = break."""

    def __init__(self):
        self.stem_ids, self.stems = {}, [""]
        self.word_ids, self.words = {}, [""]
        self.docs: dict[int, tuple[np.ndarray, np.ndarray]] = {}

    @staticmethod
    def _id(index: dict, values: list, key: str) -> int:
        i = index.get(key)
        if i is None:
            i = index[key] = len(values)
            values.append(key)
        return i

    def add(self, jid: int, text: str) -> None:
        stems, words = [], []
        for t in _TOKEN.findall(text.lower()):
            if not t[0].isalpha():
                stems.append(0); words.append(0)
                continue
            stems.append(self._id(self.stem_ids, self.stems, t[:STEM]))
            words.append(self._id(self.word_ids, self.words, t))
        self.docs[jid] = (np.array(stems, dtype=np.int32), np.array(words, dtype=np.int32))


def _band(value, bands, missing="brak danych"):
    if value is None:
        return missing
    for label, lo, hi in bands:
        if lo <= value <= hi:
            return label
    return missing


# ---------------------------------------------------------------- dataset

def build_dataset(conn: sqlite3.Connection, parser_version: str = "p2") -> tuple[pd.DataFrame, Corpus]:
    """Claim-level rows and the corpus of the court's part of each kept judgment's reasoning."""
    factors: dict[int, set[str]] = collections.defaultdict(set)
    for jid, fjson in conn.execute("SELECT judgment_id, factors FROM passages WHERE version = ?", (REASONS_VERSION,)):
        factors[jid].update(json.loads(fjson))
    rows, corpus = [], Corpus()
    query = conn.execute(
        "SELECT j.id, j.case_number, j.court_name, j.judgment_date, j.operative_part, j.reasoning, e.result"
        " FROM judgments j JOIN classification c ON c.judgment_id = j.id"
        " JOIN extractions e ON e.judgment_id = j.id AND e.model = 'rules-parser' AND e.prompt_version = ?"
        " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''",
        (parser_version,),
    )
    for jid, case_number, court, date, operative, reasoning, result in query:
        date, _ = fix_date(date, case_number, operative)
        x = json.loads(result)
        year = int(date[:4]) if date and date[:4].isdigit() else None
        if not x["is_road_accident"] or year is None or not FIRST_YEAR <= year <= 2026:
            continue
        claimants = x["claimants"]
        relatives = sum(cl["role"] != "poszkodowany" for cl in claimants)
        cats = text_relation = None
        kept = False
        for cl in claimants:
            injured = cl["role"] == "poszkodowany"
            for a in cl["awards"]:
                if a["type"] != "zadośćuczynienie" or a["is_monthly"]:
                    continue
                total, how = court_total(a)
                if total is None or not MIN_TOTAL <= total <= MAX_TOTAL:
                    continue
                if injured and cats is None:
                    cats = injury_categories(reasoning)
                relation = None
                if not injured:
                    relation = relation_group(cl["relation"])
                    if relation is None and relatives == 1:
                        if text_relation is None:
                            text_relation = relation_from_text(reasoning) or ""
                        relation = text_relation or None
                rows.append({
                    "j": jid, "role": "p" if injured else "b", "relation": relation or "nieznana",
                    "cats": (cats or []) if injured else [], "u": cl["permanent_damage_percent"],
                    "age": cl["age_at_accident"], "pc": cl["contributory_negligence_percent"],
                    "factors": sorted(factors.get(jid, ())), "total": total, "how": how,
                    "paid": a["amount_paid_earlier"], "year": year,
                    "appeal": bool(_APPEAL_CASE.search(case_number or "")), "multi": len(claimants) > 1,
                    "court_level": "SA" if "Apelacyjn" in (court or "") else "SO" if "Okręgow" in (court or "") else "SR",
                })
                kept = True
        if kept:
            m = _COURT_PART.search(reasoning)
            corpus.add(jid, reasoning[m.start():] if m else reasoning)
    return pd.DataFrame(rows), corpus


# ---------------------------------------------------------------- structured models

def _design(df: pd.DataFrame, role: str) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    """Dummy-coded covariates and {column: (group, Polish label)}."""
    X, labels = pd.DataFrame(index=df.index), {}

    def add(col, values, group, label):
        X[col] = values.astype(float)
        labels[col] = (group, label)

    if role == "p":
        for c in CATEGORIES:
            add(f"cat_{c['id']}", df["cats"].apply(lambda v, i=c["id"]: i in v), "Obrażenia", c["label"])
        ub = df["u"].apply(lambda v: _band(v, U_BANDS))
        for label, _, _ in U_BANDS + [("brak danych", 0, 0)]:
            if label != "1–5%":                                   # reference: 1–5%
                add(f"u_{label}", ub == label, "Trwały uszczerbek (wobec 1–5%)", label)
    else:
        for rel, label in RELATION_LABELS.items():
            if rel != "dziecko":                                  # reference: death of a parent
                add(f"rel_{rel}", df["relation"] == rel, "Relacja (wobec śmierci rodzica)", label)
    ab = df["age"].apply(lambda v: _band(v, AGE_BANDS))
    for label, _, _ in AGE_BANDS + [("brak danych", 0, 0)]:
        if label != "31–50":                                      # reference: 31–50
            add(f"age_{label}", ab == label, "Wiek (wobec 31–50 lat)", label)
    # Factors that don't describe this role's situation are left out: in injured persons'
    # cases "bond with the deceased" / "sudden death" only flag that someone else also died,
    # and scarring appears in 33 relatives' claims only.
    skip = {"p": {"wiez_ze_zmarlym", "nagla_smierc"}, "b": {"oszpecenie"}}[role]
    for f, label in FACTOR_LABELS.items():
        if f not in skip:
            add(f"f_{f}", df["factors"].apply(lambda v, f=f: f in v), "Okoliczności wskazane przez sąd", label)
    add("pc", df["pc"].fillna(0) > 0, "Inne", "Przyczynienie się poszkodowanego")
    add("appeal", df["appeal"], "Inne", "Orzeczenie II instancji")
    add("multi", df["multi"], "Inne", "Kilku powodów w sprawie")
    for y in sorted(df["year"].unique()):
        if y != BASE_YEAR:
            add(f"y_{y}", df["year"] == y, f"Rok (wobec {BASE_YEAR})", str(y))
    X = X.loc[:, X.std() > 0]                                     # drop empty dummies
    return X, {k: v for k, v in labels.items() if k in X.columns}


def fit_structured(df: pd.DataFrame, role: str) -> dict:
    sub = df[(df["role"] == role) & df["how"].isin(["a", "s"])].reset_index(drop=True)
    X, labels = _design(sub, role)
    y = np.log(sub["total"])
    res = sm.OLS(y, sm.add_constant(X)).fit(cov_type="cluster", cov_kwds={"groups": sub["j"]})
    ci = res.conf_int()
    coefs = []
    for col, (group, label) in labels.items():
        b, lo, hi = res.params[col], ci.loc[col, 0], ci.loc[col, 1]
        coefs.append({"group": group, "label": label, "n": int(X[col].sum()),
                      "effect": math.expm1(b), "lo": math.expm1(lo), "hi": math.expm1(hi), "p": float(res.pvalues[col])})
    return {"role": role, "n": int(res.nobs), "judgments": int(sub["j"].nunique()), "r2": float(res.rsquared),
            "r2_adj": float(res.rsquared_adj), "baseline": float(math.exp(res.params["const"])), "coefs": coefs}


# ---------------------------------------------------------------- keywords

def _ngram_keys(compact: np.ndarray, n: int) -> np.ndarray:
    """Keys of the n-grams in a sequence of compact stem ids (< 2**16; 0 = break), unique."""
    if len(compact) < n:
        return np.empty(0, dtype=np.uint64)
    win = np.lib.stride_tricks.sliding_window_view(compact, n)
    win = win[(win > 0).all(axis=1)].astype(np.uint64)
    keys = np.zeros(len(win), dtype=np.uint64)
    for k in range(n):
        keys |= win[:, k] << np.uint64(16 * k)
    return np.unique(keys)


def _decode(key: int, n: int) -> list[int]:
    return [(int(key) >> (16 * k)) & 0xFFFF for k in range(n)]


def _controls(g: pd.DataFrame, corpus: Corpus) -> np.ndarray:
    Z = pd.DataFrame(index=g.index)
    Z["role_b"] = (g["role"] == "b").astype(float)
    for c in CATEGORIES:
        Z[f"cat_{c['id']}"] = g["cats"].apply(lambda v, i=c["id"]: i in v).astype(float)
    ub = g["u"].apply(lambda v: _band(v, U_BANDS))
    for label, _, _ in U_BANDS + [("brak danych", 0, 0)]:
        Z[f"u_{label}"] = ((ub == label) & (g["role"] == "p")).astype(float)
    for rel in RELATION_LABELS:
        Z[f"rel_{rel}"] = ((g["relation"] == rel) & (g["role"] == "b")).astype(float)
    for y in sorted(g["year"].unique()):
        Z[f"y_{y}"] = (g["year"] == y).astype(float)
    Z["appeal"], Z["multi"], Z["pc"] = g["appeal"].astype(float), g["multi"].astype(float), (g["pc"].fillna(0) > 0).astype(float)
    # Longer reasonings (bigger cases) contain more words and phrases: control for length so that
    # a feature's effect isn't just "the text is long".
    vocab = {j: len(np.unique(d[0][d[0] > 0])) for j, d in corpus.docs.items()}
    Z["log_vocab"] = np.log1p(g["j"].map(lambda j: vocab.get(j, 0)))
    Z.insert(0, "const", 1.0)
    return Z.to_numpy()


def keyword_effects(df: pd.DataFrame, corpus: Corpus, max_share: float = 0.6,
                    caps: dict[int, int] | None = None, min_shares: dict[int, float] | None = None) -> list[dict]:
    """Partial association of each word stem and 2–4-word stem phrase with the log total (FDR-corrected)."""
    caps, min_shares = caps or NGRAM_CAP, min_shares or NGRAM_MIN_SHARE
    sub = df[df["how"].isin(["a", "s"])].copy()
    sub["y"] = np.log(sub["total"])
    # One row per (judgment, role): the mean log total; covariates from the first claim.
    g = sub.groupby(["j", "role"], as_index=False).agg(
        y=("y", "mean"), cats=("cats", "first"), u=("u", "first"), relation=("relation", "first"), year=("year", "first"),
        appeal=("appeal", "first"), multi=("multi", "first"), pc=("pc", "first"))
    g = g[g["j"].isin(corpus.docs.keys())].reset_index(drop=True)
    return phrase_effects(g, _controls(g, corpus), corpus, max_share, caps, min_shares, effect=math.expm1)


def phrase_effects(g: pd.DataFrame, Zm: np.ndarray, corpus: Corpus, max_share: float = 0.6,
                   caps: dict[int, int] | None = None, min_shares: dict[int, float] | None = None,
                   effect=math.expm1) -> list[dict]:
    """Partial association of every word / 2–4-word phrase with the outcome g["y"] (one row per
    g row; judgments in g["j"]), after the covariates Zm. `effect` maps a coefficient to the
    reported effect: exp(b) − 1 for a log outcome, b for a 0/1 outcome (percentage points / 100)."""
    caps, min_shares = caps or NGRAM_CAP, min_shares or NGRAM_MIN_SHARE
    jids = list(dict.fromkeys(g["j"]))
    n_docs = len(jids)
    excluded = np.array([bool(_EXCLUDED_STEMS.match(st)) for st in corpus.stems])

    # Compact ids for stems frequent enough to be part of any tested feature (an n-gram is never
    # more frequent than its rarest word); everything else breaks phrases like punctuation.
    df1 = np.zeros(len(corpus.stems), dtype=np.int64)
    for j in jids:
        st = corpus.docs[j][0]
        df1[np.unique(st[st > 0])] += 1
    frequent = np.flatnonzero(df1 >= min(min_shares.values()) * n_docs)
    frequent = frequent[np.argsort(-df1[frequent])][:65535]
    compact_of = np.zeros(len(corpus.stems), dtype=np.int64)
    compact_of[frequent] = np.arange(1, len(frequent) + 1)
    stem_of_compact = np.concatenate([[0], frequent])
    compact = {j: compact_of[corpus.docs[j][0]] for j in jids}

    # Candidate features per phrase length.
    features: list[tuple[int, int]] = []          # (n, key)
    shares: list[float] = []
    for n in range(1, MAX_N + 1):
        keys, counts = np.unique(np.concatenate([_ngram_keys(compact[j], n) for j in jids]), return_counts=True)
        ok = (counts >= min_shares[n] * n_docs) & (counts <= max_share * n_docs)
        keys, counts = keys[ok], counts[ok]
        keep = [k for k in np.argsort(-counts)
                if not any(excluded[stem_of_compact[c]] for c in _decode(keys[k], n))][: caps[n]]
        features += [(n, int(keys[k])) for k in keep]
        shares += [counts[k] / n_docs for k in keep]
    col_of = {f: c for c, f in enumerate(features)}

    # Sparse judgment × feature indicators, expanded to the (judgment, role) rows.
    from scipy import sparse
    rows_of_j = collections.defaultdict(list)
    for r, j in enumerate(g["j"]):
        rows_of_j[j].append(r)
    ri, ci = [], []
    for j in jids:
        cols = [col_of[(n, int(k))] for n in range(1, MAX_N + 1) for k in _ngram_keys(compact[j], n) if (n, int(k)) in col_of]
        for r in rows_of_j[j]:
            ri += [r] * len(cols); ci += cols
    W = sparse.csc_matrix((np.ones(len(ri)), (ri, ci)), shape=(len(g), len(features)))

    # Frisch–Waugh–Lovell: residualise the outcome and every feature on the covariates
    # (orthonormal basis of the covariates' column space; rank-safe with year dummies + constant).
    U, sv, _ = np.linalg.svd(Zm, full_matrices=False)
    U = U[:, sv > sv.max() * 1e-10]
    y = g["y"].to_numpy()
    ry = y - U @ (U.T @ y)
    nrow, dof = len(g), len(g) - U.shape[1] - 1
    b = np.empty(len(features)); se = np.empty(len(features)); ss_all = np.empty(len(features))
    for start in range(0, len(features), 1000):
        Wc = W[:, start:start + 1000].toarray()
        RW = Wc - U @ (U.T @ Wc)
        ss = (RW ** 2).sum(axis=0)
        ss_safe = np.where(ss > 0, ss, 1.0)
        bc = (RW * ry[:, None]).sum(axis=0) / ss_safe
        e = ry[:, None] - RW * bc
        b[start:start + 1000], ss_all[start:start + 1000] = bc, ss
        se[start:start + 1000] = np.sqrt((RW ** 2 * e ** 2).sum(axis=0) / ss_safe ** 2 * nrow / dof)  # HC1
    valid = ss_all > 1e-9 * nrow            # features fully explained by the covariates have no own effect
    p = np.ones(len(features))
    p[valid] = 2 * sstats.t.sf(np.abs(b[valid] / se[valid]), dof)
    q = np.ones(len(features))
    pv = p[valid]
    order = np.argsort(pv)
    qv = np.empty_like(pv)
    qv[order] = np.minimum.accumulate((pv[order] * len(pv) / np.arange(1, len(pv) + 1))[::-1])[::-1]  # Benjamini–Hochberg
    q[valid] = np.minimum(qv, 1.0)

    # The most common surface form of each significant feature ("pomocy osób trzecich").
    wanted = {n: np.array([k for (m, k), ok, qq in zip(features, valid, q) if m == n and ok and qq < 0.05], dtype=np.uint64)
              for n in range(1, MAX_N + 1)}
    forms: dict[tuple[int, int], collections.Counter] = collections.defaultdict(collections.Counter)
    for j in jids:
        comp, words = compact[j], corpus.docs[j][1]
        for n in range(1, MAX_N + 1):
            if not len(wanted[n]) or len(comp) < n:
                continue
            win = np.lib.stride_tricks.sliding_window_view(comp, n).astype(np.uint64)
            keys = np.zeros(len(win), dtype=np.uint64)
            for k in range(n):
                keys |= win[:, k] << np.uint64(16 * k)
            hit = np.flatnonzero((win > 0).all(axis=1) & np.isin(keys, wanted[n]))
            for pos in hit:
                counter = forms[(n, int(keys[pos]))]
                if counter.total() < 40:
                    counter[" ".join(corpus.words[w] for w in words[pos:pos + n])] += 1

    out = []
    for c, (n, key) in enumerate(features):
        if not valid[c]:
            continue
        stems = [corpus.stems[stem_of_compact[x]] for x in _decode(key, n)]
        form = forms.get((n, key))
        out.append({"n": n, "key": key, "stems": " ".join(stems), "phrase": form.most_common(1)[0][0] if form else " ".join(stems),
                    "share": float(shares[c]),
                    "effect": effect(b[c]), "lo": effect(b[c] - 1.96 * se[c]), "hi": effect(b[c] + 1.96 * se[c]),
                    "p": float(p[c]), "q": float(q[c])})
    return out


# ---------------------------------------------------------------- success vs. loss

# Year bands for the success model: single years can have no losses at all (2012), which makes
# their effect inestimable in a logit.
YEAR_BANDS = [("2012–2014", 2012, 2014), ("2015–2017", 2015, 2017), ("2018–2020", 2018, 2020),
              ("2021–2023", 2021, 2023), ("2024–2026", 2024, 2026)]
# Words that restate the decision itself ("nie zasługiwało na uwzględnienie", "strona przegrywająca",
# costs) rather than the circumstances: dropped from the phrases of lost cases.
# (stems are 7-letter prefixes, so the patterns are at most 6 letters)
_RESTATES_OUTCOME = re.compile(r"^(?:zasług|przegr|wygryw|oddale|bezzas|niezas|nieuza|koszt|słuszn|szczeg)")
_UFG = re.compile(r"Ubezpieczeniow\w+ Fundusz\w* Gwarancyjn\w*|\bUFG\b", re.I)
OUTCOME_FACTORS = {"p": ["trwale_skutki", "dlugie_leczenie", "bol_cierpienie", "psychika", "utrata_aktywnosci",
                         "zaleznosc_od_innych", "oszpecenie", "zycie_rodzinne", "mlody_wiek"],
                   "b": ["wiez_ze_zmarlym", "nagla_smierc"]}


def build_outcomes(conn: sqlite3.Connection, parser_version: str = "p2") -> tuple[pd.DataFrame, Corpus]:
    """One row per first-instance road-accident judgment with a clear outcome, and its corpus."""
    factors: dict[int, set[str]] = collections.defaultdict(set)
    for jid, fjson in conn.execute("SELECT judgment_id, factors FROM passages WHERE version = ?", (REASONS_VERSION,)):
        factors[jid].update(json.loads(fjson))
    rows, corpus = [], Corpus()
    query = conn.execute(
        "SELECT j.id, j.case_number, j.court_name, j.judgment_date, j.operative_part, j.reasoning, e.result"
        " FROM judgments j JOIN classification c ON c.judgment_id = j.id"
        " JOIN extractions e ON e.judgment_id = j.id AND e.model = 'rules-parser' AND e.prompt_version = ?"
        " WHERE c.is_road_accident AND c.is_personal_injury AND c.is_civil AND j.reasoning != ''",
        (parser_version,),
    )
    for jid, case_number, court, date, operative, reasoning, result in query:
        res = judgment_outcome(operative, case_number)
        if res is None:
            continue
        x = json.loads(result)
        date, _ = fix_date(date, case_number, operative)
        year = int(date[:4]) if date and date[:4].isdigit() else None
        if not x["is_road_accident"] or year is None or not FIRST_YEAR <= year <= 2026:
            continue
        claimants = x["claimants"]
        injured = any(c["role"] == "poszkodowany" for c in claimants)
        relatives = [c for c in claimants if c["role"] != "poszkodowany"]
        relation = None
        if len(relatives) == 1:
            relation = relation_group(relatives[0]["relation"]) or relation_from_text(reasoning)
        us = [c["permanent_damage_percent"] for c in claimants if c["permanent_damage_percent"] is not None]
        types = {a["type"] for c in claimants for a in c["awards"]}
        rows.append({
            "j": jid, "outcome": res, "loss": res == "loss", "year": year,
            "injured": injured, "relative": bool(relatives) or (not claimants and False),
            "cats": injury_categories(reasoning) if injured else [], "u": max(us) if us else None,
            "relation": relation or ("nieznana" if relatives else None),
            "factors": factors.get(jid, set()),
            "court_level": "SO" if "Okręgow" in (court or "") else "SR",
            "ufg": bool(_UFG.search(operative or "")),
            "multi": len(claimants) > 1,
            "pc": any(c["contributory_negligence_percent"] for c in claimants),
            "paid": any(a["amount_paid_earlier"] for c in claimants for a in c["awards"]),
            "odszkodowanie": "odszkodowanie" in types, "renta": "renta" in types,
        })
        m = _COURT_PART.search(reasoning)
        corpus.add(jid, reasoning[m.start():] if m else reasoning)
    return pd.DataFrame(rows), corpus


def _rates(frame: pd.DataFrame) -> dict:
    n = len(frame)
    counts = frame["outcome"].value_counts()
    return {"n": int(n), **{k: float(counts.get(k, 0) / n) if n else None for k in ("full", "partial", "loss")}}


def _success_design(d: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, tuple[str, str]]]:
    X, labels = pd.DataFrame(index=d.index), {}

    def add(col, values, group, label):
        X[col] = values.astype(float)
        labels[col] = (group, label)

    add("relative", d["relative"] & ~d["injured"], "Kto pozywa (wobec: poszkodowany)", "tylko osoby bliskie zmarłego")
    add("both", d["relative"] & d["injured"], "Kto pozywa (wobec: poszkodowany)", "poszkodowany i osoby bliskie")
    for c in CATEGORIES:
        add(f"cat_{c['id']}", d["cats"].apply(lambda v, i=c["id"]: i in v), "Obrażenia", c["label"])
    ub = d["u"].apply(lambda v: _band(v, U_BANDS))
    for label, _, _ in U_BANDS:
        if label != "1–5%":
            add(f"u_{label}", ub == label, "Trwały uszczerbek (wobec 1–5% lub brak danych)", label)
    for rel, label in RELATION_LABELS.items():
        if rel not in ("dziecko", "nieznana"):
            add(f"rel_{rel}", d["relation"] == rel, "Relacja (wobec śmierci rodzica lub nieznanej)", label)
    for role in ("p", "b"):
        for f in OUTCOME_FACTORS[role]:
            add(f"f_{f}", d["factors"].apply(lambda v, f=f: f in v), "Okoliczności wskazane przez sąd", FACTOR_LABELS[f])
    add("so", d["court_level"] == "SO", "Sprawa", "sąd okręgowy (wobec rejonowego)")
    add("ufg", d["ufg"], "Sprawa", "pozwany: Ubezpieczeniowy Fundusz Gwarancyjny")
    add("multi", d["multi"], "Sprawa", "kilku powodów")
    add("pc", d["pc"], "Sprawa", "sąd ustalił przyczynienie się")
    add("paid", d["paid"], "Sprawa", "ubezpieczyciel wypłacił coś przed procesem")
    add("odszk", d["odszkodowanie"], "Sprawa", "także roszczenie o odszkodowanie")
    add("renta", d["renta"], "Sprawa", "także roszczenie o rentę")
    for label, lo, hi in YEAR_BANDS:
        if label != "2015–2017":
            add(f"y_{label}", d["year"].between(lo, hi), "Lata orzeczenia (wobec 2015–2017)", label)
    keep = [c for c in X.columns if 0 < X[c].sum() < len(X) and X[c].sum() >= 10]
    return X[keep], {k: v for k, v in labels.items() if k in keep}


def fit_success(d: pd.DataFrame, corpus: Corpus) -> dict:
    """Who wins against the insurer: rates, a logit of winning (AMEs in pp) and phrases of lost cases."""
    out = {"overall": _rates(d)}
    out["by_year"] = [{"year": int(y), **_rates(g)} for y, g in d.groupby("year") if len(g) >= 30]
    out["by_group"] = [
        {"label": label, **_rates(d[mask])} for label, mask in (
            ("poszkodowani", d["injured"] & ~d["relative"]),
            ("osoby bliskie zmarłego", d["relative"] & ~d["injured"]),
            ("sąd rejonowy", d["court_level"] == "SR"),
            ("sąd okręgowy", d["court_level"] == "SO"),
            ("pozwany: UFG", d["ufg"]),
            ("pozwany: ubezpieczyciel", ~d["ufg"]),
        ) if mask.sum() >= 30]
    X, labels = _success_design(d)
    y = (~d["loss"]).astype(float)
    res = sm.Logit(y, sm.add_constant(X)).fit(disp=0, cov_type="HC1", maxiter=200)
    me = res.get_margeff(at="overall", method="dydx", dummy=True)
    frame = me.summary_frame()
    coefs = []
    for col, (group, label) in labels.items():
        row = frame.loc[col]
        coefs.append({"group": group, "label": label, "n": int(X[col].sum()), "effect": float(row["dy/dx"]),
                      "lo": float(row["Conf. Int. Low"]), "hi": float(row["Cont. Int. Hi."]), "p": float(row["Pr(>|z|)"])})
    out["model"] = {"n": int(res.nobs), "pseudo_r2": float(res.prsquared), "win_rate": float(y.mean()), "coefs": coefs}

    # Words and phrases more / less frequent in lost cases (linear probability model on "loss").
    g = d[["j"]].copy()
    g["y"] = d["loss"].astype(float)
    Z = pd.DataFrame(index=d.index)
    Z["relative"], Z["both"] = (d["relative"] & ~d["injured"]).astype(float), (d["relative"] & d["injured"]).astype(float)
    Z["so"], Z["ufg"] = (d["court_level"] == "SO").astype(float), d["ufg"].astype(float)
    for label, lo, hi in YEAR_BANDS:
        Z[f"y_{label}"] = d["year"].between(lo, hi).astype(float)
    vocab = {j: len(np.unique(doc[0][doc[0] > 0])) for j, doc in corpus.docs.items()}
    Z["log_vocab"] = np.log1p(d["j"].map(lambda j: vocab.get(j, 0)))
    Z.insert(0, "const", 1.0)
    phrases = phrase_effects(g.reset_index(drop=True), Z.to_numpy(), corpus, effect=lambda b: b)
    phrases = [k for k in phrases if not any(_RESTATES_OUTCOME.match(st) for st in k["stems"].split())]
    sig = [k for k in phrases if k["q"] < 0.05]
    out["phrases"] = {"tested": len(phrases), "significant": len(sig), "by_n": {
        str(n): {"loss": sorted([k for k in sig if k["n"] == n and k["effect"] > 0], key=lambda k: -k["effect"])[:20],
                 "win": sorted([k for k in sig if k["n"] == n and k["effect"] < 0], key=lambda k: k["effect"])[:20]}
        for n in range(1, MAX_N + 1)}}
    return out


# ---------------------------------------------------------------- trend

def trend(df: pd.DataFrame, models: dict[str, dict], min_n: int = 30) -> dict:
    out = {}
    for role in ("p", "b"):
        sub = df[(df["role"] == role) & df["how"].isin(["a", "s"])]
        years = []
        year_fx = {c["label"]: c["effect"] for c in models[role]["coefs"] if c["group"].startswith("Rok")}
        for y, grp in sub.groupby("year"):
            if len(grp) < min_n:
                continue
            t, paid = grp["total"], grp["paid"].dropna()
            years.append({"year": int(y), "n": int(len(grp)), "median": float(t.median()), "q1": float(t.quantile(0.25)),
                          "q3": float(t.quantile(0.75)), "paid_median": float(paid.median()) if len(paid) >= min_n else None,
                          "paid_n": int(len(paid)),
                          "adjusted_index": 1.0 if y == BASE_YEAR else (1 + year_fx[str(y)] if str(y) in year_fx else None)})
        out[role] = years
    return out


# ---------------------------------------------------------------- sample description

def sample_description(conn: sqlite3.Connection, df: pd.DataFrame) -> dict:
    bulk = {}
    try:
        b = sqlite3.connect("data/saos_civil.db")
        bulk["seen"], bulk["kept"] = b.execute("SELECT SUM(seen), SUM(kept) FROM dump_progress").fetchone()
    except sqlite3.Error:
        pass
    q = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    model_rows = df[df["how"].isin(["a", "s"])]
    return {
        "saos_seen": bulk.get("seen"), "civil_kept": bulk.get("kept"),
        "candidates": q("SELECT COUNT(*) FROM judgments"),
        "relevant": q("SELECT COUNT(*) FROM classification WHERE is_road_accident AND is_personal_injury AND is_civil"),
        "claims": int(len(df)), "judgments": int(df["j"].nunique()),
        "claims_model": int(len(model_rows)), "judgments_model": int(model_rows["j"].nunique()),
        "how": {k: int(v) for k, v in df["how"].value_counts().items()},
        "years": [int(df["year"].min()), int(df["year"].max())],
        "by_role": {k: int(v) for k, v in df["role"].value_counts().items()},
        "court_level": {k: int(v) for k, v in df.drop_duplicates("j")["court_level"].value_counts().items()},
        "instance2": int(df.drop_duplicates("j")["appeal"].sum()),
    }


def run(conn: sqlite3.Connection, out: Path, parser_version: str = "p2") -> dict:
    outcomes, outcome_corpus = build_outcomes(conn, parser_version)
    log.info("outcomes: %d first-instance judgments (%s)", len(outcomes), outcomes["outcome"].value_counts().to_dict())
    success = fit_success(outcomes, outcome_corpus)
    del outcome_corpus
    df, corpus = build_dataset(conn, parser_version)
    log.info("dataset: %d claims from %d judgments; vocabulary %d stems", len(df), df["j"].nunique(), len(corpus.stems))
    models = {role: fit_structured(df, role) for role in ("p", "b")}
    kw = keyword_effects(df, corpus)
    significant = [k for k in kw if k["q"] < 0.05]
    by_n = {}
    for n in range(1, MAX_N + 1):
        tested = [k for k in kw if k["n"] == n]
        sig = [k for k in significant if k["n"] == n]
        by_n[str(n)] = {"tested": len(tested), "significant": len(sig),
                        "up": sorted([k for k in sig if k["effect"] > 0], key=lambda k: -k["effect"])[:20],
                        "down": sorted([k for k in sig if k["effect"] < 0], key=lambda k: k["effect"])[:20]}
    result = {
        "sample": sample_description(conn, df),
        "models": models,
        "keywords": {"tested": len(kw), "significant": len(significant), "by_n": by_n},
        "trend": trend(df, models),
        "success": success,
        "versions": {"parser": parser_version, "reasons": REASONS_VERSION},
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"claims": len(df), "outcome_judgments": success["overall"]["n"], "win_rate": round(1 - success["overall"]["loss"], 3),
            "features_tested": len(kw), "features_significant": len(significant),
            "by_n": {n: (v["tested"], v["significant"]) for n, v in by_n.items()},
            "r2_injured": round(models["p"]["r2"], 3), "r2_relatives": round(models["b"]["r2"], 3)}
