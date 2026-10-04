"""LLM extraction of claimants, injuries and awards from judgment snippets.

Two backends, chosen by model name:
- Hugging Face Inference Providers (default: Bielik-11B-v3.0-Instruct via "publicai");
  needs an HF token (HF_TOKEN or `hf auth login`).
- Claude API for models named "claude-..."; needs ANTHROPIC_API_KEY.
Both use schema-constrained output, validated against the pydantic models below.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_MODEL = "speakleash/Bielik-11B-v3.0-Instruct"
DEFAULT_PROVIDER = "publicai"
PROMPT_VERSION = "v3"

log = logging.getLogger(__name__)


def parse_pln(value) -> float | None:
    """Parse amounts written the Polish way: '80.000 zł', '4 172,60', '1.000.000,00 złotych'."""
    if value is None or isinstance(value, (int, float)):
        return value
    text = re.sub(r"(zł\w*|pln)", "", str(value), flags=re.I).strip().replace("\xa0", " ")
    if not text:
        return None
    if "," in text:  # decimal comma: dots and spaces are thousand separators
        text = text.replace(".", "").replace(" ", "").replace(",", ".")
    else:  # '80.000' / '80 000' are thousands; a single dot with 1-2 decimals is a decimal point
        text = text.replace(" ", "")
        if not re.fullmatch(r"\d+\.\d{1,2}", text):
            text = text.replace(".", "")
    try:
        return float(text)
    except ValueError:
        return None


class Award(BaseModel):
    type: Literal["zadośćuczynienie", "odszkodowanie", "renta", "inne"]
    amount_claimed: float | None = None
    amount_appropriate: float | None = None
    amount_paid_earlier: float | None = None
    amount_awarded: float | None = None
    is_monthly: bool = False
    evidence: str = ""

    @field_validator("amount_claimed", "amount_appropriate", "amount_paid_earlier", "amount_awarded", mode="before")
    @classmethod
    def _amount(cls, v):
        return parse_pln(v)


class Claimant(BaseModel):
    role: Literal["poszkodowany", "osoba_najblizsza"]
    relation: str | None = None
    sex: Literal["K", "M"] | None = None
    age_at_accident: int | None = None
    victim_died: bool = False
    injuries: list[str] = Field(default_factory=list)
    permanent_damage_percent: float | None = None
    contributory_negligence_percent: float | None = None
    awards: list[Award] = Field(default_factory=list)


class Extraction(BaseModel):
    # First on purpose: with schema-constrained decoding the model writes fields in
    # order, so this is where it can reason about the case before committing to numbers.
    analysis: str = ""
    is_road_accident: bool
    claimants: list[Claimant] = Field(default_factory=list)


RESPONSE_SCHEMA = {"name": "extraction", "schema": Extraction.model_json_schema(), "strict": True}

SYSTEM_PROMPT = """Jesteś asystentem prawnym. Wyodrębniasz dane z fragmentów polskich wyroków \
sądów powszechnych w sprawach o zadośćuczynienie i odszkodowanie po wypadkach drogowych.
Odpowiadasz wyłącznie jednym obiektem JSON. Najpierw w polu "analysis" krótko (3–8 zdań) \
analizujesz sprawę, potem wypełniasz pozostałe pola zgodnie z tą analizą.
Nie zgaduj: jeśli informacji nie ma w tekście, wpisz null (albo pustą listę)."""

USER_TEMPLATE = """Jak analizować (zapisz to w "analysis"):
1. Kim są powodowie? Każdy powód to osobny obiekt w "claimants" (np. żona i córka zmarłego = 2 obiekty). \
Pozwany (ubezpieczyciel, UFG, sprawca) nie jest powodem.
2. Czy to wyrok sądu II instancji (apelacja)? Jeśli tak, ustal co zasądził sąd I instancji i co zmienił sąd \
II instancji: "oddala apelację" = bez zmian; "podwyższa do", "obniża do", "zmienia ... w ten sposób, że" = \
nowa kwota; "oddala powództwo" = 0. Kwota zasądzona to stan PO apelacji.
3. Dla każdego roszczenia każdego powoda ustal: żądanie, łączną kwotę uznaną przez sąd za odpowiednią, \
wypłatę ubezpieczyciela przed procesem i kwotę zasądzoną. Zwykle: zasądzona = odpowiednia × (1 − przyczynienie) − wypłacona wcześniej.

