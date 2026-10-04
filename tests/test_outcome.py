from orzeczenia.outcome import outcome


def test_outcomes_from_the_sentencja():
    assert outcome("I. zasądza od pozwanego na rzecz powoda kwotę 30.000 zł; II. zasądza od pozwanego na rzecz powoda kwotę 3.617 zł tytułem zwrotu kosztów procesu;", "I C 1/20") == "full"
    assert outcome("I. zasądza od pozwanego na rzecz powódki kwotę 8.000 zł; II. w pozostałym zakresie powództwo oddala;", "I C 2/20") == "partial"
    assert outcome("1. oddala powództwo; 2. zasądza od powoda na rzecz pozwanego kwotę 3.617 zł tytułem zwrotu kosztów procesu.", "I C 3/20") == "loss"
    # points separated by full stops, costs mentioned in a later point
    assert outcome("I. Zasądza od pozwanego na rzecz powódki G. D. kwotę 60 000 złotych z odsetkami. II. Oddala powództwo w pozostałym zakresie. III. Kosztami obciąża pozwanego.", "I C 4/20") == "partial"
    assert outcome("I. zasądza od pozwanego na rzecz: • D. Z. kwotę 115.000 zł; II. oddala powództwo w pozostałym zakresie;", "I C 5/20") == "partial"


def test_no_outcome_for_appeals_or_missing_sentencja():
    assert outcome("1. oddala apelację; 2. zasądza od powoda na rzecz pozwanego kwotę 450 zł tytułem kosztów", "I Ca 299/17") is None
    assert outcome("Sygn. akt I C 232/17", "I C 232/17") is None
    assert outcome("zasądza od pozwanego na rzecz powoda kwotę 360 zł tytułem kosztów procesu", "I C 6/20") is None
