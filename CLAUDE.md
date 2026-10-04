# CLAUDE.md — analiza-orzeczen-sadowych

Working rules, the brief and the decisions log: @.claude/instructions.md

## Project goal

Build an app that shows how Polish common courts value injuries and losses from **road
accidents**, so victims can estimate a realistic settlement (zadośćuczynienie,
odszkodowanie, renta) based on earlier judgments.

Main use case: a victim enters their injuries, circumstances and the insurer's offer. The
app shows where that offer falls in the range of totals that courts found appropriate in
similar cases. So the key figures are the court's **appropriate total**
(`amount_appropriate`) and what insurers **paid before court** (`amount_paid_earlier`),
not just the awarded difference.

Pipeline: download judgments → parse → extract structured facts (injuries, amounts) →
store in a database (local SQLite first, then a cost-efficient hosted store) → analyse →
app that gives guidance per injury or loss type.

## Phases

1. **Collect & store** (done, `src/orzeczenia/`): SAOS search → full judgments in SQLite,
   HTML → text, split into sentencja / uzasadnienie, rule-based road-accident filter.
2. **Extract**: structured extraction per judgment (LLM-based, with the rule-based regex
   amounts as a baseline). Needs a hand-labelled validation sample (~100 judgments).
3. **Analyse**: amounts by injury type, % uszczerbku, victim age, court, and year, with
   inflation and average-wage adjustment (courts' amounts drift up over time).
4. **Hosted store**: target Postgres free tier (Neon/Supabase) or Parquet + DuckDB on
   object storage (Cloudflare R2). Keep the schema portable, with no SQLite-only types.
5. **App**: user describes injuries → similar cases plus an award range, with case
   citations (sygnatura, court, date, link to source).

## Gold set & evaluation

- `evaluation/gold/<saos_id>.json`: hand labels from the full text, following
  `evaluation/GUIDELINES.md`. `orzeczenia extract --gold-only` runs a model on exactly
  those judgments; `orzeczenia evaluate [--model M] [--prompt-version V]` scores it.
  Read the `[known]` rows: they ignore cases where the label and the model are both null.
- Batch 1 (2026-10-01): 20 appeal judgments from 2011–2013.
- Batch 2 (2026-10-01): 30 judgments from 2018–2025 (15 district + 15 regional), drawn by
  `scripts/sample_gold_candidates.py` in a fixed pseudo-random order;
  `scripts/gold_view.py <id>` prints a labelling view. 9 of all 50 are out of scope
  (property-only, NNW contract, farm/work accidents, dental malpractice, a lawyer-fee
  dispute, a tree dispute, unfair competition). **rules-v2 lets these through**, so the
  scope filter needs the LLM or better rules.
- Recurring labelling pitfalls: the court says "odpowiednia kwota X" and awards X without
  deducting earlier payments (net or total? recorded as stated, with a note); joint awards
  to two plaintiffs; appeals/retrials that only dismiss the remainder; nawiązka from the
  criminal case counted towards the total.
- Prompt v3 (sentence selector fixed and `analysis` field added) vs v2 on the same 14
  batch-1 judgments: age 0/5 → 3/5, claimant count 9 → 12/14, zadośćuczynienie
  appropriate 9 → 10/15, awarded 8/15 in both. The prompt is not the bottleneck for amounts.
- Bielik-11B-v3.0, prompt v2, on batch 1 ([known] accuracy): zadośćuczynienie appropriate
  10/21 (48%), awarded 10/22 (45%), claimed 12/18; odszkodowanie ~30%; renta lump sums
  0/5; % uszczerbku 7/8; przyczynienie 4/4; claimant count 12/20; injuries recall 61%;
  age 0/6. is_road_accident 19/20 (it correctly rejected the unfair-competition case
  that rules-v2 passed).
- Snippet loss (snippets.py, my bug): ages never survive selection (0/7). About 1 in 6 gold
  appropriate/awarded amounts are missing from the snippet. Fix before re-testing.

## Bielik pilot (2026-10-01, prompt v2, 20 judgments; 4 checked by hand)

- Constrained decoding (`response_format` `json_schema`) is required. Without it, 2 of 3
  replies had mismatched brackets; with it, 20/20 parsed. ~13 s per judgment sequentially.
- Injuries and % permanent damage: good (4/4).
- Amount roles: unreliable on appeal judgments (it misses amounts changed by the appeal,
  and confuses claimed, appropriate and awarded) and on cases with several plaintiffs
  (claims get merged or misattributed). Some `amount_paid_earlier` values may be invented.
- Next: a gold-labelled sample to measure this properly, then fix the prompt and
  per-plaintiff structure, or move amounts to rules or a stronger model.

## Extraction approach (phase 2; measured on a 150-judgment sample)

**Chosen:** `orzeczenia extract` uses `snippets.select()`, then Bielik-11B-v3.0-Instruct
via `huggingface_hub.InferenceClient(provider="publicai")`, and validates the output with
the pydantic models in `extract.py` into the `extractions` table. That table is keyed by
(judgment, model, prompt_version), so models and prompts can be compared side by side.
Bump `PROMPT_VERSION` whenever the prompt changes. Auth: `HF_TOKEN`, or `hf auth login`,
with the "Make calls to Inference Providers" permission. Bielik v2.6 and Minitron-7B
have no serverless provider on HF.

- Judgments are long: the reasoning has a median of ~25k characters (p90 ~49k), roughly
  8–10k tokens of Polish text. The corpus is ~15–25k candidates. Sending whole judgments
  to an LLM costs ~$60–125 with Haiku 4.5 in batch mode, ~2× that with Sonnet 5.5.
- Windows of ±300 characters around amounts or injury words keep ~64% of the text, so
  they barely help. **Selecting sentences** (an amount next to a compensation term, plus
  short injury sentences) keeps ~23% (~5.6k characters), cutting input cost ~4×.
  Output tokens don't shrink, so the total saving is ~2.5×.
- Snippet cost (~2.4k input + ~0.4k output tokens per judgment, ~20k judgments):
  Haiku 4.5 batch ≈ $45; Bielik-11B-v3.0 on CloudFerro Sherlock ($0.67/M in and out,
  32K context, OpenAI-compatible API at `https://api-sherlock.cloudferro.com/openai/v1/`)
  ≈ $40. These prices were checked on 2026-10-01 from third-party listings; verify
  before spending.
- Bielik (SpeakLeash/Cyfronet, Apache 2.0): v3.0 Instruct 11B and Minitron-7B, with the
  first Polish-specific tokenizer. Self-hosting on a rented GPU (vLLM, e.g. RunPod) is
  probably the cheapest bulk route (~$5–15, a rough estimate). The owner's PC (RX 470
  4 GB, i7-5820K, 32 GB RAM) can't run 11B at useful speed: fine for a 10–20 document
  prototype on CPU, not for the corpus.
