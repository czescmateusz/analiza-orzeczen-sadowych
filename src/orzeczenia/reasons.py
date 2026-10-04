"""Deterministic extraction of quotable passages: why a court valued a claim as it did.

For each judgment's reasoning it finds the part where the court speaks for itself
(findings of fact and legal assessment, not the parties' arguments), and returns the
sentences that
- name a valuation factor (lasting effects, psychological harm, age, family bond, ...), or
- find the insurer's earlier payment too low (`too_low`). An "adequate" verdict was tried
  and dropped: courts rarely state it, and most hits were the parties' positions
  (2-4 of 20 right in a hand-checked sample).

These passages serve two purposes: the app quotes them to back a victim's claim, and
`factor_report` compares how often each factor appears where the court raised the award
vs. where the court awarded nothing beyond the insurer's payment.
"""
from __future__ import annotations

import json
import re
import sqlite3
import statistics
from dataclasses import dataclass, field

from .snippets import sentences

REASONS_VERSION = "r3"

FACTORS: dict[str, re.Pattern] = {name: re.compile(rx, re.I) for name, rx in {
    "trwale_skutki": r"trwał\w* (?:skutk|następstw|uszczerb|kalectw|ograniczeni|zmian|kalectw|niezdolnoś)|kalectw|niepełnospraw"
                     r"|nieodwracaln|(?:złe|niepewn\w*|niekorzystn\w*) rokowani|rokowania? (?:są |jest )?(?:niepewn|niekorzystn|złe)",
    "dlugie_leczenie": r"długotrwał\w* (?:leczeni|rehabilit|hospitaliz|unieruchomieni|proces)|wielokrotn\w* (?:operac|zabieg|hospitaliz)"
                       r"|kolejn\w* (?:operac|zabieg)|nadal (?:się )?(?:leczy|odczuwa|wymaga|korzysta)|do chwili obecnej (?:odczuwa|leczy|wymaga)",
    "bol_cierpienie": r"(?:silny|silne|silnych|znaczn\w*|intensywn\w*|dotkliw\w*|ogromn\w*|duż\w*) (?:ból|bóle|bólu|bólów|cierpieni|dolegliwości bólow)",
    "psychika": r"stresu pourazowego|PTSD|depresj|nerwic|zaburzeni\w* (?:adaptacyjn|lękow|depresyjn|nastroju|psychiczn|snu|emocjonaln)"
                r"|lęk\w* przed (?:jazd|podróż|samochod|ruchem)|koszmar|traum(?!atolog)",
    "utrata_aktywnosci": r"nie (?:może|mógł|mogła|jest w stanie|była w stanie|był w stanie) (?:już )?(?:uprawiać|wykonywać|pracować|grać|jeździć|biegać|tańczyć|wrócić"
                         r"|prowadzić (?:samochod|pojazd|auto|działalnoś|gospodarstw))"
                         r"|(?:zrezygnow\w*|rezygnacj\w*) z (?:pracy|nauki|studi|sport|uprawiani|trening|pasji|hobby|wyjazd|planów|aktywnoś|zajęć|jazdy)"
                         r"|aktywnoś\w* (?:sportow|fizyczn|zawodow|życiow|towarzysk)|hobby|pasj[iea]\b",
    "zaleznosc_od_innych": r"pomoc\w* (?:osób trzecich|innych osób|osoby trzeciej|bliskich)|zdan\w* na pomoc|wymaga\w* (?:stałej |całodobowej )?(?:opieki|pomocy)"
                           r"|niesamodzieln|bezradnoś",
    "mlody_wiek": r"młod\w* wiek|w młodym wieku|młod\w* (?:człowiek|osob|kobiet|mężczyzn|dziewczyn|chłop)|perspektyw\w* życiow|całe (?:dalsze )?życie",
    "oszpecenie": r"blizn|oszpeceni|defekt\w* estetyczn|zeszpec",
    "zycie_rodzinne": r"życi\w* (?:rodzinn|osobist|intymn|seksualn)|plan\w* (?:życiow|na przyszłość|założenia rodziny)|macierzyńst|ojcostw",
    "wiez_ze_zmarlym": r"(?:silna|silną|bliska|bliską|głęboka|głęboką|szczególn\w*|wyjątkow\w*|serdeczn\w*|bardzo dobr\w*) (?:więź|więzi|relacj)"
                       r"|więz\w* (?:emocjonaln|rodzinn|uczuciow)|osamotnieni|samotnoś|pustk",
    "nagla_smierc": r"nagł\w* (?:i niespodziewan\w* )?(?:śmier|odejści|utrat)|niespodziewan\w* (?:śmier|odejści|utrat)|tragiczn\w* (?:śmier|okolicznoś)",
}.items()}

