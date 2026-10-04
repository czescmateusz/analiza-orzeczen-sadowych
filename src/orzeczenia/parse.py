"""Deterministic, rule-based extraction of claims from a judgment (no LLM).

Core idea: every amount in the text is *labelled* with the nearest heading mention
(zadośćuczynienie / odszkodowanie / renta) or cost mention, looking first just before it
("tytułem zadośćuczynienia kwotę 320 000 zł") and then just after it ("kwotę 3.000 zł
tytułem zadośćuczynienia"). Amounts that are interest bases ("od kwoty X od dnia") or costs
are dropped. On top of that:

1. Awarded amounts come from the operative part (sentencja). Totals without a heading
   ("zasądza kwotę 4.720 zł") are decomposed using the reasoning, where courts break them
   down ("4.000 zł tytułem zadośćuczynienia ... 720 zł").
2. Appeal judgments start from the first-instance awards recited in the reasoning and apply
   the operative changes ("kwotę A obniża / podwyższa do B", "kwotę A zastępuje kwotą B",
   "zasądza dalszą kwotę X", "oddala apelację").
3. From the reasoning, per heading: the claim ("wniósł o zasądzenie"), the court's appropriate
   total ("odpowiednią kwotą jest"), the insurer's earlier payment ("wypłacił"), each taken as
   the nearest labelled amount after the keyword; plus % uszczerbku, % przyczynienia, age,
   injuries and whether the victim died.
4. Every claim keeps the sentences its numbers came from (`ClaimEvidence`).

The result is an `extract.Extraction`, scored with the same gold set as the LLM extractors.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from itertools import combinations

from .extract import Award, Claimant, Extraction, parse_pln
from .snippets import sentences

PARSER_NAME = "rules-parser"
PARSER_VERSION = "p2"

_UP = "A-ZŁŚŻŹĆŃÓ"
_AMOUNT = re.compile(
    r"(?<![\d,.])(\d{1,3}(?:[ . ]\d{3})+|\d+)(?:,(\d{1,2}))?\s*(?:,?-+)?\s*,?\s*(?:\([^()\d]{3,120}\)\s*)?(?:zł|zl|złotych|złote|złoty)\b",
    re.I,
)
_INTEREST_BASE = re.compile(r"(?:od|z)\s+(?:kwot[yaę]\s+|sumy\s+)?$", re.I)
_PERCENT = r"(\d{1,3}(?:,\d+)?)\s*%"

HEADINGS = {
    "zadośćuczynienie": re.compile(r"zadośćuczyn", re.I),
    "renta": re.compile(r"\brent[ayęo]?\b|\brenty\b", re.I),
    "odszkodowanie": re.compile(
        r"odszkodowa|zwrot\w* (?:koszt\w* (?:leczenia|opieki|dojazd|pogrzeb|rehabilit|najmu|nagrob|tłumacz)|wydatk)"
        r"|koszt\w* (?:leczenia|opieki|dojazd|pogrzeb|rehabilit|nagrob)|utracon\w* (?:zarobk|dochod)",
        re.I,
    ),
}
_COSTS = re.compile(
    r"koszt\w* (?:procesu|postępowania|zastępstwa|sądow)|opłat\w*|wydatk\w* (?:poniesion|tymczas)"
    r"|Skarbu Państwa|zaliczk\w*|wynagrodzeni\w* (?:pełnomocnika|biegł)|odsetk\w* (?:skapitalizowan)",
    re.I,
)
_MONTHLY = re.compile(r"miesięczn|co miesiąc|każdego miesiąca|płatn\w* do \d", re.I)
_LUMP_RENT = re.compile(r"skapitalizowan|zaległ", re.I)

_POINT_SPLIT = re.compile(r"(?m)^\s*(?=(?:[IVX]{1,5}|\d{1,2})\s*[.)]\s|[a-z]\)\s|-\s)|(?<=\s)(?=\d{1,2}\.\s?[A-ZŁŚŻ][a-ząćęłńóśźż])")
_NUM = r"[„\"]?(?P<{g}>\d{{1,3}}(?:[ . ]\d{{3}})+(?:,\d{{1,2}})?|\d+(?:,\d{{1,2}})?)\s*(?:zł|złotych)?[”\"]?"
_CHANGE_DOWN_UP = re.compile(
    r"kwot\w*\s+" + _NUM.format(g="a") + r"[^;]{0,120}?(?:obniża|podwyższa|zmniejsza|zwiększa)\s+do\s+(?:kwoty\s+)?" + _NUM.format(g="b"),
    re.I,
)
_CHANGE_REPLACE = re.compile(
    r"kwot\w*\s+" + _NUM.format(g="a") + r"\s*zastępuje\s+(?:ją\s+)?kwot\w*\s+" + _NUM.format(g="b"), re.I
)
_APPEAL = re.compile(r"na skutek apelacji|w wyniku apelacji|z apelacji|na skutek zażalenia", re.I)
_CLAIM_VERB = re.compile(
    r"wni(?:ósł|osła|eśli|osły|osło|osi)\s+o\s+(?:zasądzenie|zapłatę)|domaga(?:ł|ła|li|ły|ją)?\s+się|żąda(?:ł|ła|li|nie)|dochodzi(?:ł|ła|li)?|dochodzon\w*",
    re.I,
)
_FINAL_CLAIM = re.compile(r"ostatecznie|rozszerz|sprecyzowa|zmodyfikowa|po zmianie|modyfikacj", re.I)
_APPROPRIATE = re.compile(
    r"odpowiedni\w*|adekwatn\w*|w łącznej (?:kwocie|wysokości)|łączn\w* (?:kwot|sum)|należn\w* (?:jest|będzie|było|powodowi|powódce)|"
    r"spełni\w* (?:swoj\w* )?funkcj|winn[ao] wynieść|powinn[ao] wynieść",
    re.I,
)
_PAID = re.compile(r"wypłaci\w*|wypłacon\w*|wypłat\w*|przyzna\w* (?:i wypłaci\w*|powod\w*|w toku)|dobrowolnie", re.I)
_RECITAL = re.compile(r"\bzasądził\w*\b|\bzasądzając\b", re.I)
_DEATH = re.compile(r"\bzmarł\w*|\bzginął|\bzginęł\w*|poni\w+ śmier|\bśmierci?\b|446\s*§\s*4", re.I)
_RELATION = re.compile(
    r"\b(syn|córka|córki|matka|matki|ojciec|ojca|żona|żony|mąż|męża|brat|brata|siostra|siostry|wnuk\w*|babci\w*|dziad\w*|"
    r"rodzic\w*|dzieci\w*)\s+(zmarłe(?:go|j)|poszkodowane(?:go|j))",
    re.I,
)
_AGE = re.compile(
    r"w (?:chwili|dniu|dacie) (?:wypadku|zdarzenia|śmierci)[^.]{0,40}?(?:miał\w*|liczył\w*)\s+(?:lat\s+)?(\d{1,2})(?:\s+lat)?"
    r"|w chwili (?:wypadku|zdarzenia) (?:powód|powódka|poszkodowan\w*)[^.]{0,20}?(?:miał\w*)\s+(\d{1,2})",
    re.I,
)
_INJURY_SENTENCE = re.compile(r"doznał\w*[^.]{0,60}?(?:obrażeń|urazu|urazów|złamani|stłucz|wstrząś|skręceni|ran\w*|uszkodzeni)", re.I)
_INJURY_TERMS = re.compile(
    r"złamani|uraz|stłucz|wstrząś|skręceni|zwichnięci|ran[ay]\b|rana\b|krwiak|blizn|amputac|uszkodzeni|obrzęk|pęknięci|"
    r"oparzeni|zaburzeni|depresj|nerwic|niedowład|zespół|otarci",
    re.I,
)
_ROAD_STRONG = re.compile(
    r"wypad\w* (?:komunikacyjn|drogow|samochodow)\w*|kolizj\w*|potrąc\w*|zderz\w*|ruchu (?:drogowym|lądowym)|"
    r"kierując\w* (?:pojazd|samochod|motocykl|autobus|ciągnik|rower)\w*|pasażer\w*|piesz\w*|rowerzyst\w*|motocykl\w*",
    re.I,
)
_OUT_OF_SCOPE = re.compile(
    r"wypadk\w* przy pracy|nieuczciwej konkurencji|błęd\w* (?:medyczn|lekarsk)|leczeni\w* (?:stomatolog|kanałow)|"
    r"gospodarstw\w* roln\w*|umow\w* o (?:honorarium|świadczenie usług prawnych)|nieszczęśliwych wypadków|"
    r"naprawy pojazdu|najmu pojazdu zastępczego|kosztorys",
    re.I,
)
_CITATION = re.compile(r"Sąd(?:u)? Najwyższ|wyrok\w* SN|uchwał\w*|LEX|OSNC|Legalis|sygn\.\s*akt|Dz\.\s*U\.", re.I)
_PERSONAL_INJURY = re.compile(r"zadośćuczyn|uszczerb|obrażeń|446\s*§", re.I)


@dataclass
class ClaimEvidence:
    """The parts of the judgment one claim's numbers were read from."""
    claimant: str
    heading: str
    is_monthly: bool
    operative: list[str] = field(default_factory=list)
    claimed: list[str] = field(default_factory=list)
    appropriate: list[str] = field(default_factory=list)
    paid: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    extraction: Extraction
    evidence: list[ClaimEvidence]