- Pick the model by a bake-off on the labelled sample (rules vs. Bielik vs. Haiku), not
  by price alone. Keep the extractor provider-agnostic.
- Planned cascade: rules extract what they can for free → sentence selection → small
  LLM on the snippets only for amount roles and injuries → validate on a hand-labelled
  sample. Optionally, distil into a local model (HerBERT/Bielik) later.

## Rules parser (primary extractor, no LLM; `parse.py`, version p2)

`orzeczenia parse [--gold-only]` stores results in `extractions` as model `rules-parser`,
version `PARSER_VERSION`, so `orzeczenia evaluate --model rules-parser --prompt-version p2`
scores it like an LLM. The `raw` column holds the per-claim source passages (`ClaimEvidence`
JSON: operative clause, claim, appropriate-total and payment sentences).
`orzeczenia parse --show ID [ID ...]` prints them. Bump `PARSER_VERSION` when behaviour changes.

How it works:
- Every amount gets the nearest heading (zadośćuczynienie / odszkodowanie / renta / costs):
  a heading right after the amount, else the closest one before it. Costs and interest
  bases ("od kwoty X") are dropped. Amounts may have the words in brackets before "zł".
- Awarded: operative "zasądza" clauses. A total without a heading is split using reasoning
  amounts with distinct headings that add up to it (4.720 = 4.000 zadość. + 720 odszk.).