Pola:
- is_road_accident: true tylko, jeśli szkoda na osobie powstała w wypadku drogowym/komunikacyjnym \
(nie: wypadek przy rozładunku, spór reklamowy, szkoda wyłącznie na mieniu).
- role: "poszkodowany" (powód sam doznał obrażeń w wypadku) albo "osoba_najblizsza" (członek rodziny zmarłego \
lub poszkodowanego). Powód ranny w wypadku, w którym zginął jego bliski, to "poszkodowany".
- relation: np. "syn zmarłego", "żona zmarłego"; null dla poszkodowanego.
- sex: "K"/"M"; age_at_accident: wiek w chwili wypadku (np. "w chwili wypadku miał 19 lat").
- victim_died: true, jeśli bezpośrednia ofiara wypadku zmarła.
- injuries: obrażenia i ich trwałe skutki, krótko po polsku (np. "złamanie trzonu kręgu L1").
- permanent_damage_percent: łączny % trwałego uszczerbku przyjęty przez sąd (sumuj, jeśli sąd sumuje).
- contributory_negligence_percent: % przyczynienia się poszkodowanego przyjęty przez sąd (art. 362 k.c.).
- awards (osobno każde roszczenie):
  - type: "zadośćuczynienie" (art. 445 za własne obrażenia; art. 446 § 4 lub art. 448 za śmierć bliskiego — \
jeśli powód ma oba, to dwa osobne wpisy), "odszkodowanie" (koszty leczenia, opieki, dojazdów, utracone zarobki, \
pogrzeb, nagrobek, art. 446 § 3 pogorszenie sytuacji życiowej), "renta" albo "inne".
  - renta: bieżąca renta miesięczna → is_monthly=true i kwota miesięczna; renta skapitalizowana (zaległa, jednorazowa) → is_monthly=false.
  - amount_claimed: żądanie powoda (po ewentualnym rozszerzeniu powództwa).
  - amount_appropriate: łączna kwota uznana przez sąd za odpowiednią — przed odliczeniem wcześniejszych \
wypłat i przed pomniejszeniem o przyczynienie. Jeśli sąd uwzględnił żądanie w całości i nic wcześniej nie wypłacono, równa zasądzonej.
  - amount_paid_earlier: kwota wypłacona przez ubezpieczyciela przed procesem z tego tytułu.
  - amount_awarded: kwota zasądzona tym wyrokiem (po apelacji); 0 jeśli roszczenie oddalono.
  - evidence: krótki dosłowny cytat (do 200 znaków) potwierdzający kwotę.
- Pomiń koszty procesu, opłaty sądowe, koszty zastępstwa i odsetki.
- Kwoty jako liczby w złotych, np. 80000 albo 1250.5.

Przykład. Tekst: "zmienia zaskarżony wyrok w ten sposób, że kwotę 30.000 zł zastępuje kwotą 60.000 zł ... \
odpowiednim zadośćuczynieniem za krzywdę powódki w związku ze śmiercią męża jest kwota 85.000 zł; ubezpieczyciel \
wypłacił 25.000 zł". Wpis: {{"type": "zadośćuczynienie", "amount_claimed": null, "amount_appropriate": 85000, \
"amount_paid_earlier": 25000, "amount_awarded": 60000, "is_monthly": false, "evidence": "odpowiednim zadośćuczynieniem ... jest kwota 85.000 zł"}}

Tekst wyroku:
{text}"""


def _env_value(name: str, env_file: Path = Path(".env")) -> str | None:
    """Read a secret from the environment, falling back to a git-ignored .env file."""
    if os.environ.get(name):
        return os.environ[name]
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == name:
                return value.strip().strip('"').strip("'")
    return None


def parse_response(content: str) -> Extraction:
    """Parse the model's reply, tolerating code fences or text around the JSON object."""
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end < start:
        raise ValueError("no JSON object in response")
    return Extraction.model_validate(json.loads(content[start : end + 1]))


class Extractor:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        provider: str = DEFAULT_PROVIDER,
        client=None,
        delay: float = 3.0,
        max_retries: int = 5,
    ):
        self.model = model
        self.backend = "anthropic" if model.startswith("claude-") else "hf"
        if client is None:
            client = self._default_client(provider, max_retries)
        self.client = client
        self.delay = delay
        self.max_retries = max_retries
        self._last_request = 0.0

    def _default_client(self, provider: str, max_retries: int):
        if self.backend == "anthropic":
            import anthropic

            return anthropic.Anthropic(api_key=_env_value("ANTHROPIC_API_KEY"), max_retries=max_retries, timeout=300)
        from huggingface_hub import InferenceClient

        return InferenceClient(provider=provider, timeout=180)

    def _call_anthropic(self, prompt: str) -> str:
        # The SDK retries 429/5xx itself; 4xx errors propagate.
        response = self.client.messages.parse(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
            output_format=Extraction,
        )
        return next((b.text for b in response.content if b.type == "text"), "")

    def _call_hf(self, prompt: str) -> str:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat_completion(
                    messages=messages,
                    model=self.model,
                    max_tokens=6000,
                    temperature=0.0,
                    # Constrained decoding: without it Bielik often mismatches brackets.
                    response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
                )
                return response.choices[0].message.content or ""
            except Exception as exc:  # network errors, 429 and 5xx all surface as different types
                status = getattr(getattr(exc, "response", None), "status_code", None)
                if status is not None and status < 500 and status != 429:
                    raise
                log.warning("LLM call failed (%s), attempt %d/%d", exc, attempt, self.max_retries)
                time.sleep(min(120, 5 * 2**attempt))
        raise RuntimeError(f"LLM call failed after {self.max_retries} attempts")

    def extract(self, text: str) -> tuple[str, Extraction | None, str | None]:
        """Return (raw response, parsed extraction or None, error message or None)."""
        wait = self.delay - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        prompt = USER_TEMPLATE.format(text=text)
        raw = self._call_anthropic(prompt) if self.backend == "anthropic" else self._call_hf(prompt)
        try:
            return raw, parse_response(raw), None
        except (ValueError, ValidationError) as exc:
            return raw, None, f"{type(exc).__name__}: {exc}"[:2000]