@dataclass
class Labelled:
    value: float
    heading: str | None  # None = unlabelled; costs are dropped entirely
    monthly: bool
    start: int
    end: int


# ---------------------------------------------------------------- amounts and labels

def amounts_in(text: str) -> list[tuple[float, int, int]]:
    """Amounts in zł as (value, start, end), skipping interest bases ("od kwoty X")."""
    found = []
    for m in _AMOUNT.finditer(text):
        if _INTEREST_BASE.search(text[max(0, m.start() - 15) : m.start()]):
            continue
        value = parse_pln(m.group(1) + ("," + m.group(2) if m.group(2) else ""))
        if value:
            found.append((value, m.start(), m.end()))
    return found


def _mentions(text: str) -> list[tuple[int, str]]:
    """(position, label) for every heading or cost mention in the text."""
    out = [(m.start(), name) for name, rx in HEADINGS.items() for m in rx.finditer(text)]
    out += [(m.start(), "costs") for m in _COSTS.finditer(text)]
    return sorted(out)


def labelled_amounts(text: str, window: int = 140) -> list[Labelled]:
    """Every amount with the heading of its nearest label: before it first, then after it."""
    found = amounts_in(text)
    mentions = _mentions(text)
    result = []
    for i, (value, start, end) in enumerate(found):
        prev_end = found[i - 1][2] if i else max(0, start - window)
        next_start = found[i + 1][1] if i + 1 < len(found) else min(len(text), end + window)
        before = [(p, lab) for p, lab in mentions if max(prev_end, start - window) <= p < start]
        after = [(p, lab) for p, lab in mentions if end <= p < min(next_start, end + window)]
        # "kwotę X tytułem Y" puts the label right after; "tytułem Y kwotę X" right before.
        near_after = after[0] if after and after[0][0] - end <= 40 else None
        label = near_after[1] if near_after else (before[-1][1] if before else (after[0][1] if after else None))
        if label == "costs":
            continue
        context = text[max(prev_end, start - 60) : min(next_start, end + 120)]
        monthly = label == "renta" and bool(_MONTHLY.search(context)) and not _LUMP_RENT.search(context)
        result.append(Labelled(value, label, monthly, start, end))
    return result