- Appeals: the first-instance awards recited in the reasoning, plus the operative changes
  ("obniża/podwyższa do", "zastępuje kwotą", "dalszą kwotę").
- Claimed, appropriate and paid: the nearest labelled amount after the keyword.

Gold results (50 judgments, 2026-10-02), p1 → p2:
- zadośćuczynienie awarded: 4/23 → 21/44. Appropriate: 3/23 → 16/44.
  Claimed: 13/21 → 16/39. Paid: 9/16 → 17/31.
- Claimant found: 24/58 → 45/58. is_road_accident: 40/50 → 45/50. victim_died: 43/45.
- Odszkodowanie and renta are still weak (awarded 6/24; renta 2/5).

Known gaps:
- Several plaintiffs with separate clauses ("na rzecz każdej z powódek").
- Appeals whose first-instance recital isn't in the opening sentences.
- Odszkodowanie claimed and paid amounts.

## Quotable passages and valuation factors (`reasons.py`, version r3; no LLM)

`orzeczenia reasons` reads relevant judgments and stores sentences in the `passages` table.
It reads only the part where the court speaks for itself: it starts at the findings of fact
("ustalił następujący stan faktyczny" / "zważył") and skips the parties' arguments, appeal
grounds, citations and general legal standards. Each stored sentence has:
- `factors`: lasting effects, long treatment, pain, psychological harm, lost activities,
  dependence on others, young age, scarring, family life, bond with the deceased, sudden
  death. Negated mentions are ignored ("nie wymaga pomocy").
- `insurer_view = 'too_low'`: the court finds the insurer's earlier payment too low.

Measured by hand on random samples of fresh judgments (2026-10-04): "too low" verdicts
~80% right (24/30), factor tags ~78% (31/40). An "adequate" verdict was dropped (2–4 of 20
right): courts rarely state it, and most hits were parties' positions.

- `orzeczenia factors` compares, for first-instance judgments, how often the court states
  each factor where it raised the award vs. where it awarded nothing beyond the insurer's
  payment. Outcomes come from the rules parser.
- Outcome labels: "raised" when the parser finds a zadośćuczynienie award after an
  insurer payment. "Nothing more" is read straight from the sentencja (`fully_dismissed`):
  the parser's zero awards were wrong in 11 of 12 sampled cases, the sentencja rule in 2 of 20.
- r2 (2026-10-04) narrows r1: "rezygnacja" counts only with an activity, "nie może
  prowadzić" only with a vehicle or business, and "ani nadmierne, ani zaniżone" is not a
  "too low" verdict. r2 was built by re-tagging r1's stored sentences, which is equivalent
  to a full re-run because r2 only narrows the rules.
- Results, r2 (first instance, 15,276 judgments with passages):
  - Injured person, 2,739 raised vs 56 nothing more. Factors stated more often where the
    award was raised: lost activities +28pp, long treatment +21, pain +21, dependence on
    others +21, psychological harm +16, lasting effects +14.
  - Relatives, 924 raised vs 22 nothing more: sudden death +19pp, psychological harm +16,
    family life +16. With only 22 cases this is indicative only.
  - Where the court raised the award, its total is ~4× the insurer's payment (relatives
    ~3.3×), whichever factors are present. Factors predict *whether* a court raises the
    award, not by how much relative to the payment.
  - Caveats: the "nothing more" group is small; first-instance only; the parser's amounts
    are ~50% exact. These are associations, not causes.
- `orzeczenia quotes [--factor F]` prints citable bundles for the app: the "too low"
  verdict, up to two factor sentences, sygnatura, court, date and link.

## App prototype (`app/`, `export.py`, `categories.py`; 2026-10-04)

