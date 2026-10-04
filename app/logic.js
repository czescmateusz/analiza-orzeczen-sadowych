// Matching and statistics for the app. Pure functions, no DOM: tested in tests/test_app_logic.py.
//
// Case record (from `orzeczenia export`, short keys keep cases.json small):
//   j judgment id, r "p" injured / "b" relative, rel relation to the deceased, c injury
//   categories, u % uszczerbku, age, pc % contributory negligence, f factors stated by the
//   court, t the court's total (odpowiednia suma), how "a" stated / "s" awarded+paid /
//   "w" awarded only, pd insurer's earlier payment, aw awarded, y year, i instance.

export const MIN_CASES = 15;   // fewer similar cases than this: widen the search
export const MAX_CASES = 80;   // the most similar cases shown and used for statistics

// Lower-case, no Polish diacritics: "Złamana Noga" -> "zlamana noga".
export function fold(text) {
  return text.toLowerCase().replace(/ł/g, "l").normalize("NFD").replace(/[̀-ͯ]/g, "");
}

// Categories whose synonyms match the typed text (each typed word may match a synonym stem).
export function searchCategories(query, categories) {
  const words = fold(query).split(/[^a-z0-9]+/).filter((w) => w.length >= 3);
  if (!words.length) return [];
  const scored = categories.map((cat) => {
    const syns = cat.synonyms.map(fold).concat([fold(cat.label)]);
    let score = 0;
    for (const w of words) {
      if (syns.some((s) => s.startsWith(w) || w.startsWith(s) || (s.includes(" ") && s.includes(w)))) score++;
    }
    return { id: cat.id, score };
  });
  return scored.filter((s) => s.score > 0).sort((a, b) => b.score - a.score).map((s) => s.id);
}

// Similarity of a case to the user's situation, 0..1.
export function similarity(c, q) {
  let score = 0, weight = 0;
  if (q.role === "p" && q.categories.length) {
    const shared = c.c.filter((x) => q.categories.includes(x)).length;
    const union = new Set([...c.c, ...q.categories]).size;
    score += 3 * (union ? shared / union : 0); weight += 3;
  }
  if (q.role === "p" && q.uszczerbek != null) {
    weight += 2;
    if (c.u != null) score += 2 * Math.max(0, 1 - Math.abs(c.u - q.uszczerbek) / Math.max(5, q.uszczerbek * 0.6));
  }
  if (q.age != null && c.age != null) {
    weight += 1; score += Math.max(0, 1 - Math.abs(c.age - q.age) / 25);
  }
  if (q.factors.length) {
    const present = q.factors.filter((f) => c.f.includes(f)).length;
    score += 2 * present / q.factors.length; weight += 2;
  }
  return weight ? score / weight : 0;
}

// The cases most similar to the user's situation, with notes on how the search was widened.
export function findSimilar(cases, q) {
  const notes = [];
  let pool = cases.filter((c) => c.r === q.role && (c.y == null || c.y >= q.since) && c.y <= new Date().getFullYear());
  if (q.role === "b" && q.relation) {
    const same = pool.filter((c) => c.rel === q.relation);
    if (same.length >= MIN_CASES) pool = same;
    else notes.push("Za mało spraw z tą samą relacją do zmarłego – pokazano sprawy wszystkich osób bliskich.");
  }
  if (q.role === "p" && q.categories.length) {
    const shared = pool.filter((c) => c.c.some((x) => q.categories.includes(x)));
    if (shared.length >= MIN_CASES) pool = shared;
    else notes.push("Za mało spraw z tymi samymi obrażeniami – pokazano sprawy o podobnym stopniu uszczerbku i okolicznościach.");
  }
  const ranked = pool
    .map((c) => ({ c, s: similarity(c, q) }))
    .sort((a, b) => b.s - a.s || (b.c.y || 0) - (a.c.y || 0))
    .slice(0, MAX_CASES);
  return { cases: ranked.map((r) => ({ ...r.c, sim: r.s })), notes };
}

export function quantile(sorted, p) {
  if (!sorted.length) return null;
  const pos = (sorted.length - 1) * p, lo = Math.floor(pos), hi = Math.ceil(pos);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (pos - lo);
}