def heading_of(text: str) -> str | None:
    if _COSTS.search(text) and not HEADINGS["zadośćuczynienie"].search(text):
        return None
    for name in ("zadośćuczynienie", "renta", "odszkodowanie"):
        if HEADINGS[name].search(text):
            return name
    return None


def _num(text: str | None) -> float | None:
    return parse_pln(text) if text else None


# ---------------------------------------------------------------- structure

def operative_clauses(operative: str) -> list[str]:
    """Split the operative part into points/clauses, from the parties onwards."""
    start = re.search(r"(?i)z powództwa|sprawy z\b", operative)
    body = operative[start.start():] if start else operative
    parts = [p.strip() for p in _POINT_SPLIT.split(body) if p.strip()]
    clauses = []
    for part in parts:
        clauses.extend(c.strip() for c in re.split(r";\s*(?=\S)", part) if c.strip())
    return clauses


_NAME = rf"[{_UP}]\.(?:\s?[{_UP}]\.)*(?:\s*\(\d\))?"


def plaintiffs(operative: str) -> list[str]:
    """Plaintiff identifiers (anonymised initials) from 'z powództwa A. B. i C. D.'."""
    m = re.search(r"(?is)(?:z powództwa|sprawy z)\s*:?\s+(.{0,250}?)\s*(?:przeciwko|\n\s*przeciw)", operative)
    if not m:
        return []
    names = re.findall(rf"{_NAME}(?:\s?[{_UP}]\.)?", m.group(1))
    names = [n.strip() for n in names if len(n.replace(" ", "")) >= 4 or "(" in n]
    return list(dict.fromkeys(names))