# A sentence about what the insurer paid or granted before the case.
_INSURER = re.compile(
    r"wypłacon|wypłaci|ubezpieczyciel|postępowani\w* likwidacyjn|likwidacji szkody|dotychczas\w* (?:wypłac|przyznan|otrzyman)"
    r"|przyznan\w* (?:przez (?:pozwan|ubezpieczyciel|stron\w* pozwan)|w toku|już)|przyznał\w* (?:powod|już)",
    re.I,
)
# Sentences that mention the insurer but are about interest, deadlines, vehicles or other matters.
_INSURER_OTHER = re.compile(
    r"odset|opóźnieni|zwłok|w terminie|termin\w* (?:wypłat|spełnieni)|spełni\w* świadczeni|pojazd|naprawy|kosztorys|wartoś\w* rynkow"
    r"|regres|art\. 411|kredyt|bank",
    re.I,
)
_NOT = r"\bnie\s+(?:był[aoy]?\s+|stanowi\w*\s+|jest\s+|są\s+|może\s+(?:być|zostać)\s+(?:uznan\w*\s+za\s+)?)?"
_TOO_LOW = re.compile(
    r"zaniżon|nieadekwatn|niewystarczając|symboliczn|niewspółmiern|zbyt nisk|rażąco nisk|nieodpowiedni"
    rf"|{_NOT}(?:adekwatn|odpowiedni|wystarczając)"
    r"|\bnie\s+(?:spełni\w*|rekompensuj\w*|rekompensował\w*|kompensuj\w*|zrekompensował\w*|pokrywa\w*|naprawi\w*)",
    re.I,
)
# A general legal standard rather than this case: not a fact or a verdict about this claimant.
_GENERAL = re.compile(
    r"należy (?:wziąć pod uwagę|uwzględni|mieć na uwadze)|bierze się pod uwagę|Obejmuje ono|w orzecznictwie|w judykaturze|przy (?:szacowaniu|ustalaniu|określaniu)"
    r"|Chodzi tu o|w rozumieniu art|szczególnie wówczas|zgodnie z art|na gruncie art|przesłank\w*|ingerencj\w*|rażąco zawyżon\w* (?:albo|lub)|"
    r"ma (?:charakter|na celu)|należy rozumieć|zakłada uwzględnieni|wskazać można|rzutując|winn[oa] (?:zaś )?być|prowadziłoby|Wśród nich|należą (?:m\.in|między innymi)|powinn\w* (?:stanowić|uwzględniać|być|uwzględnić)|może (?:przyznać|zasądzić)|\bczy\b",
    re.I,
)
# Where the court's own findings start (before: the parties' positions).
_COURT_PART = re.compile(r"ustalił\w*,? (?:co następuje|następując)|stan\w* faktyczn|zważył|Sąd (?:\w+ ){0,2}ustalił", re.I)
_ASSESSMENT = re.compile(r"zważył|W ocenie Sądu|Sąd (?:\w+ ){0,2}(?:uznał|ocenił|doszedł do (?:wniosku|przekonania))|Rozważania", re.I)
# "Powód podniósł, że ..." — a party's argument, not the court's view.
_ATTRIBUTED = re.compile(
    r"^(?:\S+\s+){0,4}(?:powod\w*|powód|powódk\w*|pozwan\w*|apelując\w*|skarżąc\w*|pełnomocnik\w*|apelacj\w*|strona\s+\w+)\s+(?:\S+\s+){0,4}?"
    r"(?:wskazał|podni\w+|twierdził|zarzuci\w*|wni\w+|argumentował|domaga\w*|wywodził|uzasadni\w*|podkreśla\w*|kwestionował|zakwestionował|wyjaśni\w*|zeznał|zeznała|poda\w*)"
    r"|(?:pozwan\w*|powód|powódk\w*|ubezpieczyciel\w*|apelując\w*|skarżąc\w*|stron\w* (?:pozwan|powodow)\w*)\s+(?:\S+\s+){0,3}?"
    r"(?:utrzymywał|twierdził|sto(?:i|ją|ał|ała) (?:\w+ )?na stanowisku|uważał\w*|podnosił\w*|zarzuca\w*|zarzucił\w*|kwestionował\w*|wskazywał\w*)"
    r"|\btwierdz(?:ąc|ił|ili)\b|\btwierdzeni\w*|\bzarzu(?:t|c)\w*|\bzarzucił\w*"
    r"|(?:ocenie|zdaniem|stanowisk\w*|opinii|przekonaniu)\s+(?:powod|powódk|pozwan|apelując|skarżąc|strony|pełnomocnik)|W (?:jego|jej|ich) (?:ocenie|opinii)|zdaniem (?:apelując|skarżąc)",
    re.I,
)
_CITATION = re.compile(r"Sąd(?:u)? Najwyższ|wyrok\w* (?:SN|Sądu Apelacyjnego)|uchwał\w*|LEX|OSNC|Legalis|sygn\.\s*akt|Dz\.\s*U\.", re.I)


