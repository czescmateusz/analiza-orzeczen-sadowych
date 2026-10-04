// Methodology page: sample funnel, time trend, model coefficients and keywords, from data/model.json.
import { formatPLN } from "./logic.js";

const $ = (s) => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "style") el.style.cssText = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c instanceof Node ? c : String(c));
  return el;
}
function s(tag, attrs = {}) {
  const el = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  return el;
}
const num = (x) => String(Math.round(x)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");
const pct = (x, digits = 0) => `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x * 100).toFixed(digits).replace(".", ",")}%`;
const pval = (p) => (p < 0.001 ? "< 0,001" : p.toFixed(3).replace(".", ","));

// ------------------------------------------------------------------ tooltip (enhances; every value is also in a table)
const tip = () => $("#tooltip");
function showTip(target, lines, evt) {
  const t = tip();
  t.replaceChildren(...lines.map((l, i) => (i === 0 ? h("strong", {}, l) : h("div", {}, l))));
  t.hidden = false;
  const r = target.getBoundingClientRect();
  const x = evt && evt.clientX != null ? evt.clientX : r.left + r.width / 2;
  const y = evt && evt.clientY != null ? evt.clientY : r.top;
  const w = t.offsetWidth;
  t.style.left = `${Math.min(Math.max(8, x + 14), window.innerWidth - w - 8)}px`;
  t.style.top = `${Math.max(8, y - t.offsetHeight - 10)}px`;
}
function hideTip() { tip().hidden = true; }
function hoverable(el, lines) {
  el.setAttribute("tabindex", "0");
  el.addEventListener("pointermove", (e) => showTip(el, lines(), e));
  el.addEventListener("pointerleave", hideTip);
  el.addEventListener("focus", () => showTip(el, lines()));
  el.addEventListener("blur", hideTip);
}

// ------------------------------------------------------------------ sample funnel
function funnel(sample) {
  const rows = [
    ["Orzeczenia w SAOS (wszystkie sądy i rodzaje spraw)", sample.saos_seen],
    ["Orzeczenia w sprawach cywilnych, gospodarczych, pracy i rodzinnych (bez karnych)", sample.civil_kept],
    ["Wzmianka o wypadku drogowym i odszkodowaniu/zadośćuczynieniu", sample.candidates],
    ["Sklasyfikowane jako: wypadek drogowy + szkoda na osobie + sprawa cywilna", sample.relevant],
    ["Orzeczenia z odczytaną kwotą zadośćuczynienia (porównanie w aplikacji)", sample.judgments],
    ["Orzeczenia w modelu statystycznym (kwota wskazana przez sąd lub zasądzona + wypłacona)", sample.judgments_model],
  ].filter(([, v]) => v != null);
  const max = rows[0][1];
  $("#funnel").replaceChildren(h("div", { class: "funnel" }, rows.map(([label, v]) =>
    h("div", { class: "funnel-row" },
      h("div", {}, h("div", { class: "funnel-label" }, label),
        h("div", { class: "funnel-bar", style: `width:${Math.max(0.4, (v / max) * 100)}%` })),
      h("div", { class: "funnel-value" }, num(v))))));
  const lv = sample.court_level || {};
  $("#sample-notes").textContent =
    `Porównanie w aplikacji obejmuje ${num(sample.claims)} roszczeń o zadośćuczynienie z ${num(sample.judgments)} orzeczeń ` +
    `z lat ${sample.years[0]}–${sample.years[1]} (poszkodowani: ${num(sample.by_role.p)}, osoby bliskie zmarłego: ${num(sample.by_role.b)}). ` +
    `Model statystyczny: ${num(sample.claims_model)} roszczeń z ${num(sample.judgments_model)} orzeczeń. ` +
    `Orzeczenia według sądu: rejonowe ${num(lv.SR || 0)}, okręgowe ${num(lv.SO || 0)}, apelacyjne ${num(lv.SA || 0)}; ` +
    `${num(sample.instance2)} to orzeczenia II instancji.`;
}

// ------------------------------------------------------------------ trend (small multiples, shared y-scale)
function trendChart(title, years, yMax) {
  const W = 420, H = 260, L = 52, R = 74, T = 16, B = 30;
  const x0 = years[0].year, x1 = years[years.length - 1].year;
  const x = (yr) => L + ((yr - x0) / Math.max(1, x1 - x0)) * (W - L - R);
  const y = (v) => T + (1 - v / yMax) * (H - T - B);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `${title}: mediana kwot sądowych i wypłat ubezpieczycieli w latach ${x0}–${x1}` });
  // grid + y ticks
  const step = yMax > 150000 ? 50000 : 25000;
  for (let v = 0; v <= yMax; v += step) {
    svg.append(s("line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "var(--grid)", "stroke-width": 1 }));
    const t = s("text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", "font-size": 11, fill: "var(--muted)" });
    t.textContent = v === 0 ? "0" : `${v / 1000} tys.`;
    svg.append(t);
  }
  for (const d of years) {
    if (d.year % 2 === 0 || years.length < 8) {
      const t = s("text", { x: x(d.year), y: H - 10, "text-anchor": "middle", "font-size": 11, fill: "var(--muted)" });
      t.textContent = d.year; svg.append(t);
    }
  }
  // IQR band (a wash), then lines
  const band = years.map((d) => `${x(d.year)},${y(d.q3)}`).concat([...years].reverse().map((d) => `${x(d.year)},${y(d.q1)}`));
  svg.append(s("polygon", { points: band.join(" "), fill: "var(--c-up)", "fill-opacity": 0.12 }));
  const line = (key, color) => {
    const pts = years.filter((d) => d[key] != null);
    if (pts.length < 2) return;
    svg.append(s("polyline", { points: pts.map((d) => `${x(d.year)},${y(d[key])}`).join(" "), fill: "none", stroke: color,
                               "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }));
    for (const d of pts) svg.append(s("circle", { cx: x(d.year), cy: y(d[key]), r: 4, fill: color, stroke: "var(--surface)", "stroke-width": 2 }));
    const last = pts[pts.length - 1];
    const t = s("text", { x: x(last.year) + 8, y: y(last[key]) + 4, "font-size": 12, fill: "var(--text)" });
    t.textContent = `${Math.round(last[key] / 1000)} tys. zł`;
    svg.append(t);
  };
  line("median", "var(--c-up)");
  line("paid_median", "var(--c-down)");
  // crosshair + per-year hit areas (hover and keyboard)
  const cross = s("line", { y1: T, y2: H - B, stroke: "var(--muted)", "stroke-width": 1, visibility: "hidden" });
  svg.append(cross);
  const colW = (W - L - R) / Math.max(1, years.length - 1);
  for (const d of years) {
    const r = s("rect", { x: x(d.year) - colW / 2, y: T, width: colW, height: H - T - B, class: "hit" });
    r.addEventListener("pointerenter", () => { cross.setAttribute("x1", x(d.year)); cross.setAttribute("x2", x(d.year)); cross.setAttribute("visibility", "visible"); });
    r.addEventListener("pointerleave", () => cross.setAttribute("visibility", "hidden"));
    hoverable(r, () => [`${formatPLN(d.median)} – mediana kwot sądowych`, `${d.year}, ${d.n} roszczeń`,
      `Q1–Q3: ${formatPLN(d.q1)} – ${formatPLN(d.q3)}`,
      d.paid_median != null ? `Mediana wypłat ubezpieczycieli: ${formatPLN(d.paid_median)} (${d.paid_n})` : "Wypłaty ubezpieczycieli: za mało danych",
      d.adjusted_index != null ? `Indeks skorygowany o strukturę spraw: ${d.adjusted_index.toFixed(2).replace(".", ",")}` : ""].filter(Boolean));
    svg.append(r);
  }
  return h("div", { class: "chart" }, h("h3", {}, title),
    h("p", { class: "legend-row" },
      h("span", { class: "key" }, h("span", { class: "swatch line", style: "background:var(--c-up)" }), "mediana kwot sądowych"),
      h("span", { class: "key" }, h("span", { class: "swatch band" }), "Q1–Q3 kwot sądowych"),
      h("span", { class: "key" }, h("span", { class: "swatch line", style: "background:var(--c-down)" }), "mediana wypłat ubezpieczycieli")),
    svg);
}

// Trend values in constant prices: within one year every amount has the same factor, so the
// median and quartiles scale exactly.
function trendInPrices(years, factors) {
  return years.map((d) => {
    const f = factors[String(d.year)] ?? 1;
    return { ...d, median: d.median * f, q1: d.q1 * f, q3: d.q3 * f, paid_median: d.paid_median != null ? d.paid_median * f : null };
  });
}

function trend(raw, inflation, real) {
  const model = real ? { ...raw, trend: { p: trendInPrices(raw.trend.p, inflation.factors), b: trendInPrices(raw.trend.b, inflation.factors) } } : raw;
  const roles = [["p", "Poszkodowani w wypadku"], ["b", "Osoby bliskie zmarłego"]];
  const yMax = Math.ceil(Math.max(...roles.flatMap(([r]) => model.trend[r].map((d) => d.q3))) / 50000) * 50000;
  $("#trend-charts").replaceChildren(...roles.map(([r, title]) => trendChart(title, model.trend[r], yMax)));
  const idx = (r, yr) => (model.trend[r].find((d) => d.year === yr) || {}).adjusted_index;
  const p = model.trend.p, last = p[p.length - 1];
  $("#trend-notes").textContent =
    `Mediana nominalna zależy też od tego, jakie sprawy trafiły do SAOS w danym roku (np. w pierwszych latach przeważają orzeczenia sądów apelacyjnych). ` +
    `Dlatego model podaje też indeks skorygowany o strukturę spraw (obrażenia, uszczerbek, instancja itd.; 2015 = 1,00): ` +
    `dla poszkodowanych ${idx("p", 2019) != null ? idx("p", 2019).toFixed(2).replace(".", ",") : "–"} w 2019 r. i ` +
    `${last.adjusted_index != null ? last.adjusted_index.toFixed(2).replace(".", ",") : "–"} w ${last.year} r. ` +
    `Ostatnie lata opierają się na mniejszej liczbie orzeczeń, więc są mniej pewne.`;
  const rows = roles.flatMap(([r, title]) => model.trend[r].map((d) =>
    h("tr", {}, h("td", {}, title), h("td", { class: "num" }, d.year), h("td", { class: "num" }, d.n),
      h("td", { class: "num" }, formatPLN(d.median)), h("td", { class: "num" }, `${formatPLN(d.q1)} – ${formatPLN(d.q3)}`),
      h("td", { class: "num" }, d.paid_median != null ? formatPLN(d.paid_median) : "–"),
      h("td", { class: "num" }, d.adjusted_index != null ? d.adjusted_index.toFixed(2).replace(".", ",") : "–"))));
  $("#trend-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ["Grupa", "Rok", "Roszczeń", "Mediana", "Q1–Q3", "Mediana wypłat ubezp.", "Indeks skoryg."].map((t, i) =>
      h("th", { class: i ? "num" : null }, t)))), h("tbody", {}, rows)));
}