def _recipient(text: str, names: list[str]) -> str | None:
    m = re.search(rf"na rzecz\s+(?:powod\w*|powódki|małoletni\w*)?\s*({_NAME})", text)
    if not m:
        return None
    got = m.group(1).replace(" ", "")
    for n in names:
        if got.startswith(n.replace(" ", "")) or n.replace(" ", "").startswith(got):
            return n
    return None


# ---------------------------------------------------------------- awards

Awards = dict[tuple[str, str, bool], tuple[float, list[str]]]


def _add(awards: Awards, key, value: float, evidence: str) -> None:
    prev = awards.get(key)
    awards[key] = ((prev[0] if prev else 0.0) + value, (prev[1] if prev else []) + [evidence])


def decompose(total: float, sents: list[str]) -> list[Labelled] | None:
    """Find headed amounts in the reasoning that add up to `total` (a breakdown of a lump award)."""
    # Conclusions come late in the reasoning, so the latest mention of each (value, heading) wins.
    items: dict[tuple[float, str], Labelled] = {}
    for s in sents:
        for a in labelled_amounts(s):
            if a.heading and a.value < total:
                items.pop((a.value, a.heading), None)
                items[(a.value, a.heading)] = a
    pool = list(items.values())[-40:][::-1]
    for size in (2, 3):
        for combo in combinations(pool, size):
            if abs(sum(a.value for a in combo) - total) < 1 and len({(a.heading, a.monthly) for a in combo}) == size:
                return list(combo)
    return None


def heading_for_amount(value: float, sents: list[str]) -> str | None:
    for s in reversed(sents):
        for a in labelled_amounts(s):
            if a.heading and abs(a.value - value) < 1:
                return a.heading
    return None


def _operative_awards(clauses: list[str], names: list[str], default: str, sents: list[str], claimed: set[str]) -> Awards:
    awards: Awards = {}
    for clause in clauses:
        if not re.search(r"(?i)zasądza|przyznaje", clause):
            continue
        who = _recipient(clause, names) or default
        for a in labelled_amounts(clause):
            if a.heading:
                _add(awards, (who, a.heading, a.monthly), a.value, clause)
                continue
            parts = decompose(a.value, sents)
            if parts:
                for p in parts:
                    _add(awards, (who, p.heading, p.monthly), p.value, clause)
                continue
            heading = heading_for_amount(a.value, sents)
            if heading is None:
                # Courts list zadośćuczynienie first; a second unlabelled award is usually odszkodowanie.
                taken = (who, "zadośćuczynienie", False) in awards
                heading = "odszkodowanie" if taken else (next(iter(claimed)) if len(claimed) == 1 else "zadośćuczynienie")
            _add(awards, (who, heading, False), a.value, clause)
            break  # an unlabelled amount is the clause's main award; later ones are details
    return awards


