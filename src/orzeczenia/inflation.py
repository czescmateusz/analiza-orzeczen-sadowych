"""Converting awards to constant prices with the consumer price index (CPI) published by GUS.

Courts set the "odpowiednia suma" at the prices of the judgment date (art. 363 § 2 k.c.), so an
amount from year Y is converted to prices of BASE_YEAR by the CPI of every following year:
    factor(Y) = Π CPI(t) / 100 for t = Y+1 … BASE_YEAR.
Judgments from after BASE_YEAR (no annual CPI yet) keep their nominal amounts (factor 1).

Source: GUS, "Roczne wskaźniki cen towarów i usług konsumpcyjnych od 1950 r."
(average annual CPI, previous year = 100), checked 2026-10-04; 2025 from GUS's announcement of
15.01.2026 ("średnioroczny wskaźnik cen towarów i usług konsumpcyjnych ogółem w 2025 r.
w stosunku do 2024 r. wyniósł 103,6"). Update CPI and BASE_YEAR when GUS publishes a new year.
"""
from __future__ import annotations

CPI = {
    2004: 103.5, 2005: 102.1, 2006: 101.0, 2007: 102.5, 2008: 104.2, 2009: 103.5, 2010: 102.6,
    2011: 104.3, 2012: 103.7, 2013: 100.9, 2014: 100.0, 2015: 99.1, 2016: 99.4, 2017: 102.0,
    2018: 101.6, 2019: 102.3, 2020: 103.4, 2021: 105.1, 2022: 114.4, 2023: 111.4, 2024: 103.6,
    2025: 103.6,
}
BASE_YEAR = max(CPI)
SOURCES = [
    "https://stat.gov.pl/obszary-tematyczne/ceny-handel/wskazniki-cen/wskazniki-cen-towarow-i-uslug-konsumpcyjnych-pot-inflacja-/roczne-wskazniki-cen-towarow-i-uslug-konsumpcyjnych/",
    "https://stat.gov.pl/sygnalne/komunikaty-i-obwieszczenia/lista-komunikatow-i-obwieszczen/komunikat-w-sprawie-sredniorocznego-wskaznika-cen-towarow-i-uslug-konsumpcyjnych-ogolem-w-2025-r-,50,13.html",
]


def factor(year: int, base: int = BASE_YEAR) -> float:
    """Multiplier converting an amount from `year` prices to `base` prices."""
    f = 1.0
    for t in range(year + 1, base + 1):
        f *= CPI[t] / 100
    return f


def factors(first: int = min(CPI), base: int = BASE_YEAR, last: int = 2026) -> dict[str, float]:
    return {str(y): round(factor(min(y, base), base), 4) for y in range(first, last + 1)}
