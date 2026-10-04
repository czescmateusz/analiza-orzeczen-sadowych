from orzeczenia.evaluate import evaluate
from orzeczenia.extract import Extraction


def extraction(*claimants, road=True):
    return Extraction.model_validate({"is_road_accident": road, "claimants": list(claimants)})


def claimant(role="poszkodowany", awards=(), **kw):
    return {"role": role, "awards": list(awards), **kw}


def award(type_="zadośćuczynienie", **amounts):
    return {"type": type_, **amounts}


def score(report, name):
    ok, total = report.counts[name]
    return ok / total


def test_split_awards_are_summed_before_comparison():
    gold = {1: extraction(claimant(awards=[award(amount_awarded=65000)]))}
    pred = {1: extraction(claimant(awards=[award(amount_awarded=5000), award(amount_awarded=60000)]))}
    report = evaluate(gold, pred)
    assert score(report, "zadośćuczynienie.amount_awarded") == 1.0


def test_known_only_metric_ignores_null_null_matches():
    gold = {1: extraction(claimant(permanent_damage_percent=None, awards=[award(amount_awarded=10000)]))}
    pred = {1: extraction(claimant(permanent_damage_percent=None, awards=[award(amount_awarded=12000)]))}
    report = evaluate(gold, pred)
    assert score(report, "permanent_damage_percent") == 1.0
    assert "permanent_damage_percent [known]" not in report.counts
    assert score(report, "zadośćuczynienie.amount_awarded [known]") == 0.0


def test_claimants_aligned_by_role_and_amounts():
    gold = {1: extraction(
        claimant("osoba_najblizsza", awards=[award(amount_awarded=60000)]),
        claimant("poszkodowany", awards=[award(amount_awarded=5000)]),
    )}
    pred = {1: extraction(
        claimant("poszkodowany", awards=[award(amount_awarded=5000)]),
        claimant("osoba_najblizsza", awards=[award(amount_awarded=60000)]),
    )}
    report = evaluate(gold, pred)
    assert score(report, "claimant role") == 1.0
    assert score(report, "zadośćuczynienie.amount_awarded") == 1.0


def test_missing_claimant_is_counted():
    gold = {1: extraction(claimant(), claimant("osoba_najblizsza"))}
    pred = {1: extraction(claimant())}
    report = evaluate(gold, pred)
    assert report.counts["claimant found"] == [1, 2]