@dataclass
class Passage:
    index: int                       # sentence index in the reasoning
    section: str                     # "facts" or "assessment"
    text: str
    factors: list[str] = field(default_factory=list)
    insurer_view: str | None = None  # "too_low" when the court finds the insurer's payment too low


# "nie sposób stwierdzić, że kwota jest nieadekwatna": the court rejects the "too low" argument.
_DENIED = re.compile(
    r"\bnie (?:sposób|można|da się) (?:\w+ ){0,3}?(?:stwierdzić|uznać|przyjąć)|\bnie (?:wykazał\w*|udowodnił\w*)"
    r"|\bnie\s+(?:może|można)\s+(?:\w+\s+){0,3}?uzna\w*\s+za\s+(?:zaniżon|nieadekwatn|niewystarczając|rażąco|zbyt)"
    # "nie będzie ani nadmierne, ani zaniżone": the court describes its own award
    r"|\bani\b[^.;]{0,30}(?:zaniżon|nisk)|\bnie\s+(?:był\w*|jest|są)\s+(?:\w+\s+)?(?:zaniżon|nieadekwatn|niewystarczając|zbyt nisk)|\bnie\s+(?:będzie|jest|było)\s+(?:ani\s+)?(?:nadmiern\w*|zawyżon\w*)",
    re.I,
)
# A sentence about the claimant's demand being excessive, not about the insurer's payment.
_EXCESSIVE_CLAIM = re.compile(r"(?:żąda|dochodzon|domaga)\w*[^;]{0,160}?(?:nadmiern|zawyżon|wygórowan)", re.I)
# Appeal grounds ("- art. 445 § 1 k.c. poprzez ...", "naruszenie art. ...") are a party's arguments.
_APPEAL_GROUND = re.compile(r"^\s*[-–•]?\s*(?:(?:\d{1,2}|[a-z])[.)]\s*)?(?:art\.|przepis\w*|naruszeni\w*|obraz\w* (?:przepis|art)|błęd\w* w ustaleniach)", re.I)
# Words that negate a factor mentioned right after them ("nie wymaga pomocy", "bez trwałych skutków").
# A factor denied after the mention ("symptomy ... nie występują", "aktywność ... nie uległa zmniejszeniu").
_NEGATED_AFTER = re.compile(r"^[^.;]{0,70}?\bnie (?:występuj|wystąpi|uległ|stwierdzono|rozpoznano|stwierdził|było|miał|ma\b|powoduj|wpłynęł|wywarł)", re.I)
_NEGATION = re.compile(r"\b(?:nie|bez|brak\w*|ani)\b[^,;]{0,40}$", re.I)  # "." allowed: initials like "U. W."


# A party's argument anywhere in the sentence ("powódka domagała się ..., wskazując, że kwota
# jest niewspółmierna"): not the court's verdict.
_PARTY_ARGUMENT = re.compile(r"\b(?:domaga\w*|wskazując|twierdząc|podnosząc|argumentując|zarzucając|wnosząc)\b", re.I)


def insurer_view(sentence: str) -> str | None:
    """How the court judges the insurer's earlier payment in this sentence, if it does."""
    if not _INSURER.search(sentence) or _INSURER_OTHER.search(sentence) or _GENERAL.search(sentence):
        return None
    if _EXCESSIVE_CLAIM.search(sentence) or _PARTY_ARGUMENT.search(sentence):
        return None
    if _TOO_LOW.search(sentence) and not _DENIED.search(sentence):
        return "too_low"
    return None


def factors_in(sentence: str) -> list[str]:
    """Factors stated in the sentence, ignoring negated mentions ("nie wymaga pomocy")."""
    found = []
    for name, rx in FACTORS.items():
        for m in rx.finditer(sentence):
            negated = _NEGATION.search(sentence[max(0, m.start() - 45) : m.start()]) or _NEGATED_AFTER.search(sentence[m.end() :])
            if m.group(0).lower().startswith("nie ") or not negated:
                found.append(name)
                break
    return found


