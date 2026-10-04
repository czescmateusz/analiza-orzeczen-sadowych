from datetime import date

from orzeczenia.dates import case_year, fix_date

TODAY = date(2026, 10, 4)


def test_case_year_from_header_or_saos():
    assert case_year("I C 781/19") == 2019
    assert case_year("I C 870/17", "Sygnatura akt XII C 1016/14 WYROK") == 2014   # the header wins
    assert case_year(None) is None


def test_obvious_typos_are_fixed():
    assert fix_date("3013-12-04", "I ACa 772/13", today=TODAY) == ("2013-12-04", True)
    assert fix_date("2010-01-30", "I C 781/19", today=TODAY) == ("2020-01-30", True)    # 2019 or 2020: nearer case year + 1
    assert fix_date("2012-04-20", "I C 283/20", today=TODAY) == ("2021-04-20", True)    # 2012 -> 2021 (swap) beats 2022
    assert fix_date("2031-05-10", "I C 100/21", today=TODAY) == ("2021-05-10", True)   # swapped digits


def test_plausible_or_unfixable_dates_are_kept():
    assert fix_date("2016-12-02", "I C 870/17", "Sygnatura akt XII C 1016/14 WYROK", today=TODAY) == ("2016-12-02", False)
    assert fix_date("2018-03-01", "I C 5/17", today=TODAY) == ("2018-03-01", False)
    assert fix_date("1999-01-01", "I C 5/17", today=TODAY) == ("1999-01-01", False)    # no single-digit fix in range
    assert fix_date(None, "I C 5/17", today=TODAY) == (None, False)
