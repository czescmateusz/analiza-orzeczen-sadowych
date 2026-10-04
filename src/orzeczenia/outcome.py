"""Outcome of a first-instance judgment for the plaintiff, read from the operative part (sentencja).

- "full":    the court awards the plaintiff money and dismisses nothing of the claim;
- "partial": it awards money and dismisses the rest ("w pozostałej części oddala", "dalej idące");
- "loss":    it dismisses the claim entirely and awards the plaintiff nothing (except costs);
- None:      no clear outcome (e.g. discontinued, settlement, judgment text without a sentencja),
             or a second-instance judgment (who appealed decides what "success" means there).
"""
from __future__ import annotations

import re

from .reasons import fully_dismissed

_APPEAL_CASE = re.compile(r"\bA?C[az]\b")
# An award from the defendant to the plaintiff side ("na rzecz", "na rzecz: • D. Z.", the typo "rzecz");
# the 120 characters after it tell
# whether it is the claim itself or only the costs of the proceedings.
_AWARD = re.compile(r"zasądza\w*\s+od\s+(?!powod|powódk)[^;]{0,250}?(?:na\s+)?rzecz\s*:?\s*\W{0,3}\s*(?!pozwan|stron\w*\s+pozwan|Skarbu)([^;]{0,120})", re.I)
# Points of the sentencja: "I.", "2)", "III/" ... (judgments separate them with ";" or ".").
_POINT = re.compile(r"(?:^|\s)(?=(?:[IVX]{1,5}|\d{1,2})\s*[.)/]\s*[A-ZŁŚŻa-z])")
_DISMISS = re.compile(r"[^;]{0,60}oddala(?:jąc)?[^;]{0,160}", re.I)
_PARTIAL = re.compile(r"pozostał|dalsz|dalej|ponad|części|co do", re.I)


def plaintiff_awarded(operative: str) -> bool:
    return any("koszt" not in after.lower() for point in _POINT.split(operative) for after in _AWARD.findall(point))


def outcome(operative: str | None, case_number: str | None) -> str | None:
    if not operative or _APPEAL_CASE.search(case_number or ""):
        return None
    if fully_dismissed(operative):
        return "loss"
    if not plaintiff_awarded(operative):
        return None
    dismissals = [c for c in _DISMISS.findall(operative) if "powództw" in c.lower() or "żądani" in c.lower()]
    if any(_PARTIAL.search(c) for c in dismissals):
        return "partial"
    return "full" if not dismissals else "partial"