// Statistics on the court totals and insurer payments of the similar cases, and where the offer falls.
export function summarize(similar, offer) {
  const totals = similar.map((c) => c.t).sort((a, b) => a - b);
  const paid = similar.filter((c) => c.pd).map((c) => c.pd).sort((a, b) => a - b);
  const ratios = similar.filter((c) => c.pd && c.how !== "w").map((c) => c.t / c.pd).sort((a, b) => a - b);
  const stats = {
    n: totals.length,
    p10: quantile(totals, 0.1), p25: quantile(totals, 0.25), median: quantile(totals, 0.5),
    p75: quantile(totals, 0.75), p90: quantile(totals, 0.9),
    paidMedian: quantile(paid, 0.5), paidN: paid.length,
    ratioMedian: quantile(ratios, 0.5),
    offerShareBelow: null,
  };
  if (offer != null && totals.length) {
    // Share of similar cases where the court's total was higher than the offer.
    stats.offerShareBelow = totals.filter((t) => t > offer).length / totals.length;
  }
  return stats;
}

export function verdict(stats, offer) {
  if (offer == null || stats.offerShareBelow == null) return null;
  const pct = Math.round(stats.offerShareBelow * 100);
  if (offer < stats.p25) return { level: "low", pct };
  if (offer < stats.median) return { level: "below", pct };
  if (offer <= stats.p75) return { level: "typical", pct };
  return { level: "high", pct };
}

export function formatPLN(x) {
  if (x == null) return "–";
  // Grouped by hand ("1 234 567 zł") so the output doesn't depend on the browser's locale data.
  return String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, " ") + " zł";
}

// ------------------------------------------------------------------ browser search (przegladarka.html)

// Query words -> folded stems of `len` letters (the export indexes the same way).
export function queryStems(query, len = 6) {
  return [...new Set(fold(query).split(/[^a-z]+/).filter((w) => w.length >= 3).map((w) => w.slice(0, len)))];
}

// Delta-encoded posting list -> row numbers.
export function decodePostings(deltas) {
  const out = new Array(deltas.length);
  let acc = 0;
  for (let i = 0; i < deltas.length; i++) { acc += deltas[i]; out[i] = acc; }
  return out;
}

// Rows matching every stem; a stem shorter than the index length matches all stems it begins.
export function matchRows(stems, indexShards) {
  let result = null;
  for (const stem of stems) {
    const shard = indexShards[stem.slice(0, 2)] || {};
    const rows = new Set();
    for (const [key, deltas] of Object.entries(shard)) {
      if (key === stem || (stem.length < 6 && key.startsWith(stem))) for (const r of decodePostings(deltas)) rows.add(r);
    }
    result = result === null ? rows : new Set([...result].filter((r) => rows.has(r)));
    if (!result.size) break;
  }
  return result || new Set();
}

// Split a sentence into parts, marking every amount equal to `value` (to show where a number came from).
export function markAmounts(text, value) {
  if (value == null) return [{ text, mark: false }];
  const re = /\d{1,3}(?:[ . ]\d{3})+(?:,\d{1,2})?|\d+(?:,\d{1,2})?/g;
  const parts = [];
  let last = 0, m;
  while ((m = re.exec(text))) {
    const v = Number(m[0].replace(/[ . ]/g, "").replace(",", "."));
    if (Math.abs(v - value) < 1) {
      if (m.index > last) parts.push({ text: text.slice(last, m.index), mark: false });
      parts.push({ text: m[0], mark: true });
      last = m.index + m[0].length;
    }
  }
  if (last < text.length) parts.push({ text: text.slice(last), mark: false });
  return parts;
}

// ------------------------------------------------------------------ inflation

// Cases with amounts converted to constant prices: factors[year] multiplies the court's total and
// the insurer's payment (data/labels.json -> inflation.factors, from GUS's CPI). The nominal total
// stays in `tn`.
export function inPrices(cases, factors) {
  return cases.map((c) => {
    const f = factors[String(c.y)] ?? 1;
    return { ...c, tn: c.t, t: c.t * f, pd: c.pd != null ? c.pd * f : null };
  });
}
