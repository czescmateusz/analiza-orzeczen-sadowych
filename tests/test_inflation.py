import pytest

from orzeczenia.inflation import BASE_YEAR, factor, factors


def test_factor_compounds_following_years():
    assert factor(BASE_YEAR) == 1.0
    assert factor(2024) == pytest.approx(1.036)                 # 2025 CPI only
    assert factor(2022) == pytest.approx(1.114 * 1.036 * 1.036)  # 2023, 2024, 2025
    assert factor(2015) > factor(2020) > 1


def test_years_after_base_are_not_adjusted():
    f = factors(last=BASE_YEAR + 1)
    assert f[str(BASE_YEAR + 1)] == 1.0 and f[str(BASE_YEAR)] == 1.0
