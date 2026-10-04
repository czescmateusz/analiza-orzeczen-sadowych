from orzeczenia.reasons import factors_in, insurer_view, passages


def test_too_low_verdicts():
    assert insurer_view("W ocenie Sądu wypłacona przez pozwanego kwota 4.000 zł nie jest adekwatna do doznanej krzywdy.") == "too_low"
    assert insurer_view("Przyznane przez ubezpieczyciela zadośćuczynienie było rażąco niskie, a wręcz symboliczne.") == "too_low"
    assert insurer_view("Wypłacone dotychczas zadośćuczynienie nie rekompensuje w pełni krzywdy powódki.") == "too_low"


def test_not_a_too_low_verdict():
    # interest ("niezwłocznie spełnić" once matched "nie spełni")
    assert insurer_view("Od daty decyzji ubezpieczyciel wiedział o roszczeniu i winien niezwłocznie spełnić świadczenie.") is None
    # the court rejects the "too low" argument
    assert insurer_view("Nie sposób stwierdzić, że kwota 20.000 zł, z czego 14.000 zł wypłacono w postępowaniu likwidacyjnym, jest nieadekwatna.") is None
    # the claimant's demand is excessive
    assert insurer_view("Żądana kwota dalszego zadośćuczynienia, przy wypłaconych 26.000 zł, jest nadmierna i nieadekwatna.") is None


def test_factors_and_negation():
    assert factors_in("Po wypadku stała się osobą uzależnioną od pomocy osób trzecich.") == ["zaleznosc_od_innych"]
    assert "psychika" in factors_in("U powódki utrzymują się zaburzenia lękowe i depresyjne.")
    assert factors_in("Obecnie powódka nie wymaga pomocy osób trzecich.") == []
    assert factors_in("Biegły psycholog nie stwierdził u U. W. zaburzeń nerwicowych.") == []
    assert factors_in("Opinia biegłego z zakresu ortopedii i traumatologii k. 171.") == []


def test_passages_skip_party_arguments_and_general_law():
    reasoning = (
        "Powód wniósł o zasądzenie kwoty 40.000 zł. Pozwany podniósł, że wypłacona kwota jest zaniżona tylko zdaniem powoda. "
        "Sąd ustalił następujący stan faktyczny. Powód doznał trwałego kalectwa. "
        "Sąd zważył, co następuje. Obejmuje ono wszystkie cierpienia fizyczne i psychiczne, w tym depresję. "
        "W ocenie Sądu wypłacona przez pozwanego kwota 5.000 zł jest rażąco zaniżona."
    )
    found = passages(reasoning)
    assert [(p.section, p.factors, p.insurer_view) for p in found] == [
        ("facts", ["trwale_skutki"], None),
        ("assessment", [], "too_low"),
    ]


def test_fully_dismissed():
    from orzeczenia.reasons import fully_dismissed

    assert fully_dismissed("1. oddala powództwo; 2. zasądza od powoda na rzecz pozwanego kwotę 3.617 zł tytułem zwrotu kosztów procesu.")
    assert fully_dismissed("I. powództwo oddala w całości; II. odstępuje od obciążania powódki kosztami.")
    assert not fully_dismissed("I. zasądza od pozwanego na rzecz powoda kwotę 5.000 zł; II. w pozostałym zakresie oddala powództwo;")
    assert not fully_dismissed("I. zasądza od pozwanego na rzecz powódki kwotę 4.000 zł; II. dalej idące powództwo oddala;")
    assert not fully_dismissed("1. zasądza od pozwanego na rzecz M. K. (1) tytułem zadośćuczynienia kwotę 10.000 zł; 2. oddala powództwo D. G.;")