A static site with no backend: user input never leaves the browser. Run it locally with
`.venv/Scripts/python -m http.server 8765 -d app`; see `app/README.md`.
- `orzeczenia export` writes `app/data/` (~18 MB). It contains one record per
  zadośćuczynienie claim (13,908 claims from 11,607 judgments), citations, quote shards and
  labels. The court total is the appropriate sum if stated, else awarded + paid, else
  awarded only (flag `how`).
- Injury categories (`categories.py`, 14 categories with search synonyms) are read from
  the court's description of the injuries. Measured on the gold set: precision 69%, recall
  79%; some false positives are injuries the gold labels omit. Several injured plaintiffs
  in one judgment share the union of categories.
- Relation to the deceased comes from the parser's `relation`. With a single relative it
  falls back to "śmierć syna/żony/…" in the text: known for 44% of relatives' claims.
- Matching (`app/logic.js`): role, year from, relation; injury categories must overlap.
  Ranking uses category Jaccard similarity, % uszczerbku, age and factors; the top 80 cases
  are shown, and the search widens below 15. Tested in `tests/test_app_logic.py` via QuickJS.
- Reasons r3 narrows r2 further: a party's argument anywhere in the sentence ("wskazując",
  "domagał się") and "nie była (rażąco) zaniżona" are no longer "too low" verdicts.
- Known gaps:
  - amounts are nominal (no inflation adjustment; the default is 2015+);
  - the "paralysis" category also catches mild niedowład;
  - amounts are ~50% exact on gold, so the app carries a beta warning.

## Statistical model and time trend (`model.py`; 2026-10-04)

`orzeczenia model` writes `app/data/model.json`. It needs the `analysis` extra:
`pip install -e ".[analysis]"`. The output is shown on `app/metodologia.html`, together with
the methodology note: sample, how the data was read, definitions, assumptions and limitations.
- Structured models: OLS of log(court total), one row per claim. Rows are claims whose
  total was stated by the court or computed as awarded + paid (9,692 claims from 8,615
  judgments). Standard errors are clustered by judgment. There are separate models for
  injured persons and for relatives after a death, with year fixed effects (base 2015).
  The reference levels are 1–5% uszczerbku, age 31–50 and the death of a parent.
  Off-role factors are dropped.
- R²: injured persons 0.52, relatives 0.16. Uszczerbek bands dominate (41%+ ≈ +215% vs 1–5%).
- Second instance (+94%) and contributory negligence are selection or definition effects.
  "Silna więź" among relatives (in 85% of cases) is not informative. These are explained
  on the page.
- Keywords and phrases (n = 1–4). Tokens: 3+ letters; stems are 7-letter prefixes;
  punctuation breaks phrases.
  - Features: n-grams of stems from the court's part of the reasoning, in 2% (n = 1, 2)
    or 1% (n = 3, 4) to 60% of judgments, capped at 3,000 / 3,000 / 2,000 / 1,500.
  - Method: FWL residualisation on an SVD basis of the judgment-level covariates
    (including log vocabulary size), HC1 errors, and one Benjamini–Hochberg FDR over all
    features.
  - The surface form shown is the most common one in the texts.
  - Implemented with `Corpus` (integer token ids) and n-gram keys packed into uint64
    (16 bits per compact stem id).
  - Without the vocabulary-size control, 2,302 of 4,000 unigrams were significant (a
    long-text artefact).
  - Stems that restate amounts, the court level or judges' titles (SSR/SSO/SSA) are
    excluded.
- Trend: medians and quartiles per year (years with ≥30 claims) plus a
  composition-adjusted index from the year effects. Injured persons: about 0.75–0.8 in
  2017–2020, ≈1.1–1.2 in 2024–2025 (2015 = 1).
- Charts use the dataviz palette validator's passing pair (`--c-up` / `--c-down`, light and
  dark). The validator was run through QuickJS.

## Parser browser (`app/przegladarka.html`, `export.export_browser`; 2026-10-04)

