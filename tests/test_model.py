"""The statistical model recovers effects planted in synthetic data."""
import math

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("statsmodels")
from orzeczenia.model import Corpus, fit_structured, keyword_effects  # noqa: E402


def _synthetic(n=600, seed=1):
    """Claims whose log total depends on spine injury, % uszczerbku, the word "renta" and the
    phrase "opieki całodobowej" (planted effects 0.5, 0.02/pp, 0.4 and 0.3)."""
    rng = np.random.default_rng(seed)
    rows, corpus = [], Corpus()
    fillers = ["powód doznał obrażeń", "sąd zważył", "biegły ocenił stan", "pozwany wypłacił świadczenie", "leczenie trwało"]
    for j in range(n):
        spine = rng.random() < 0.3
        u = float(rng.choice([3, 8, 15, 30, 50]))
        word = rng.random() < 0.3
        phrase = rng.random() < 0.3
        log_total = math.log(20000) + 0.5 * spine + 0.02 * u + 0.4 * word + 0.3 * phrase + rng.normal(0, 0.3)
        rows.append({"j": j, "role": "p", "relation": "nieznana", "cats": ["kregoslup"] if spine else [],
                     "u": u, "age": 40, "pc": None, "factors": [], "total": math.exp(log_total), "how": "a",
                     "paid": None, "year": 2015 + j % 5, "appeal": False, "multi": False, "court_level": "SO"})
        # filler sentences vary the text length independently of the planted features
        text = ". ".join(rng.choice(fillers) for _ in range(int(rng.integers(3, 12))))
        if word:
            text += ". przyznano rentę"
        if phrase:
            text += ". wymagał opieki całodobowej"
        corpus.add(j, text)
    return pd.DataFrame(rows), corpus


def test_structured_model_recovers_planted_effect():
    df, _ = _synthetic()
    m = fit_structured(df, "p")
    spine = next(c for c in m["coefs"] if c["label"].startswith("Złamanie kręgosłupa"))
    assert spine["p"] < 0.001 and 0.4 < math.log1p(spine["effect"]) < 0.6


def test_keywords_and_phrases_found_and_fdr_applied():
    df, corpus = _synthetic()
    found = keyword_effects(df, corpus, max_share=0.9, min_shares={1: 0.1, 2: 0.1, 3: 0.1, 4: 0.1})
    by_stems = {k["stems"]: k for k in found}
    assert by_stems["rentę"]["q"] < 0.001 and 0.3 < math.log1p(by_stems["rentę"]["effect"]) < 0.5
    phrase = by_stems["opieki całodob"]
    assert phrase["n"] == 2 and phrase["phrase"] == "opieki całodobowej" and phrase["q"] < 0.001
    # phrases never cross sentence punctuation
    assert not any("zważył biegły" in k["phrase"] for k in found)
