"""Pre-determined injury categories for the app (deterministic, no LLM).

Each category has
- `pattern`: what marks it in a judgment's description of the injuries;
- `synonyms`: everyday words a victim might type into the app's search box, matched in
  the browser (lower-case word stems, no diacritics needed: the app folds them).

`injury_categories(reasoning)` reads the court's description of the injuries (sentences
about what the victim suffered, from the findings of fact onwards) and returns the
categories found. With several injured plaintiffs the injuries can't be told apart
reliably, so every injured plaintiff in the judgment gets the union.
"""
from __future__ import annotations

import re

from .snippets import sentences

CATEGORIES: list[dict] = [
    {"id": "glowa", "label": "Uraz głowy, wstrząśnienie mózgu",
     "pattern": r"wstrząśnieni\w* mózgu|ura\w* (?:głowy|czaszkowo|mózgu|czaszki)|krwiak\w* (?:śródmózg|podtwardów|nadtwardów|mózgu)"
                r"|stłuczeni\w* (?:mózgu|głowy)|złamani\w*[^,;.]{0,30}(?:czaszki|sklepieni)|obrzęk\w* mózgu|krwawieni\w* (?:podpajęczyn|śródczaszk)",
     "synonyms": ["glowa", "glowy", "wstrzas", "mozg", "czaszk", "krwiak", "nieprzytomn", "utrata przytomnosci"]},
    {"id": "szyja", "label": "Uraz kręgosłupa szyjnego",
     "pattern": r"kręgosłup\w* szyjn|odcin\w* szyjn|smagnięci|biczow|skręceni\w* (?:kręgosłup|szyi)|kołnierz\w* ortopedyczn",
     "synonyms": ["szyj", "kark", "kregoslup szyjny", "smagniecie", "whiplash", "kolnierz", "odcinek szyjny"]},
    {"id": "kregoslup", "label": "Złamanie kręgosłupa lub miednicy",
     "pattern": r"złamani\w*[^,;.]{0,40}(?:kręg|miednic|kości krzyżow|kości łonow|kości kulszow|panewk)",
     "synonyms": ["kregoslup", "kreg", "miednic", "plecy", "ledzwi", "kosc ogonowa", "krzyz"]},
    {"id": "rdzen", "label": "Uszkodzenie rdzenia kręgowego, paraliż",
     "pattern": r"rdzeni\w* kręgow|porażeni\w*|niedowład\w*|paraplegi|tetraplegi|wózk\w* inwalidzk",
     "synonyms": ["rdzen", "paraliz", "sparalizow", "niedowlad", "wozek", "porazeni"]},
    {"id": "noga", "label": "Złamanie nogi, stopy lub kostki",
     "pattern": r"złamani\w*[^,;.]{0,40}(?:udow|piszczel|strzałk|podudzi|kostki|stopy|rzepki|nogi|kończyny dolnej|stawu skokow|śródstopi|pięt)",
     "synonyms": ["noga", "nogi", "kostk", "stop", "udo", "piszczel", "podudzi", "rzepk", "pieta", "zlamana noga"]},
    {"id": "reka", "label": "Złamanie ręki, ramienia, obojczyka lub dłoni",
     "pattern": r"złamani\w*[^,;.]{0,40}(?:ramien|przedrami|łokci|nadgarst|promieniow|łokciow|dłoni|palc|obojczyk|łopatk|kończyny górnej|śródręcz)",
     "synonyms": ["reka", "reki", "ramie", "nadgarst", "obojczyk", "dlon", "palec", "palc", "lokiec", "lopatk", "zlamana reka"]},
    {"id": "kolano", "label": "Kolano, więzadła, łąkotki, zwichnięcia",
     "pattern": r"(?:uraz|skręceni|stłuczeni|uszkodzeni|zwichnięci|złamani|naderwani|zerwani)\w*[^,;.]{0,30}(?:kolan|więzad|łąkot)"
                r"|łąkot\w*|więzad\w* krzyżow|zwichnięci\w*|skręceni\w* (?:stawu|kolana|kostki)",
     "synonyms": ["kolan", "wiezad", "lakotk", "zwichn", "skrecenie kostki", "staw"]},
    {"id": "klatka", "label": "Żebra, klatka piersiowa, płuca",
     "pattern": r"złamani\w*[^,;.]{0,30}żeb|klatki piersiowej|odm\w* opłucnow|stłuczeni\w* płuc|mostk",
     "synonyms": ["zebr", "klatka", "piersi", "pluc", "odma", "mostek"]},
    {"id": "wewnetrzne", "label": "Narządy wewnętrzne (śledziona, wątroba, nerki, jelita)",
     "pattern": r"śledzion\w*|wątrob\w*|nerk\w*|jelit\w*|krwotok\w* wewnętrzn|pęcherz\w* moczow|trzustk",
     "synonyms": ["sledzion", "watrob", "nerk", "jelit", "brzuch", "krwotok wewnetrzny", "narzady wewnetrzne", "trzustk"]},
    {"id": "twarz", "label": "Twarz, zęby, oko, słuch",
     "pattern": r"twarzoczaszk|złamani\w*[^,;.]{0,30}(?:nosa|żuchw|szczęk|jarzm|oczodoł)|zęb(?:a|ów|y|em)?\b(?! obrotnika)|gałk\w* ocz|utrat\w* (?:wzroku|słuchu|oka)|\bwzrok\w*|\bsłuch(?:u|em)?\b|niedosłuch|głuch\w*|\boka\b|oczodoł",
     "synonyms": ["twarz", "nos", "zab", "zeb", "szczek", "zuchw", "oko", "oczy", "wzrok", "sluch", "ucho"]},
    {"id": "amputacja", "label": "Amputacja, utrata kończyny lub palców",
     "pattern": r"amputac\w*|amputowa\w*|utrat\w* (?:kończyny|palc|ręki|nogi|stopy)|kikut",
     "synonyms": ["amputac", "amputow", "utrata nogi", "utrata reki", "kikut", "proteza"]},
    {"id": "oparzenia_blizny", "label": "Oparzenia, rany, blizny",
     "pattern": r"oparzeni\w*|blizn\w*|ran\w* (?:cięt|szarpan|tłuczon|głow|twarzy)|szwy|zszyci",
     "synonyms": ["oparzen", "blizn", "rana", "rany", "szwy", "szycie", "oszpecen"]},
    {"id": "lekkie", "label": "Stłuczenia, otarcia, lekkie obrażenia",
     "pattern": r"stłuczeni\w*|otarci\w*|podbiegnięci\w*|zasinieni\w*|siniak|powierzchown",
     "synonyms": ["stluczen", "otarc", "siniak", "sinia", "lekkie", "potluczen", "zadrapan"]},
    {"id": "psychika", "label": "Skutki psychiczne (PTSD, depresja, lęk przed jazdą)",
     "pattern": r"stresu pourazowego|PTSD|depresj\w*|nerwic\w*|zaburzeni\w* (?:adaptacyjn|lękow|depresyjn|nastroju|psychiczn|snu|emocjonaln)"
                r"|lęk\w* przed (?:jazd|podróż|samochod|ruchem)|fobi\w*",
     "synonyms": ["ptsd", "depresj", "lek", "strach", "nerwic", "psychi", "koszmar", "bezsennosc", "fobi", "trauma"]},
]
_COMPILED = {c["id"]: re.compile(c["pattern"], re.I) for c in CATEGORIES}

