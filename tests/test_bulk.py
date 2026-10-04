from datetime import date

from orzeczenia.bulk import category_for_division_type, category_for_supreme, month_windows


def test_division_categories():
    assert category_for_division_type("Karny") is None
    assert category_for_division_type("Penitencjarny i Nadzoru nad Wykonywaniem Orzeczeń Karnych") is None
    assert category_for_division_type("Cywilny") == "civil"
    assert category_for_division_type("Cywilny Odwoławczy") == "civil"
    assert category_for_division_type("Gospodarczy") == "commercial"
    assert category_for_division_type("Pracy i Ubezpieczeń Społecznych") == "labour"
    assert category_for_division_type("Rodzinny i Nieletnich") == "family"
    assert category_for_division_type(None) == "other"


def test_supreme_court_categories():
    assert category_for_supreme("II CSK 123/20") == "civil"
    assert category_for_supreme("III CZP 32/11") == "civil"
    assert category_for_supreme("I PK 5/19") == "labour"
    assert category_for_supreme("III UK 7/18") == "labour"
    assert category_for_supreme("IV KK 382/14") is None


def test_month_windows_cover_range_and_garbled_years():
    windows = month_windows(date(2020, 11, 1), date(2021, 2, 15))
    assert windows[0] == ("0001-01-01", "2019-12-31")
    assert windows[1] == ("2020-11-01", "2020-11-30")
    assert ("2021-02-01", "2021-02-28") in windows
    assert windows[-1] == ("2022-01-01", "9999-12-31")