def _recited_first_instance(sents: list[str], names: list[str], default: str) -> Awards:
    """Awards of the first-instance judgment, recited at the start of an appeal's reasoning."""
    awards: Awards = {}
    started = False
    for s in sents[:30]:
        if not started and not _RECITAL.search(s):
            continue
        if started and re.search(r"(?i)apelacj|ustalił\w* następując|podstaw\w* (?:faktyczn|rozstrzygnięcia)", s):
            break
        started = True
        for a in labelled_amounts(s):
            who = _recipient(s[max(0, a.start - 140) : a.start], names) or default
            heading = a.heading or heading_for_amount(a.value, sents[30:]) or "zadośćuczynienie"
            _add(awards, (who, heading, a.monthly), a.value, s)
        if len(awards) >= 6:
            break
    return awards


def _apply_changes(base: Awards, clauses: list[str], default: str) -> Awards:
    """Apply the appeal's operative changes to the recited first-instance awards."""
    awards = dict(base)
    for clause in clauses:
        changed = False
        for pattern in (_CHANGE_DOWN_UP, _CHANGE_REPLACE):
            for m in pattern.finditer(clause):
                a, b = _num(m.group("a")), _num(m.group("b"))
                if not a or b is None:
                    continue
                changed = True
                for key, (value, ev) in list(awards.items()):
                    if abs(value - a) < 1:
                        awards[key] = (b, ev + [clause])
                        break
                else:
                    heading = heading_of(clause) or "zadośćuczynienie"
                    awards[(default, heading, False)] = (b, [clause])
        m = re.search(r"(?i)(?:zasądza|podwyższa)[^;]{0,160}?dalsz\w+ kwot\w*", clause)
        if m and not changed:
            extra = labelled_amounts(clause[m.start():])
            if extra:
                heading = extra[0].heading or heading_of(clause) or "zadośćuczynienie"
                target = next((k for k in awards if k[1] == heading and not k[2]), (default, heading, False))
                _add(awards, target, extra[0].value, clause)
    return awards


# ---------------------------------------------------------------- reasoning values

def _pick(sents: list[str], heading: str, verb: re.Pattern, prefer: re.Pattern | None = None,
          last: bool = True) -> tuple[float | None, list[str]]:
    """The labelled amount nearest after `verb`, in the last/first sentence about `heading`."""
    hits = []
    for s in sents:
        if _CITATION.search(s):
            continue
        for v in verb.finditer(s):
            amounts = [a for a in labelled_amounts(s) if a.start > v.start() and a.heading in (heading, None)]
            amounts = [a for a in amounts if a.heading == heading] or (amounts if heading_of(s) == heading else [])
            if amounts:
                hits.append((s, amounts[0].value))
                break
    if not hits:
        return None, []
    if prefer:
        hits = [h for h in hits if prefer.search(h[0])] or hits
    s, value = hits[-1] if last else hits[0]
    return value, [s]


def _percent(sents: list[str], keyword: re.Pattern, prefer: re.Pattern | None = None) -> float | None:
    values = []
    for s in sents:
        if _CITATION.search(s) or not keyword.search(s):
            continue
        for m in re.finditer(_PERCENT, s):
            v = float(m.group(1).replace(",", "."))
            if 0 < v <= 100:
                values.append((s, v))
    if not values:
        return None
    if prefer:
        pref = [v for v in values if prefer.search(v[0])]
        if pref:
            return pref[-1][1]
    return values[-1][1]


def _injuries(sents: list[str]) -> list[str]:
    for s in sents:
        if _CITATION.search(s) or not _INJURY_SENTENCE.search(s):
            continue
        tail = re.split(r"(?i)doznał\w*\s*(?:licznych\s+)?(?:obrażeń ciała\s*)?(?:w postaci|takich jak)?:?", s, maxsplit=1)[-1]
        parts = [p.strip(" .:-") for p in re.split(r",|;| oraz | a także | i (?=\w+ (?:kości|kręg|stawu|głowy))", tail)]
        items = [p for p in parts if _INJURY_TERMS.search(p) and len(p) < 160]
        if items:
            return items[:12]
    return []


def is_road_accident(operative: str, reasoning: str) -> bool:
    facts = [s for s in sentences(reasoning)[:120] if not _CITATION.search(s)]
    text = " ".join(facts)
    strong = len(_ROAD_STRONG.findall(text))
    out = len(_OUT_OF_SCOPE.findall(text))
    personal = bool(_PERSONAL_INJURY.search(operative + " " + text))
    return personal and strong >= 1 and strong > 2 * out


