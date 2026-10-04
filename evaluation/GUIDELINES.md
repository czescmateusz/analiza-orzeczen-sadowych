# Gold-label guidelines

Each `gold/<saos_id>.json` holds the correct extraction for one judgment, labelled from the
**full text** (not the snippets the LLM sees). The schema is the same as
`orzeczenia.extract.Extraction`, plus `case_number` and `notes`.
`orzeczenia evaluate` compares model extractions against these files.

## Scope (`is_road_accident`)

`true` only when someone was injured or killed in a road accident and claims compensation
for it. It is `false` for property-only claims (car repair, rental car), for unloading
or work accidents covered by OC, and for disputes that aren't about injury at all. For
`false` cases, leave `claimants` empty unless injury awards were decided anyway.
Contract benefits from the victim's own NNW policy (art. 805 k.c., "1% of the insured
sum per 1% uszczerbku") are recorded as type `inne`, not `zadośćuczynienie`.

## Claimants

- One entry per plaintiff (powód/powódka), in the order they appear in the judgment.
- `role`:
  - `poszkodowany`: the plaintiff was injured themself.
  - `osoba_najblizsza`: the plaintiff claims as a relative of a victim who died or was
    injured (art. 446 k.c., or art. 448 in connection with art. 24 k.c.).
  - A plaintiff who was injured **and** lost a relative is `poszkodowany`. Their awards
    for the relative's death are still listed under that same entry.
- `relation`: relation to the victim, e.g. "córka zmarłego". `null` for `poszkodowany`.
- `age_at_accident`: only when stated or computable (accident date and birth year).
  Otherwise `null`.
- `victim_died`: the direct victim of the accident died.
- `injuries`: short Polish phrases from the medical findings, including lasting effects
  (e.g. "zespół stresu pourazowego"). Empty for `osoba_najblizsza`, unless the
  plaintiff's own health effects are described.
- `permanent_damage_percent`: the total uszczerbek the court accepted. If experts give
  per-specialty values that the court adds up, record the sum. If the appeal court
  changed it, record the appeal court's value.
- `contributory_negligence_percent`: the przyczynienie the court finally accepted.
  `null` if none.

## Awards

Record one award per (claimant, type, monthly/lump sum). If several odszkodowanie items
are claimed (medicines, transport, care), record their **total**; the evaluator sums
same-type awards anyway.

- `type`:
  - `zadośćuczynienie`: art. 445, art. 446 § 4, or art. 448 k.c.
  - `odszkodowanie`: art. 444 § 1, or art. 446 § 1 and § 3 (including "znaczne
    pogorszenie sytuacji życiowej").
  - `renta`: art. 444 § 2 or art. 446 § 2. A capitalised rent arrears (renta
    skapitalizowana) is `renta` with `is_monthly: false`. The ongoing rent is
    `is_monthly: true` with the monthly amount.
  - `inne`: anything else that is substantive, not costs.
- Never record costs of proceedings, court fees or interest.
- `amount_claimed`: the plaintiff's final demand in the first instance, after any
  extension of the claim (rozszerzenie powództwa).
- `amount_appropriate`: the total the court (the appeal court, if it reassessed) deems
  appropriate, **before** deducting earlier insurer payments and **before** the
  przyczynienie reduction. `null` if the court never states or implies a total.
- `amount_paid_earlier`: what the insurer paid in the liquidation procedure for this
  claim type. `null` if not stated.
- `amount_awarded`: what this judgment finally orders the defendant to pay for this claim.
  For appeals, it's the first-instance amount as changed by the appeal. If a claim was
  dismissed entirely, use `0`.
- `evidence`: a short quote supporting the key amount (not evaluated).

## Notes

Use `notes` for anything ambiguous: how a number was derived, conflicting values, or
parts of the judgment that are missing.
