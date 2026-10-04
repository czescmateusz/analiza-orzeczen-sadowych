"""Select the sentences of a judgment that matter for compensation extraction.

Judgments are long (median reasoning ~25k chars), but amounts, injuries and the
victim's age sit in a small share of sentences. Sending only those to the LLM cuts
the input ~4x (measured on a 150-judgment sample).
"""
from __future__ import annotations

import re

_SENTENCE_SPLIT = re.compile(r"(?<=[.;:])\s+(?=[A-ZŁŚŻŹĆŃÓ0-9])|\n")
# A "sentence" ending in an initial or abbreviation ("M.", "r.", "art.", "k.c.", "(...)")
# was split too early: anonymised names like "powoda M. M. (1) będzie kwota" are common.
_FALSE_END = re.compile(r"(?:\b\w{1,3}\.|\b\w\.\w\.|\(\.\.\.\)\.?)$")
# Courts write "80.000 zł", "4 172,60 zł", "20 000,00-, zł" and OCR'd "zl".
_AMOUNT = re.compile(r"\d[\d .]*(?:,\d\d)?[-,\s]*(?:zł|zl|złotych)\b|\d+(?:,\d+)?\s*%", re.I)
_COMPENSATION = re.compile(
    r"zadośćuczyn|odszkodow|\brent[ayęo]|wypłaci|uszczerb|przyczyni|odpowiedni\w* (?:sum|kwot)|zasądz|nawiązk",
    re.I,
)
_INJURY = re.compile(
    r"doznał\w*|obrażeni|złamani|\burazu?\b|urazy|urazów|wstrząśni|stłucz|skręceni|amputac|blizn"
    r"|zmarł|śmierć|niepełnosprawn|niezdoln\w* do pracy",
    re.I,
)
_AGE = re.compile(r"\bur\.|urodzi\w*|w wieku|\b\d{1,2}\s*(?:lat[a]?|rok\w*)\b|\blat\s*\d{1,2}\b|\bmiał\w* lat", re.I)
# The operative part starts with the court's composition; the substance begins here.
_OPERATIVE_START = re.compile(r"^.*z powództwa", re.I | re.M)

MAX_SENTENCE_CHARS = 800  # longer "sentences" are usually unsplit paragraphs of procedure
MAX_AGE_SENTENCE_CHARS = 300


def sentences(text: str) -> list[str]:
    """Split into sentences, re-joining pieces that were cut after an initial or abbreviation."""
    merged: list[str] = []
    for piece in _SENTENCE_SPLIT.split(text):
        piece = piece.strip()
        if not piece:
            continue
        if merged and _FALSE_END.search(merged[-1]) and not merged[-1].endswith("\n"):
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def _relevant(sentence: str) -> bool:
    if _AMOUNT.search(sentence) and _COMPENSATION.search(sentence):
        return True
    if len(sentence) <= MAX_AGE_SENTENCE_CHARS and _AGE.search(sentence):
        return True
    return len(sentence) <= MAX_SENTENCE_CHARS and bool(_INJURY.search(sentence))


def select(operative_part: str, reasoning: str, max_chars: int = 20_000) -> str:
    """Return the operative part plus relevant reasoning sentences, in original order."""
    match = _OPERATIVE_START.search(operative_part)
    operative = operative_part[match.start() :] if match else operative_part
    kept, used = [], len(operative)
    for sentence in sentences(reasoning):
        if not _relevant(sentence):
            continue
        if used + len(sentence) > max_chars:
            break
        kept.append(sentence)
        used += len(sentence) + 1
    return f"SENTENCJA:\n{operative}\n\nFRAGMENTY UZASADNIENIA:\n" + "\n".join(kept)
