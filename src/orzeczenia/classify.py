"""Rule-based first pass: is this a civil road-accident personal-injury case?

Deliberately simple and transparent. The matched signals are stored so the rules
can be tuned against a hand-labelled (or LLM-labelled) sample later.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

METHOD = "rules-v2"

_I = re.IGNORECASE

TRAFFIC_SIGNALS = {
    "wypadek_komunikacyjny": re.compile(r"wypad\w* (?:komunikacyjn|drogow)\w*", _I),
    "kolizja": re.compile(r"\bkolizj\w*", _I),
    "potracenie": re.compile(r"\bpotrąc\w*", _I),
    "zderzenie": re.compile(r"\bzderz\w*", _I),
    "ruch_drogowy": re.compile(r"ruchu drogow\w*|prawo o ruchu", _I),
    "oc_posiadaczy_pojazdow": re.compile(r"posiadacz\w* pojazd\w* mechaniczn\w*", _I),
    "art_436_kc": re.compile(r"art\.?\s*436\b", _I),
    "ufg": re.compile(r"Ubezpieczeniow\w* Fundusz\w* Gwarancyjn\w*|\bUFG\b"),
    "pojazd": re.compile(r"\b(?:samoch\w*|pojazd\w*|motocykl\w*|autobus\w*|ciągnik\w*)", _I),
    "uczestnik_ruchu": re.compile(r"\b(?:pasażer\w*|piesz[ya]\w*|kierowc\w*|kierując\w*)", _I),
}
# A case only counts as a road accident if at least one of these is present;
# vehicle and road-user words alone are too common (e.g. property disputes).
STRONG_TRAFFIC_SIGNALS = {
    "wypadek_komunikacyjny", "kolizja", "potracenie", "ruch_drogowy",
    "oc_posiadaczy_pojazdow", "art_436_kc", "ufg",
}

INJURY_SIGNALS = {
    "zadoscuczynienie": re.compile(r"zadośćuczynieni\w*", _I),
    "art_445_kc": re.compile(r"art\.?\s*445\b", _I),
    "art_446_kc": re.compile(r"art\.?\s*446\b", _I),
    "uszczerbek": re.compile(r"uszczerb\w* na zdrowiu", _I),
    "renta": re.compile(r"\brent[ayęo]\w*", _I),
}


@dataclass
class Classification:
    is_road_accident: bool
    is_personal_injury: bool
    is_civil: bool
    signals: dict[str, int] = field(default_factory=dict)


def _count(patterns: dict[str, re.Pattern], text: str) -> dict[str, int]:
    counts = {name: len(p.findall(text)) for name, p in patterns.items()}
    return {name: n for name, n in counts.items() if n}


def classify(operative_part: str, reasoning: str, division_name: str | None) -> Classification:
    text = f"{operative_part}\n{reasoning}"
    traffic = _count(TRAFFIC_SIGNALS, text)
    injury = _count(INJURY_SIGNALS, text)
    strong = STRONG_TRAFFIC_SIGNALS & traffic.keys()
    return Classification(
        is_road_accident=bool(strong),
        is_personal_injury=bool(injury.keys() - {"renta"}),
        is_civil="cywiln" in (division_name or "").lower(),
        signals={**traffic, **injury},
    )