# ---------------------------------------------------------------- main

def parse(operative: str, reasoning: str) -> ParseResult:
    sents = sentences(reasoning)
    clean = [s for s in sents if not _CITATION.search(s)]
    names = plaintiffs(operative) or ["?"]
    default = names[0]
    road = is_road_accident(operative, reasoning)
    clauses = operative_clauses(operative)
    claimed_headings = {heading_of(s) for s in clean[:15] if _CLAIM_VERB.search(s)} - {None}

    appeal = bool(_APPEAL.search(operative))
    if appeal:
        base = _recited_first_instance(clean, names, default)
        awards = _apply_changes(base, clauses, default) if re.search(r"(?i)\bzmienia\b", operative) else base
        if not awards:
            awards = _operative_awards(clauses, names, default, clean, claimed_headings)
    else:
        awards = _operative_awards(clauses, names, default, clean, claimed_headings)

    if re.search(r"(?i)oddala", operative):
        for heading in claimed_headings:
            if not any(k[1] == heading for k in awards):
                awards[(default, heading, False)] = (0.0, [c for c in clauses if "oddala" in c.lower()][:1])

    death = bool(_DEATH.search(" ".join(clean[:60])))
    relations = [m.group(0) for m in _RELATION.finditer(" ".join(clean[:80]))]
    age = None
    for s in clean:
        m = _AGE.search(s)
        if m:
            age = int(m.group(1) or m.group(2))
            break
    injuries = _injuries(clean)
    damage = _percent(clean, re.compile(r"uszczerb", re.I), prefer=re.compile(r"łączn|ogółem|w sumie|łącznie", re.I))
    contributory = _percent(clean, re.compile(r"przyczyni", re.I),
                            prefer=re.compile(r"(?i)sąd\w* (?:uznał|przyjął|ustalił|określił)|należało|na poziomie"))

    by_claimant: dict[str, Claimant] = {}
    evidence: list[ClaimEvidence] = []
    multi = len(names) > 1
    for (who, heading, monthly), (awarded, ops) in awards.items():
        own = [s for s in clean if who in s] if multi else []
        scope = own or clean
        claimed, ev_claimed = _pick(scope[:60], heading, _CLAIM_VERB, prefer=_FINAL_CLAIM)
        appropriate, ev_appr = _pick(scope, heading, _APPROPRIATE)
        paid, ev_paid = _pick(scope, heading, _PAID, last=False)
        if appropriate is not None and awarded and appropriate < awarded - 1:
            appropriate = None  # a figure below the award is not the appropriate total
        if paid is not None and appropriate is not None and paid >= appropriate:
            paid = None
        if appropriate is None and awarded:
            if paid and not contributory:
                appropriate = awarded + paid
            elif claimed is not None and abs(claimed - awarded) < 1 and not paid:
                appropriate = awarded
        award = Award(
            type=heading, is_monthly=monthly, amount_claimed=claimed, amount_appropriate=appropriate,
            amount_paid_earlier=paid, amount_awarded=awarded, evidence=(ops[0] if ops else "")[:200],
        )
        claimant = by_claimant.get(who)
        if claimant is None:
            rel = [r for r in relations if not multi or who in r]
            relation = rel[0] if rel and (multi or not injuries) else None
            role = "osoba_najblizsza" if (death and (relation or not injuries)) else "poszkodowany"
            claimant = Claimant(
                role=role, relation=relation, victim_died=death, age_at_accident=None if multi else age,
                injuries=injuries if role == "poszkodowany" else [],
                permanent_damage_percent=damage if role == "poszkodowany" else None,
                contributory_negligence_percent=contributory,
            )
            by_claimant[who] = claimant
        claimant.awards.append(award)
        evidence.append(ClaimEvidence(who, heading, monthly, ops, ev_claimed, ev_appr, ev_paid))

    extraction = Extraction(
        analysis=f"{PARSER_NAME} {PARSER_VERSION}: appeal={appeal}, plaintiffs={names}",
        is_road_accident=road,
        claimants=list(by_claimant.values()) if road else [],
    )
    return ParseResult(extraction, evidence)