def passages(reasoning: str, max_chars: int = 900) -> list[Passage]:
    sents = sentences(reasoning)
    court_start = next((i for i, s in enumerate(sents) if _COURT_PART.search(s)), 0)
    assess_start = next((i for i, s in enumerate(sents) if i >= court_start and _ASSESSMENT.search(s)), len(sents))
    found = []
    for i, s in enumerate(sents[court_start:], court_start):
        if len(s) > max_chars or _CITATION.search(s) or _ATTRIBUTED.search(s) or _GENERAL.search(s) or _APPEAL_GROUND.search(s):
            continue
        section = "assessment" if i >= assess_start else "facts"
        view = insurer_view(s) if section == "assessment" else None
        factors = factors_in(s)
        if factors or view:
            found.append(Passage(i, section, " ".join(s.split()), factors, view))
    return found


# ---------------------------------------------------------------- storage and analysis

def save_passages(conn: sqlite3.Connection, judgment_id: int, found: list[Passage]) -> None:
    conn.execute("DELETE FROM passages WHERE judgment_id = ? AND version = ?", (judgment_id, REASONS_VERSION))
    conn.executemany(
        "INSERT INTO passages VALUES (?, ?, ?, ?, ?, ?, ?)",
        [(judgment_id, REASONS_VERSION, p.index, p.section, p.insurer_view, json.dumps(p.factors), p.text) for p in found],
    )


# Second-instance case numbers ("II Ca 45/16", "I ACa 12/17", "I ACz 3/18"): their awards
# depend on what the first instance did, so the outcome comparison uses first instance only.
_APPEAL_CASE = re.compile(r"\bA?C[az]\b")
# The whole claim dismissed: an "oddala powództwo" clause not limited to "w pozostałej części / ponad".
_DISMISSAL_CLAUSE = re.compile(r"[^;]{0,60}oddala(?:jąc)?[^;]{0,160}", re.I)  # also "w pozostałym zakresie oddala"
_PARTIAL = re.compile(r"pozostał|dalsz|dalej|ponad|części|co do", re.I)
# An award from the defendant to someone other than the defendant or the State, not for costs.
_PLAINTIFF_AWARD = re.compile(r"zasądza od (?!powod|powódk)[^;]{0,200}?na rzecz (?!pozwan|stron\w* pozwan|Skarbu)[^;]*", re.I)


def fully_dismissed(operative: str) -> bool:
    clauses = _DISMISSAL_CLAUSE.findall(operative)
    if not any("powództw" in c.lower() for c in clauses) or any(_PARTIAL.search(c) for c in clauses):
        return False
    return not any("koszt" not in m.lower() for m in _PLAINTIFF_AWARD.findall(operative))


def outcomes(conn: sqlite3.Connection, parser_version: str) -> dict[tuple[int, str], tuple[str, float | None]]:
    """Zadośćuczynienie outcome per (first-instance judgment, claimant role), from the rules parser.

    "raised": the insurer had paid and the court awarded more; the ratio is the court's
    appropriate total ÷ the insurer's payment, when both are known.
    "upheld": the insurer had paid, and the operative part dismisses the whole claim and
    awards the plaintiff nothing (read from the sentencja: the parser's zero awards were
    wrong in 11 of 12 sampled cases).
    """
    rows = conn.execute(
        "SELECT e.judgment_id, e.result, j.case_number, j.operative_part FROM extractions e JOIN judgments j ON j.id = e.judgment_id"
        " WHERE e.model = 'rules-parser' AND e.prompt_version = ? AND e.result IS NOT NULL AND j.reasoning != ''",
        (parser_version,),
    )
    found: dict[tuple[int, str], tuple[str, float | None]] = {}
    for judgment_id, result, case_number, operative in rows:
        if _APPEAL_CASE.search(case_number or ""):
            continue
        extraction = json.loads(result)
        if not extraction["is_road_accident"]:
            continue
        dismissed = fully_dismissed(operative or "")
        for claimant in extraction["claimants"]:
            for a in claimant["awards"]:
                if a["type"] != "zadośćuczynienie" or a["is_monthly"] or not a["amount_paid_earlier"]:
                    continue
                key = (judgment_id, claimant["role"])
                if dismissed:
                    found[key] = ("upheld", None)
                elif a["amount_awarded"]:
                    ratio = a["amount_appropriate"] / a["amount_paid_earlier"] if a["amount_appropriate"] else None
                    found[key] = ("raised", ratio)
    return found