// ------------------------------------------------------------------ forest plot (HTML rows + per-row SVG marks)
const RATIO_TICKS = [0.25, 0.5, 0.6, 0.7, 0.8, 0.9, 1, 1.1, 1.2, 1.3, 1.5, 1.75, 2, 2.5, 3, 4];
// Ticks inside the domain, at least 18% of the width apart; 0 ("no effect") always kept.
function ticks(lo, hi, X) {
  const inside = RATIO_TICKS.filter((t) => t >= lo && t <= hi);
  const kept = [1];
  for (const t of inside) if (kept.every((k) => Math.abs(X(k) - X(t)) >= 18)) kept.push(t);
  return kept.sort((a, b) => a - b);
}

function forest(container, items, { label, extra, groups = true }) {
  // Domain always includes "no effect" (ratio 1), on a log scale so −50% and +100% sit equally far from 0.
  const lo = Math.max(0.2, Math.min(0.95, ...items.map((d) => 1 + d.lo)) * 0.95);
  const hi = Math.min(5, Math.max(1.05, ...items.map((d) => 1 + d.hi)) * 1.05);
  const X = (ratio) => ((Math.log(Math.min(Math.max(ratio, lo), hi)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo))) * 100;
  const color = (d) => (d.p >= 0.05 ? "var(--c-ns)" : d.effect > 0 ? "var(--c-up)" : "var(--c-down)");
  const axis = h("div", { class: "forest-row forest-axis" }, h("div", {}),
    h("div", { class: "forest-plot" }, ticks(lo, hi, X).map((t) =>
      h("span", { class: "tick", style: `left:${X(t)}%` }, t === 1 ? "0" : pct(t - 1)))), h("div", {}));
  const rows = [axis];
  let group = null;
  for (const d of items) {
    if (groups && d.group !== group) { group = d.group; rows.push(h("div", { class: "forest-group" }, group)); }
    const svg = s("svg", { viewBox: "0 0 100 20", preserveAspectRatio: "none", "aria-hidden": "true" });
    svg.append(s("line", { x1: X(1), x2: X(1), y1: 0, y2: 20, stroke: "var(--muted)", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
    svg.append(s("line", { x1: X(1 + d.lo), x2: X(1 + d.hi), y1: 10, y2: 10, stroke: color(d), "stroke-width": 2, "vector-effect": "non-scaling-stroke", "stroke-linecap": "round" }));
    const dot = h("span", { class: "forest-dot", style: `left:${X(1 + d.effect)}%;background:${color(d)}` });
    const row = h("div", { class: "forest-row" },
      h("div", { class: "forest-label" }, label(d)),
      h("div", { class: "forest-plot" }, svg, dot),
      h("div", { class: "forest-value" }, pct(d.effect)));
    hoverable(row, () => [`${pct(d.effect)} – ${label(d)}`, `95% przedział ufności: ${pct(d.lo)} do ${pct(d.hi)}`, `p = ${pval(d.p)}`, extra(d)].filter(Boolean));
    rows.push(row);
  }
  container.replaceChildren(h("div", { class: "forest" }, rows));
}

function models(model) {
  for (const role of ["p", "b"]) {
    const m = model.models[role];
    const items = m.coefs.filter((c) => !c.group.startsWith("Rok"));
    forest($(`#model-${role}`), items, { label: (d) => d.label, extra: (d) => `${num(d.n)} roszczeń z tą cechą` });
    $(`#model-${role}-summary`).textContent =
      `${num(m.n)} roszczeń z ${num(m.judgments)} orzeczeń; R² = ${m.r2.toFixed(2).replace(".", ",")} ` +
      `(model wyjaśnia ${Math.round(m.r2 * 100)}% zmienności logarytmu kwot). Punkt odniesienia dla cech podzielonych na grupy podano w nawiasie.`;
  }
  const rows = ["p", "b"].flatMap((role) => model.models[role].coefs.map((c) =>
    h("tr", {}, h("td", {}, role === "p" ? "Poszkodowani" : "Osoby bliskie"), h("td", {}, c.group), h("td", {}, c.label),
      h("td", { class: "num" }, num(c.n)), h("td", { class: "num" }, pct(c.effect)),
      h("td", { class: "num" }, `${pct(c.lo)} do ${pct(c.hi)}`), h("td", { class: "num" }, pval(c.p)))));
  $("#model-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ["Model", "Grupa", "Cecha", "Roszczeń", "Wpływ na kwotę", "95% CI", "p"].map((t, i) => h("th", { class: i > 2 ? "num" : null }, t)))),
    h("tbody", {}, rows)));
}

function keywords(model) {
  const k = model.keywords;
  $("#kw-tested").textContent = num(k.tested);
  $("#kw-significant").textContent = `${num(k.significant)} cech`;
  const opts = { groups: false, label: (d) => d.phrase,
                 extra: (d) => `występuje w ${Math.round(d.share * 100)}% orzeczeń; q (FDR) = ${pval(d.q)}; rdzenie: ${d.stems}` };
  const NAMES = { 1: "pojedynczych słów", 2: "fraz 2-wyrazowych", 3: "fraz 3-wyrazowych", 4: "fraz 4-wyrazowych" };
  const show = (n) => {
    const part = k.by_n[n];
    $("#kw-n-summary").textContent = `Wśród ${num(part.tested)} ${NAMES[n]} istotnych jest ${num(part.significant)}; poniżej po 15 o największym wpływie.`;
    forest($("#kw-up"), part.up.slice(0, 15), opts);
    forest($("#kw-down"), part.down.slice(0, 15), opts);
  };
  for (const r of document.querySelectorAll("input[name=ngram]")) r.addEventListener("change", () => show(r.value));
  show("1");
  const rows = Object.entries(k.by_n).flatMap(([n, part]) => [...part.up, ...part.down].map((d) =>
    h("tr", {}, h("td", { class: "num" }, n), h("td", {}, d.phrase), h("td", {}, d.stems),
      h("td", { class: "num" }, `${Math.round(d.share * 100)}%`), h("td", { class: "num" }, pct(d.effect)),
      h("td", { class: "num" }, `${pct(d.lo)} do ${pct(d.hi)}`), h("td", { class: "num" }, pval(d.q)))));
  $("#kw-table").replaceChildren(h("table", {},
    h("thead", {}, h("tr", {}, ["Słów", "Fraza (najczęstsza forma)", "Rdzenie", "Orzeczeń", "Wpływ na kwotę", "95% CI", "q (FDR)"].map((t, i) =>
      h("th", { class: i === 0 || i > 2 ? "num" : null }, t)))),
    h("tbody", {}, rows)));
}

async function main() {
  let model;
  try {
    model = await fetch("data/model.json").then((r) => { if (!r.ok) throw new Error(); return r.json(); });
  } catch {
    $("main").prepend(h("p", { class: "beta" }, "Brak pliku data/model.json – uruchom `orzeczenia model`."));
    return;
  }
  funnel(model.sample);
  let inflation = { base: null, factors: {} };
  try { inflation = (await fetch("data/labels.json").then((r) => r.json())).inflation || inflation; } catch { /* nominal only */ }
  for (const el of document.querySelectorAll(".base-year")) el.textContent = inflation.base;
  trend(model, inflation, false);
  for (const r of document.querySelectorAll("input[name=prices]")) r.addEventListener("change", () => trend(model, inflation, r.value === "real"));
  models(model);
  keywords(model);
  $("#versions").textContent = `parser ${model.versions.parser}, fragmenty uzasadnień ${model.versions.reasons}`;
}

main();