A searchable view of everything the parser read, inside the same static app (no server).
`orzeczenia export` also writes:
- `data/browse.json`: one row per relevant judgment (17,883). Fields: sygnatura, court,
  date, instance, the parser's road-accident decision, roles, max court total, categories,
  "too low" flag, number of claims. Filters run on this file.
- `data/parser/NNN.json`: 128 shards (~67 MB in total), loaded when a judgment is opened.
  Each judgment has its claims with every amount and its source sentences
  (`ClaimEvidence`), plus its passages.
- `data/index/xx.json`: an inverted index over the source sentences. Keys are folded
  6-letter stems; values are delta-encoded row numbers; shards are keyed by the stem's
  first two letters (~6.5 MB in total).
  - A query loads only the shards it needs.
  - Stems found in more than 40% of judgments are not indexed (listed in
    `browse-meta.json`).
- Filters and the open judgment (`#j=ID`) live in the URL fragment, so searches can be
  shared and nothing reaches the server.
- In the detail view, the parsed amount is highlighted in its source sentence
  (`markAmounts`). Amounts the parser computed (split totals, awarded + paid) are labelled.

## App changes, 2026-10-04 (evening)

- **Dates** (`dates.py`): obvious typos in judgment dates are corrected at export and in the
  model. A date is obviously wrong when its year is in the future or before the case year in
  the sygnatura. The fix is a single-digit change or adjacent swap, within case year … +8,
  closest to case year + 1. Three judgments were fixed (3013 → 2013, 2010 → 2020,
  2012 → 2021). The browser marks them with `*` and shows SAOS's original date.
- **Inflation** (`inflation.py`): GUS average annual CPI for 2004–2025 (2025 = 103.6, from
  the announcement of 15.01.2026); amounts are converted to the prices of `BASE_YEAR`
  (2025) from the judgment year (art. 363 § 2 k.c.).
  - The factors are in `labels.json`.
  - The comparison converts by default (`#ceny=nom` for nominal); the methodology trend has
    a nominal / real toggle.
  - The model is unaffected, because it has year fixed effects.
  - Update `CPI` each January.
- **Combined situation**: the form takes both roles (`rola=pb`). There is one comparison
  per claim (art. 445 and art. 446 § 4) plus a summary of the sums.
- **Year filter**: free input, 2004–2026.
- **Browser**: sorting by date or court total (rows without a value go last), kept in the
  URL (`sort=total-desc`).
- **Links** go to SAOS's readable page (`export.saos_url`); the record's `source.judgmentUrl`
  returns raw XML.
- **Texts for proofreading**: `scripts/teksty.py export|apply` and
  `teksty/teksty_do_korekty.csv` (344 texts from HTML, JS and the Python labels; see
  `teksty/README.md`). `apply` refuses changes to `${…}` placeholders.
- **Local server**: `scripts/serve.py` sends `Cache-Control: no-cache`; plain
  `http.server` let Chrome keep a stale `logic.js`.
- Accuracy metrics were removed from the methodology page's section 2 at the owner's
  request (they remain in this file).

## Bulk corpus (`orzeczenia dump`, `bulk.py`, started 2026-10-02)

All SAOS judgments with civil claims go into `data/saos_civil.db` as zlib-compressed JSON
records (`bulk.load_record`), via `/api/dump/judgments`, one judgment-date month at a time.
`dump_progress` makes it resumable: re-run the same command after a stop.
- What is kept: common courts minus criminal, penitentiary and misdemeanour divisions
  (division type via `/api/ccDivisions/{id}`, cached in `divisions`); Supreme Court C*/P*/U* cases.
- What is skipped: the Constitutional Tribunal and KIO.
- Progress is logged to `data/dump.log`.
- Finished 2026-10-02: 542,707 judgments seen and 399,501 kept (3.8 GB); mostly 2013–2019.
  To update it, delete the recent rows from `dump_progress` and re-run.
