# Project instructions

Standing instructions for working on this project, from the owner (Mateusz).
They are imported into `CLAUDE.md`, so every Claude Code session here loads them.

## Brief

> Analyse Polish court judgments. Download, parse and analyse judgments concerning road
> accidents, and store the results in a database: first locally, later in a cost-efficient
> hosted data store. Use the analysis to show accident victims what settlement they could
> expect, based on previous judgments. The final product should be an app that shows how
> Polish courts value each type of injury or loss.
>
> The app should be available to every victim of a traffic accident. It should help them
> estimate the value of their claim and decide whether the settlement offered by the
> insurance company is fair. To strengthen the victim's position against the insurer, the
> app should also quote passages from similar judgments that support the claim, for
> example where a court found an insurer's payment too low and explained why.

## How to work on this project

- **Cost first.** Prefer free or cheap approaches: rules, local models, small samples.
  Ask before anything that costs money (paid API calls, hosted services) and give a
  measured estimate.
- **Measure before scaling.** Any extraction method gets validated on a hand-labelled
  sample before it runs on the full corpus. Report its accuracy, not impressions.
- **Resumable pipeline.** Every step must be re-runnable without redoing finished work.
  Raw source data is kept so derived data can be rebuilt.
- **Git.** Don't commit or push without asking. Work on a branch, not `main`.
- **Keep the instructions current.** Record decisions and their reasons in `CLAUDE.md`
  (technical) or here (working rules) as they are made.
- **The app's output is guidance, not legal advice.** Always show the cases an estimate
  is based on (sygnatura, court, date, link) and the spread of awards, not a single number.
- **Privacy.** Judgments are anonymised. Never try to de-anonymise parties.

## Decisions log

| Date       | Decision | Why |
|------------|----------|-----|
| 2026-10-01 | Data source: SAOS API (search → full judgment fetch) | Free, no key, still updated; search returns snippets only |
| 2026-10-01 | Local store: SQLite with portable schema | Zero setup; easy move to Postgres/DuckDB later |
| 2026-10-01 | Common-court judgments (wyroki) only; criminal divisions kept but flagged | Civil divisions set the compensation amounts |
| 2026-10-01 | LLM extraction: Bielik-11B-v3.0-Instruct via Hugging Face Inference Providers (provider `publicai`) | Owner's choice. Polish-native open model; Public AI was listed as free of charge (check this still holds before bulk runs) |
| 2026-10-01 | Bielik is no longer free on HF: Public AI charges $0.40/M tokens (in and out), and the HF account needs credits (402 after 15 calls) | Est. full corpus ≈ $30, gold run ≈ $0.10. Needs the owner's OK and HF billing |
| 2026-10-02 | Download ALL SAOS judgments with civil claims, not only road-accident searches: `orzeczenia dump` into `data/saos_civil.db` (common courts except criminal/penitentiary/misdemeanour divisions; Supreme Court civil and labour) | Owner's request; one corpus to filter and parse later |
| 2026-10-02 | Primary extraction is a deterministic, rule-based parser (`orzeczenia.parse`), not an LLM. LLMs stay optional, for comparison only | Owner's request: free, reproducible, explainable |
| 2026-10-03 | The app quotes supporting passages from judgments, each with sygnatura, court, date and SAOS link, quoted verbatim | Owner's request: arm victims against low offers. Judgments are official documents, not protected by copyright (art. 4 pkt 2 of the Polish Copyright Act), and are already anonymised |
| 2026-10-04 | The app is non-commercial: no ads, referral links, paid features or payments | Owner's decision. Free non-commercial hosting tiers (Cloudflare Pages, GitHub Pages, Vercel Hobby) all qualify; keep it that way, or the hosting choice must be revisited |
| 2026-10-04 | App v1: structured form with pre-determined injury categories, a synonym search running in the browser, and a static site; no LLM and no user data sent anywhere. LLM free-text input only later, optional, behind consent | Owner's decision. Costs €0, keeps health data on the device (GDPR art. 9), results are explainable. Free text via Haiku 4.5 would be ≈$0.002/query but needs a backend, consent and a DPA |
| 2026-10-01 | Comparison model: Claude Haiku 4.5 via the Anthropic API; the key goes in the git-ignored `.env` as `ANTHROPIC_API_KEY` | Owner asked for the comparison. The extractor picks the backend from the model name |

## Open questions

- Is Bielik accurate enough? Measure on the hand-labelled sample before the bulk run.
- Scope: personal injury and death only, or also vehicle damage claims?
- Hosted store: Postgres free tier (Neon/Supabase) or Parquet + DuckDB on R2.
- Front end (analysed 2026-10-04): the proposal is a static site on Cloudflare Pages, with
  matching done in the browser so victims' health data never leaves their device. Still
  open: will it take free-text injury descriptions (needs a backend and an LLM)? Will it
  have user accounts?
