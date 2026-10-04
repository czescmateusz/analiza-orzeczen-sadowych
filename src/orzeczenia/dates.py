"""Correction of obviously wrong judgment dates (typos in the judgments themselves, e.g. "3013").

A date is obviously wrong when its year is in the future, or earlier than the year the case
was registered (the year in the sygnatura, "I C 781/19" -> 2019). The sygnatura is read from
the judgment's own header when present, otherwise SAOS's case number.

The corrected year is a year that
- differs from the wrong one by a single digit, or by swapping two adjacent digits (typical
  typos: 3013 -> 2013, 2010 -> 2020); and
- lies between the case year and 8 years later (and not in the future).

If several years qualify, the one closest to the case year + 1 (the typical first-instance
duration) wins. If none qualifies, the date is left as it is.
"""
from __future__ import annotations

import re
from datetime import date as _date

MAX_DURATION = 8
_SYGN = re.compile(r"(?:Sygn\w*\.?\s*(?:akt)?)\s*[^/\n]{1,30}/(\d{2,4})\b", re.I)
_CASE_YEAR = re.compile(r"/(\d{2,4})\b")


def _full_year(digits: str) -> int:
    y = int(digits)
    if len(digits) == 4:
        return y
    return 2000 + y if y < 50 else 1900 + y


def case_year(case_number: str | None, operative: str | None = None) -> int | None:
    m = _SYGN.search((operative or "")[:300])
    if m:
        return _full_year(m.group(1))
    m = _CASE_YEAR.search(case_number or "")
    return _full_year(m.group(1)) if m else None


def _one_typo(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    diff = [i for i in range(len(a)) if a[i] != b[i]]
    if len(diff) == 1:
        return True
    return len(diff) == 2 and diff[1] == diff[0] + 1 and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]]


def fix_date(judgment_date: str | None, case_number: str | None, operative: str | None = None,
             today: _date | None = None) -> tuple[str | None, bool]:
    """(date, corrected?) – the date unchanged unless it is obviously wrong and fixable."""
    if not judgment_date or not judgment_date[:4].isdigit():
        return judgment_date, False
    year = int(judgment_date[:4])
    now = (today or _date.today()).year
    cy = case_year(case_number, operative)
    wrong = year > now or (cy is not None and year < cy)
    if not wrong or cy is None:
        return judgment_date, False
    candidates = [y for y in range(cy, min(cy + MAX_DURATION, now) + 1) if _one_typo(str(year), str(y))]
    if not candidates:
        return judgment_date, False
    best = min(candidates, key=lambda y: (abs(y - (cy + 1)), y))
    return f"{best:04d}{judgment_date[4:]}", True
