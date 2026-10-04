"""Compare stored model extractions against hand-labelled gold files.

Claimants are aligned greedily by role and overlap of awarded amounts. Awards are
compared per (claimant, type, is_monthly), with amounts of the same key summed, so
splitting one claim into several entries is not penalised.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .extract import Claimant, Extraction

GOLD_DIR = Path("evaluation") / "gold"
AMOUNT_FIELDS = ("amount_claimed", "amount_appropriate", "amount_paid_earlier", "amount_awarded")


def load_gold(directory: Path = GOLD_DIR) -> dict[int, Extraction]:
    gold = {}
    for path in sorted(directory.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        gold[int(path.stem)] = Extraction.model_validate(data)
    return gold


def _same_amount(a: float | None, b: float | None) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(1.0, 0.01 * abs(a))


def _award_totals(claimant: Claimant) -> dict[tuple[str, bool], dict[str, float | None]]:
    totals: dict[tuple[str, bool], dict[str, float | None]] = {}
    for award in claimant.awards:
        key = (award.type, award.is_monthly)
        slot = totals.setdefault(key, {f: None for f in AMOUNT_FIELDS})
        for f in AMOUNT_FIELDS:
            value = getattr(award, f)
            if value is not None:
                slot[f] = (slot[f] or 0) + value
    return totals


def _stem_tokens(text: str) -> set[str]:
    # Crude Polish stemming: the first 5 letters of each word of 4+ letters.
    return {w[:5] for w in re.findall(r"\w{4,}", text.lower())}


def _injury_recall(gold: list[str], pred: list[str]) -> float | None:
    if not gold:
        return None
    pred_tokens = [_stem_tokens(p) for p in pred]
    hits = sum(1 for g in gold if any(_stem_tokens(g) & p for p in pred_tokens))
    return hits / len(gold)


def _align(gold: list[Claimant], pred: list[Claimant]) -> list[tuple[Claimant, Claimant | None]]:
    def score(g: Claimant, p: Claimant) -> float:
        s = 2.0 if g.role == p.role else 0.0
        g_amounts = {a.amount_awarded for a in g.awards if a.amount_awarded}
        p_amounts = {a.amount_awarded for a in p.awards if a.amount_awarded}
        return s + len(g_amounts & p_amounts)

    remaining = list(pred)
    pairs = []
    for g in gold:
        best = max(remaining, key=lambda p: score(g, p), default=None)
        if best is not None:
            remaining.remove(best)
        pairs.append((g, best))
    return pairs


@dataclass
class Report:
    judgments: int = 0
    counts: dict[str, list[int]] = field(default_factory=lambda: defaultdict(lambda: [0, 0]))  # name -> [correct, total]
    injury_recall: list[float] = field(default_factory=list)

    def add(self, name: str, correct: bool) -> None:
        self.counts[name][0] += int(correct)
        self.counts[name][1] += 1

    def format(self) -> str:
        lines = [f"judgments evaluated: {self.judgments}"]
        for name, (ok, total) in sorted(self.counts.items()):
            lines.append(f"  {name:<45} {ok:>4}/{total:<4} {ok / total:6.1%}")
        if self.injury_recall:
            mean = sum(self.injury_recall) / len(self.injury_recall)
            lines.append(f"  {'injuries (mean recall)':<45} {'':>9} {mean:6.1%}")
        return "\n".join(lines)


def evaluate(gold: dict[int, Extraction], predictions: dict[int, Extraction]) -> Report:
    report = Report()
    for judgment_id, g in gold.items():
        p = predictions.get(judgment_id)
        if p is None:
            continue
        report.judgments += 1
        report.add("is_road_accident", g.is_road_accident == p.is_road_accident)
        report.add("claimant count", len(g.claimants) == len(p.claimants))
        for gc, pc in _align(g.claimants, p.claimants):
            report.add("claimant found", pc is not None)
            if pc is None:
                continue
            report.add("claimant role", gc.role == pc.role)
            report.add("victim_died", gc.victim_died == pc.victim_died)
            for name in ("permanent_damage_percent", "contributory_negligence_percent"):
                g_value, p_value = getattr(gc, name), getattr(pc, name)
                report.add(name, _same_amount(g_value, p_value))
                if g_value is not None:
                    report.add(f"{name} [known]", _same_amount(g_value, p_value))
            if gc.age_at_accident is not None:
                report.add("age_at_accident (when known)", gc.age_at_accident == pc.age_at_accident)
            recall = _injury_recall(gc.injuries, pc.injuries)
            if recall is not None:
                report.injury_recall.append(recall)
            g_totals, p_totals = _award_totals(gc), _award_totals(pc)
            for key, g_amounts in g_totals.items():
                label = f"{key[0]}{' (monthly)' if key[1] else ''}"
                p_amounts = p_totals.get(key)
                report.add(f"award present: {label}", p_amounts is not None)
                for f in AMOUNT_FIELDS:
                    p_value = p_amounts[f] if p_amounts else None
                    report.add(f"{label}.{f}", _same_amount(g_amounts[f], p_value))
                    if g_amounts[f] is not None:
                        # Stricter view: only where the judgment states the value.
                        report.add(f"{label}.{f} [known]", _same_amount(g_amounts[f], p_value))
            for key in p_totals.keys() - g_totals.keys():
                report.add("no spurious award types", False)
            if not p_totals.keys() - g_totals.keys():
                report.add("no spurious award types", True)
    return report
