from orzeczenia.parse import amounts_in, decompose, labelled_amounts, operative_clauses, parse, plaintiffs


def test_amount_with_words_in_brackets_and_interest_base_skipped():
    text = "kwotę 500.000,00 ( pięćset tysięcy ) zł z odsetkami od kwoty 20.000 zł od dnia 1 maja"
    assert [a[0] for a in amounts_in(text)] == [500000.0]


def test_heading_before_or_right_after_amount_and_costs_dropped():
    text = ("tytułem zadośćuczynienia kwotę 30.000 zł, kwotę 720 zł tytułem odszkodowania "
            "oraz kwotę 3.617 zł tytułem zwrotu kosztów procesu")
    assert [(a.value, a.heading) for a in labelled_amounts(text)] == [
        (30000.0, "zadośćuczynienie"), (720.0, "odszkodowanie")]


def test_plaintiffs_and_unspaced_points():
    operative = "sprawy z powództwa A. B. i C. D. (1)\nprzeciwko (...) S.A.\no zapłatę\n1.Oddala powództwo. 2.Znosi koszty."
    assert plaintiffs(operative) == ["A. B.", "C. D. (1)"]
    assert any(c.startswith("1.Oddala") for c in operative_clauses(operative))


def test_decompose_lump_award_from_reasoning():
    sents = ["Sąd uznał za zasadne zadośćuczynienie w kwocie 4.000 złotych.",
             "Za uzasadnione uznano też koszty leczenia w kwocie 720 złotych."]
    parts = decompose(4720.0, sents)
    assert sorted((p.heading, p.value) for p in parts) == [("odszkodowanie", 720.0), ("zadośćuczynienie", 4000.0)]


def test_parse_claim_with_evidence():
    operative = ("sprawy z powództwa M. G.\nprzeciwko (...) S.A.\no zapłatę\n"
                 "I. zasądza od pozwanego na rzecz powoda M. G. kwotę 8.000 zł tytułem zadośćuczynienia;\n"
                 "II. oddala powództwo w pozostałym zakresie.")
    reasoning = ("Powód M. G. wniósł o zasądzenie kwoty 40.000 zł tytułem zadośćuczynienia. "
                 "Powód kierujący samochodem uczestniczył w wypadku drogowym i doznał obrażeń w postaci złamania obojczyka. "
                 "Ubezpieczyciel wypłacił powodowi kwotę 1.500 zł tytułem zadośćuczynienia. "
                 "Odpowiednią kwotą zadośćuczynienia jest kwota 9.500 zł.")
    result = parse(operative, reasoning)
    assert result.extraction.is_road_accident
    award = result.extraction.claimants[0].awards[0]
    assert (award.type, award.amount_claimed, award.amount_appropriate, award.amount_paid_earlier, award.amount_awarded) == (
        "zadośćuczynienie", 40000.0, 9500.0, 1500.0, 8000.0)
    ev = result.evidence[0]
    assert "8.000 zł" in ev.operative[0] and "wypłacił" in ev.paid[0] and "Odpowiednią" in ev.appropriate[0]