def factor_report(conn: sqlite3.Connection, parser_version: str) -> str:
    """How often the court's reasoning states each factor, where it raised vs. didn't raise the award."""
    factors_by_judgment: dict[int, set[str]] = {}
    too_low: set[int] = set()
    for judgment_id, factors, view in conn.execute(
        "SELECT judgment_id, factors, insurer_view FROM passages WHERE version = ?", (REASONS_VERSION,)
    ):
        factors_by_judgment.setdefault(judgment_id, set()).update(json.loads(factors))
        if view == "too_low":
            too_low.add(judgment_id)
    processed = {r[0] for r in conn.execute("SELECT DISTINCT judgment_id FROM passages WHERE version = ?", (REASONS_VERSION,))}
    results = {k: v for k, v in outcomes(conn, parser_version).items() if k[0] in processed}

    lines = [f"rules-parser {parser_version} outcomes, reasons {REASONS_VERSION}; first-instance judgments only"]
    for role, label in (("poszkodowany", "INJURED PERSON"), ("osoba_najblizsza", "RELATIVE AFTER A DEATH")):
        raised = {j: r for (j, ro), (o, r) in results.items() if ro == role and o == "raised"}
        upheld = {j for (j, ro), (o, _) in results.items() if ro == role and o == "upheld"}
        if not raised or not upheld:
            continue
        pct = lambda ids, test: 100 * sum(1 for j in ids if test(j)) / len(ids)  # noqa: E731
        lines.append(f"\n{label}: court raised the award in {len(raised)} judgments, awarded nothing more in {len(upheld)}")
        lines.append(f"  court found the insurer's payment too low: raised {pct(raised, too_low.__contains__):.0f}%,"
                     f" nothing more {pct(upheld, too_low.__contains__):.0f}%  (sanity check of the outcome labels)")
        lines.append(f"  {'factor stated by the court':26s} {'raised':>7s} {'nothing':>8s} {'diff':>6s}   court total ÷ payment, raised: with / without")
        rows = []
        for name in FACTORS:
            has = lambda j, name=name: name in factors_by_judgment.get(j, ())  # noqa: E731
            r, u = pct(raised, has), pct(upheld, has)
            with_f = [x for j, x in raised.items() if x and has(j)]
            without = [x for j, x in raised.items() if x and not has(j)]
            med = lambda v: f"{statistics.median(v):4.1f}× (n={len(v)})" if len(v) >= 20 else "   -"  # noqa: E731
            rows.append((r - u, f"  {name:26s} {r:6.0f}% {u:7.0f}% {r - u:+5.0f}pp   {med(with_f)} / {med(without)}"))
        lines += [text for _, text in sorted(rows, reverse=True)]
    return "\n".join(lines)


def quotes(conn: sqlite3.Connection, factor: str | None = None, limit: int = 10) -> list[dict]:
    """Court findings that the insurer paid too little, with the factors the court gave, for citing.

    Each item: the verdict sentence, up to two factor sentences from the same judgment
    (preferring `factor`), and the citation (sygnatura, court, date, SAOS link).
    """
    scope = ""
    params: list = [REASONS_VERSION]
    if factor:
        scope = " AND p.judgment_id IN (SELECT judgment_id FROM passages WHERE version = ? AND factors LIKE ?)"
        params += [REASONS_VERSION, f'%"{factor}"%']
    rows = conn.execute(
        "SELECT p.judgment_id, p.text, j.case_number, j.court_name, j.judgment_date, j.source_url"
        " FROM passages p JOIN judgments j ON j.id = p.judgment_id"
        f" WHERE p.version = ? AND p.insurer_view = 'too_low'{scope}"
        " GROUP BY p.judgment_id ORDER BY j.judgment_date DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    found = []
    for judgment_id, verdict, case_number, court, date, url in rows:
        reasons = conn.execute(
            "SELECT text, factors FROM passages WHERE judgment_id = ? AND version = ? AND factors != '[]'"
            " ORDER BY (factors LIKE ?) DESC, section = 'assessment' DESC, sentence LIMIT 2",
            (judgment_id, REASONS_VERSION, f'%"{factor}"%' if factor else ""),
        ).fetchall()
        found.append({
            "verdict": verdict,
            "reasons": [{"text": t, "factors": json.loads(f)} for t, f in reasons],
            "case_number": case_number, "court": court, "date": date,
            "url": url or f"https://www.saos.org.pl/judgments/{judgment_id}",
        })
    return found