- `orzeczenia import-bulk` copies civil, commercial and other SENTENCE/REASONS records
  that mention a road accident and compensation into `data/orzeczenia.db`, skipping ids
  already present. Run `classify` and `parse` afterwards. Run on 2026-10-03, it took the
  main database to 39,102 judgments; 18,410 are classified road + injury + civil.
- Parser p2 run over all of them (35 min). Of 17,883 classified relevant, the parser
  confirms 15,613 as road accidents, with 15,264 claimants.
  - zadośćuczynienie, injured person: 8,621 claims, median awarded 30k zł, median
    appropriate total 50k zł (5,778 claims have one).
  - zadośćuczynienie, close relative after a death: 6,106 claims, medians 45k / 70k zł.
  - These are raw p2 output: on the gold set only ~36–48% of zadośćuczynienie amounts are
    exact. Don't publish the medians as figures for the app.

## Data source: SAOS (https://www.saos.org.pl, public API, no key)

- `GET /api/search/judgments?all=...&courtType=COMMON&judgmentTypes=SENTENCE&pageSize=100`
  returns only metadata and a ~900-char snippet. It is slow (~10 s/page).
- `GET /api/judgments/{id}` returns the full record (`textContent` is HTML).
- `GET /api/dump/judgments` is a bulk dump (pageSize ≤ 100, `sinceModificationDate` for sync).
- Quirks: some `judgmentDate` years are OCR-garbled (e.g. `3013-12-04`); SAOS has no
  administrative courts; coverage of recent years is partial.
- Don't build Polish query strings with curl in Git Bash, which mangles the diacritics.
  Use Python `requests`.
- Be polite: keep ≥0.3 s between requests.

## Domain notes (important for extraction)

- **zadośćuczynienie** (art. 445 k.c. for the victim; art. 446 § 4 for relatives after a
  death) ≠ **odszkodowanie** (material loss, art. 444 / 446 § 3) ≠ **renta** (art. 444 § 2).
  Keep them separate.
- Courts often set the "appropriate sum" (*odpowiednia suma*), e.g. 80 000 zł, then
  deduct what the insurer already paid and award only the difference. **The valuation is
  the total, not the awarded difference.** Extract both.
- Appeal judgments (sygn. "ACa"/"Ca") may change first-instance amounts ("podwyższa do",
  "obniża do"). The final amount is what counts.
- Contributory negligence (*przyczynienie się*, art. 362 k.c.) reduces awards by a
  percentage. Extract it.
- Criminal divisions also award zadośćuczynienie/nawiązka (art. 46 k.k.). They are kept,
  but flagged `is_civil = 0`.
- The old prototype (`notebooks/`, `modules/`) summed every number after "zasądza", which
  mixes up costs, interest and amounts. Do not reuse it as ground truth.

## Code layout & conventions

- Python 3.11, package in `src/orzeczenia/`, virtualenv in `.venv/`.
  Set up with `python -m venv .venv && .venv/Scripts/python -m pip install -e ".[dev]"`.
- CLI (each step resumable/idempotent):
  `orzeczenia collect | fetch [--limit N] | reparse | classify | extract [--limit N] | parse [--gold-only | --show ID] | dump | evaluate | stats`
  (`--db` overrides `data/orzeczenia.db`).
- SAOS has maintenance windows: it serves a "Przerwa techniczna" HTML page with status
  200. The client waits these out (every 5 minutes, for up to 2 hours).
- `judgments.raw_json` keeps the full SAOS record, so parsing changes only need `reparse`,
  never a re-download.
- Tests: `.venv/Scripts/python -m pytest`. Add a test for every parsing or classification rule.
- `data/` is git-ignored. Never commit the database or downloads.
- Code and comments in English. Polish legal terms are kept verbatim.
- On Windows, set `PYTHONIOENCODING=utf-8` when printing Polish text.

## Legacy material

- `resources/compensation/*.zip`: 12 280 SAOS civil judgments (an earlier export) with
  lemmatised text. `resources/supreme-court_chunk*.zip`: Supreme Court judgments.
- `notebooks/`: earlier TF-IDF, random forest, and NMF topic experiments.
