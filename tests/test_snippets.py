from orzeczenia.snippets import select

OPERATIVE = (
    "Sygn. akt I C 1/20\nWYROK\nSąd Okręgowy w X\nPrzewodniczący: SSO Jan Nowak\n"
    "po rozpoznaniu sprawy z powództwa A. B.\nprzeciwko (...) S.A.\nzasądza 60.000 zł"
)
REASONING = (
    "Powódka wniosła o zasądzenie kwoty 100.000 zł tytułem zadośćuczynienia. "
    "Pozwany wniósł o oddalenie powództwa. "
    "W wyniku wypadku powódka doznała złamania kości udowej. "
    "Biegły ustalił 15% trwałego uszczerbku na zdrowiu. "
    "Rozprawa została odroczona do dnia 5 maja. "
    "Odpowiednią kwotą zadośćuczynienia jest 80.000 zł, a pozwany wypłacił już 20.000 zł."
)


def test_keeps_amount_and_injury_sentences_only():
    text = select(OPERATIVE, REASONING)
    assert "100.000 zł tytułem zadośćuczynienia" in text
    assert "złamania kości udowej" in text
    assert "15% trwałego uszczerbku" in text
    assert "Odpowiednią kwotą zadośćuczynienia jest 80.000 zł" in text
    assert "oddalenie powództwa" not in text
    assert "odroczona" not in text


def test_drops_court_composition_from_operative_part():
    text = select(OPERATIVE, REASONING)
    assert "SSO Jan Nowak" not in text
    assert "z powództwa A. B." in text and "zasądza 60.000 zł" in text


def test_does_not_split_at_anonymised_initials():
    text = select(OPERATIVE, "Odpowiednim zadośćuczynieniem dla powoda M. M. (1) będzie kwota 100.000 zł, dla powódki A. M. będzie kwota 120.000 zł.")
    assert "powoda M. M. (1) będzie kwota 100.000 zł, dla powódki A. M. będzie kwota 120.000 zł." in text


def test_unusual_amount_formats():
    for sentence in ("Za odpowiednie zadośćuczynienie Sąd przyjął kwotę 20 000,00-, zł oddalając powództwo.", "zasądził kwotę 3.500 zl tytułem zwrotu kosztów."):
        assert sentence in select(OPERATIVE, sentence)


def test_keeps_short_age_sentences():
    for sentence in ("W chwili wypadku powód miał 19 lat, był uczniem liceum.", "M. urodziła się (...) a więc w dniu wypadku miała 3 lata."):
        assert sentence in select(OPERATIVE, sentence)


def test_respects_max_chars():
    long_reasoning = " ".join(f"Powód doznał urazu nr {i}." for i in range(1000))
    assert len(select(OPERATIVE, long_reasoning, max_chars=2000)) <= 2100