# Sentences that describe the injuries suffered (from the court's findings of fact).
_INJURY_SENTENCE = re.compile(
    r"doznał\w*|obrażeń|obrażeni\w*|rozpozna\w*|złamani|uraz\w*|stłucz|wstrząś|hospitaliz|uszczerb|biegł\w*|zdiagnozowa",
    re.I,
)
_COURT_PART = re.compile(r"ustalił\w*,? (?:co następuje|następując)|stan\w* faktyczn|Sąd (?:\w+ ){0,2}ustalił", re.I)
_CITATION = re.compile(r"Sąd(?:u)? Najwyższ|wyrok\w* SN|uchwał\w*|LEX|OSNC|Legalis|sygn\.\s*akt|Dz\.\s*U\.", re.I)
# Negated injuries ("nie doznał złamania", "bez złamań") are skipped.
_NEGATED = re.compile(r"\b(?:nie|bez|brak\w*|wykluczon\w*)\b[^,;]{0,35}$", re.I)


def categories_in(text: str) -> list[str]:
    found = []
    for cid, rx in _COMPILED.items():
        for m in rx.finditer(text):
            if not _NEGATED.search(text[max(0, m.start() - 40) : m.start()]):
                found.append(cid)
                break
    return found


def injury_categories(reasoning: str) -> list[str]:
    sents = sentences(reasoning)
    start = next((i for i, s in enumerate(sents) if _COURT_PART.search(s)), 0)
    text = " ".join(s for s in sents[start:] if _INJURY_SENTENCE.search(s) and not _CITATION.search(s))
    return categories_in(text)
