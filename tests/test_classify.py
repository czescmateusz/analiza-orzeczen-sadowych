from orzeczenia.classify import classify

CIVIL = "I Wydział Cywilny"


def test_road_accident_injury_case():
    result = classify(
        "zasądza od pozwanego na rzecz powódki 50.000 zł tytułem zadośćuczynienia",
        "Powódka jako pasażerka uczestniczyła w wypadku komunikacyjnym. Sprawca posiadał "
        "ubezpieczenie OC posiadaczy pojazdów mechanicznych. Doznała uszczerbku na zdrowiu.",
        CIVIL,
    )
    assert result.is_road_accident and result.is_personal_injury and result.is_civil
    assert result.signals["zadoscuczynienie"] == 1


def test_single_strong_signal_is_enough():
    result = classify("", "Powód doznał obrażeń w wypadku komunikacyjnym. Zadośćuczynienie 40.000 zł.", CIVIL)
    assert result.is_road_accident


def test_vehicle_words_alone_are_not_a_road_accident():
    result = classify("zasądza 10.000 zł", "Strony zawarły umowę sprzedaży samochodu. Pojazd był wadliwy.", CIVIL)
    assert not result.is_road_accident
    assert not result.is_personal_injury


def test_criminal_division_is_not_civil():
    result = classify("", "wypadek drogowy, oskarżony kierujący samochodem", "II Wydział Karny")
    assert result.is_road_accident and not result.is_civil
