// The app's UI: form, results, chart and quotes. Matching and statistics live in logic.js.
import { fold, searchCategories, findSimilar, summarize, verdict, formatPLN, inPrices } from "./logic.js";

const FACTORS_BY_ROLE = {
  p: ["trwale_skutki", "dlugie_leczenie", "bol_cierpienie", "psychika", "utrata_aktywnosci",
      "zaleznosc_od_innych", "oszpecenie", "zycie_rodzinne", "mlody_wiek"],
  b: ["wiez_ze_zmarlym", "nagla_smierc", "psychika", "zycie_rodzinne", "mlody_wiek", "dlugie_leczenie"],
};
const RELATION_LABELS = { rodzic: "śmierć dziecka", malzonek: "śmierć małżonka/partnera", dziecko: "śmierć rodzica",
                          rodzenstwo: "śmierć brata/siostry", inny: "śmierć innej bliskiej osoby" };
const SHOWN = 15;

const $ = (sel) => document.querySelector(sel);
let data = null;
const shardCache = new Map();

// Small DOM helper: h("p", {class: "x"}, "text", child...). Text is always set as text, never HTML.
function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) if (c != null) el.append(c instanceof Node ? c : String(c));
  return el;
}

async function loadData() {
  const get = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(p); return r.json(); });
  const [labels, cases, judgments] = await Promise.all([get("data/labels.json"), get("data/cases.json"), get("data/judgments.json")]);
  const inflation = labels.inflation || { base: null, factors: {} };
  return { labels, cases, judgments, inflation, realCases: inPrices(cases, inflation.factors),
           catLabel: Object.fromEntries(labels.categories.map((c) => [c.id, c.label])) };
}

async function quotesFor(jid) {
  const n = String(jid % data.labels.shards).padStart(2, "0");
  if (!shardCache.has(n)) shardCache.set(n, fetch(`data/quotes/${n}.json`).then((r) => r.json()));
  return (await shardCache.get(n))[String(jid)] || { v: [], f: [] };
}

// ------------------------------------------------------------------ form

// The situations ticked: "p" (injured), "b" (a close person died), or both.
function roles() { return [...document.querySelectorAll("input[name=role]:checked")].map((i) => i.value); }

function renderCategories() {
  const box = $("#categories");
  box.replaceChildren(...data.labels.categories.map((c) =>
    h("label", { class: "chip", "data-id": c.id },
      h("input", { type: "checkbox", value: c.id }), h("span", {}, c.label))));
}

function renderFactors() {
  for (const r of ["p", "b"]) {
    $(`#factors-${r}`).replaceChildren(...FACTORS_BY_ROLE[r].map((id) =>
      h("label", {}, h("input", { type: "checkbox", value: id }), h("span", {}, data.labels.factors[id]))));
  }
}

function onRoleChange(e) {
  // At least one situation stays ticked.
  if (!roles().length && e) e.target.checked = true;
  const active = roles();
  for (const fs of document.querySelectorAll("fieldset[data-role]")) fs.hidden = !active.includes(fs.dataset.role);
}

function onSearch() {
  const hits = searchCategories($("#search").value, data.labels.categories);
  const box = $("#categories");
  for (const chip of box.children) chip.classList.toggle("suggested", hits.includes(chip.dataset.id));
  // Suggested categories first, in order of match strength.
  const chips = [...box.children].sort((a, b) => {
    const ia = hits.indexOf(a.dataset.id), ib = hits.indexOf(b.dataset.id);
    return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib);
  });
  box.replaceChildren(...chips);
}

function numberOrNull(sel) {
  const v = $(sel).value.trim();
  return v === "" ? null : Number(v);
}

function readQuery(r) {
  return {
    role: r,
    categories: r === "p" ? [...document.querySelectorAll("#categories input:checked")].map((i) => i.value) : [],
    uszczerbek: r === "p" ? numberOrNull("#uszczerbek") : null,
    age: numberOrNull(r === "p" ? "#age" : "#age-b"),
    relation: r === "b" ? $("#relation").value || null : null,
    factors: [...document.querySelectorAll(`#factors-${r} input:checked`)].map((i) => i.value),
    offer: numberOrNull(`#offer-${r}`),
    since: Math.min(Math.max(Number($("#since").value) || 2015, 2004), new Date().getFullYear()),
    real: $("#real").checked,
  };
}

// ------------------------------------------------------------------ results

const VERDICT_TEXT = {
  low: (p) => [`Oferta wygląda na zaniżoną`, `W ${p}% podobnych spraw sąd uznał za odpowiednią kwotę wyższą niż ta oferta. Jest niższa niż pierwszy kwartyl (Q1) kwot sądowych.`],
  below: (p) => [`Oferta jest poniżej mediany kwot sądowych`, `W ${p}% podobnych spraw sąd uznał za odpowiednią kwotę wyższą niż ta oferta.`],
  typical: (p) => [`Oferta mieści się w rozstępie międzykwartylowym`, `Oferta jest między medianą a trzecim kwartylem (Q3) kwot uznanych przez sądy za odpowiednie (w ${p}% spraw sąd ustalił więcej).`],
  high: (p) => [`Oferta jest powyżej trzeciego kwartyla (Q3) kwot sądowych`, `Tylko w ${p}% podobnych spraw sąd uznał za odpowiednią kwotę wyższą niż ta oferta.`],
};

function chart(similar, stats, offer) {
  const W = 640, H = 150, L = 12, R = 12, top = 18, mid = 70;
  const max = Math.max(stats.p90 * 1.25, offer ? offer * 1.1 : 0, 1000);
  const x = (v) => L + (Math.min(v, max) / max) * (W - L - R);
  const ns = "http://www.w3.org/2000/svg";
  const s = (tag, attrs) => { const e = document.createElementNS(ns, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); return e; };
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Rozkład kwot uznanych przez sądy w podobnych sprawach" });
  svg.append(s("rect", { x: x(stats.p25), y: mid - 26, width: Math.max(2, x(stats.p75) - x(stats.p25)), height: 52, rx: 6, fill: "var(--band)" }));
  similar.forEach((c, i) => {
    const jitter = ((i * 37) % 41) - 20;
    svg.append(s("circle", { cx: x(c.t), cy: mid + jitter, r: 3.2, fill: "var(--dot)", "fill-opacity": 0.75 }));
  });
  svg.append(s("line", { x1: x(stats.median), x2: x(stats.median), y1: mid - 30, y2: mid + 30, stroke: "var(--text)", "stroke-width": 2 }));
  const lab = (v, y, text, anchor = "middle", color = "var(--muted)") => {
    const t = s("text", { x: Math.min(Math.max(x(v), 40), W - 40), y, "text-anchor": anchor, "font-size": 13, fill: color });
    t.textContent = text; svg.append(t);
  };
  lab(stats.median, top - 2, `mediana ${formatPLN(stats.median)}`, "middle", "var(--text)");
  if (offer != null) {
    svg.append(s("line", { x1: x(offer), x2: x(offer), y1: mid - 38, y2: mid + 38, stroke: "var(--offer)", "stroke-width": 3 }));
    lab(offer, H - 30, `oferta ${formatPLN(offer)}`, "middle", "var(--offer)");
  }
  const axisY = H - 10;
  svg.append(s("line", { x1: L, x2: W - R, y1: axisY - 12, y2: axisY - 12, stroke: "var(--line)" }));
  for (const f of [0, 0.25, 0.5, 0.75, 1]) {
    const v = max * f, t = s("text", { x: x(v), y: axisY, "text-anchor": f === 0 ? "start" : f === 1 ? "end" : "middle", "font-size": 11, fill: "var(--muted)" });
    t.textContent = v >= 1000 ? `${Math.round(v / 1000)} tys.` : `${Math.round(v)}`;
    svg.append(t);
  }
  return h("div", { class: "chart" },
    h("p", { class: "caption" }, "Każda kropka to kwota, którą sąd uznał za odpowiednią w jednej z podobnych spraw. Niebieski pas: rozstęp międzykwartylowy (od Q1 do Q3, czyli 50% spraw o środkowych kwotach); czarna kreska: mediana."),
    svg);
}

function stat(label, value) { return h("div", { class: "stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value)); }

function citation(jid) {
  const [sygn, court, date, url] = data.judgments[jid];
  return { sygn, court, date, url, text: `wyrok ${court || "sądu"} z dnia ${date}, sygn. akt ${sygn}` };
}

function quoteBlock(text, jid, cls = "") {
  const c = citation(jid);
  const copy = h("button", { type: "button", class: "link copy", onclick: async (e) => {
    await navigator.clipboard.writeText(`„${text}” (${c.text})`);
    e.target.textContent = "Skopiowano";
  } }, "Kopiuj cytat z sygnaturą");
  return h("blockquote", { class: cls }, `„${text}”`,
    h("cite", {}, c.text, " · ", h("a", { href: c.url, target: "_blank", rel: "noopener" }, "pełna treść w SAOS")), copy);
}

function caseCard(c, q) {
  const { sygn, court, date, url } = citation(c.j);
  const tags = [
    ...(c.r === "p" ? c.c.map((id) => data.catLabel[id]) : [RELATION_LABELS[c.rel] || "osoba bliska"]),
    c.u != null ? `uszczerbek ${c.u}%` : null,
    c.pc ? `przyczynienie ${c.pc}%` : null,
    ...c.f.filter((f) => q.factors.includes(f)).map((f) => data.labels.factors[f]),
  ].filter(Boolean);
  const amountNote = { a: "kwota wskazana przez sąd", s: "zasądzono + wypłacone wcześniej", w: "kwota zasądzona (wcześniejsza wypłata nieznana)" }[c.how];
  const details = h("div", {});
  const more = h("button", { type: "button", class: "link", onclick: async () => {
    more.remove();
    const qs = await quotesFor(c.j);
    const factorQuotes = qs.f.filter(([, f]) => f.some((x) => q.factors.includes(x))).concat(qs.f).slice(0, 2);
    details.append(...qs.v.map((t) => quoteBlock(t, c.j, "verdict-quote")), ...[...new Set(factorQuotes)].map(([t]) => quoteBlock(t, c.j)));
    if (!qs.v.length && !qs.f.length) details.append(h("p", { class: "notes" }, "Brak fragmentów do zacytowania."));
  } }, "Pokaż fragmenty uzasadnienia");
  return h("article", { class: "case" },
    h("div", { class: "case-head" },
      h("span", { class: "case-title" }, h("a", { href: url, target: "_blank", rel: "noopener" }, sygn)),
      h("span", { class: "case-amount", title: amountNote }, formatPLN(c.t))),
    c.tn != null && Math.round(c.tn) !== Math.round(c.t)
      ? h("div", { class: "case-meta" }, `W cenach z ${data.inflation.base} r.; w wyroku: ${formatPLN(c.tn)}`) : null,
    h("div", { class: "case-meta" }, `${court || ""}, ${date || ""}`, c.pd ? ` · ubezpieczyciel wypłacił wcześniej ${formatPLN(c.pd)}` : "",
      c.i === 2 ? " · II instancja" : ""),
    h("div", { class: "tags" }, tags.map((t) => h("span", { class: "tag" }, t))),
    more, details);
}

const BLOCK_TITLES = {
  p: "Zadośćuczynienie za Twoje obrażenia (art. 445 k.c.)",
  b: "Zadośćuczynienie za śmierć osoby bliskiej (art. 446 § 4 k.c.)",
};

// Results for the ticked situations, one block each, plus a combined summary when both are ticked.
function renderResults() {
  const out = $("#results");
  out.hidden = false;
  const blocks = roles().map((r) => { const q = readQuery(r); return { r, q, ...resultBlock(q) }; });
  const parts = [];
  if (blocks.length > 1) {
    const ok = blocks.filter((b) => b.stats);
    const medians = ok.reduce((sum, b) => sum + b.stats.median, 0);
    const offers = blocks.every((b) => b.q.offer != null) ? blocks.reduce((sum, b) => sum + b.q.offer, 0) : null;
    parts.push(h("div", { class: "verdict" }, h("h2", {}, "Dwa roszczenia z jednego wypadku"),
      h("p", {}, "Sąd ocenia każde roszczenie osobno, dlatego poniżej są dwa porównania. " +
        (ok.length === 2 ? `Suma median kwot sądowych w podobnych sprawach: ${formatPLN(medians)}` +
          (offers != null ? `; suma ofert ubezpieczyciela: ${formatPLN(offers)}.` : ".") : ""))));
  }
  for (const b of blocks) {
    if (blocks.length > 1) parts.push(h("h2", { class: "block-title" }, BLOCK_TITLES[b.r]));
    parts.push(...b.nodes);
  }
  out.replaceChildren(...parts);
  out.scrollIntoView({ behavior: "smooth", block: "start" });
}

// One comparison (verdict, statistics, chart, quotes, similar cases) for a single claim.
function resultBlock(q) {
  const { cases: similar, notes } = findSimilar(q.real ? data.realCases : data.cases, q);
  if (similar.length < 5) {
    return { stats: null, nodes: [h("div", { class: "verdict" }, h("h2", {}, "Za mało podobnych spraw"),
      h("p", {}, "Spróbuj zaznaczyć mniej okoliczności albo uwzględnić starsze orzeczenia."))] };
  }
  const stats = summarize(similar, q.offer);
  const v = verdict(stats, q.offer);
  const parts = [];
  if (v) {
    const [title, text] = VERDICT_TEXT[v.level](v.pct);
    parts.push(h("div", { class: `verdict ${v.level}` }, h("h2", {}, title), h("p", {}, text)));
  } else {
    parts.push(h("div", { class: "verdict" }, h("h2", {}, "Kwoty w podobnych sprawach"),
      h("p", {}, "Wpisz kwotę z oferty ubezpieczyciela, aby zobaczyć, jak wypada na tle orzeczeń.")));
  }
  parts.push(h("div", { class: "stats" },
    stat("Mediana kwot sądowych", formatPLN(stats.median)),
    stat("Rozstęp międzykwartylowy (Q1–Q3)", `${formatPLN(stats.p25)} – ${formatPLN(stats.p75)}`),
    stat("Mediana wcześniejszych wypłat ubezpieczycieli", stats.paidN >= 5 ? formatPLN(stats.paidMedian) : "–")));
  parts.push(chart(similar, stats, q.offer));
  const prices = q.real ? ` Kwoty z orzeczeń przeliczono na ceny z ${data.inflation.base} r. według wskaźników inflacji GUS.` : " Kwoty są nominalne (z dat orzeczeń).";
  const basis = `Porównanie opiera się na ${stats.n} najbardziej podobnych sprawach z lat ${q.since}–${new Date().getFullYear()}.${prices} Q1 (pierwszy kwartyl): w 25% spraw sąd uznał za odpowiednią niższą kwotę; Q3 (trzeci kwartyl): w 25% spraw – wyższą.`;
  const ratio = stats.ratioMedian ? ` W sprawach, w których znamy obie kwoty, sąd uznał za odpowiednią kwotę średnio (mediana) ${stats.ratioMedian.toFixed(1).replace(".", ",")} razy wyższą niż wcześniejsza wypłata ubezpieczyciela.` : "";
  parts.push(h("p", { class: "notes" }, basis + ratio, " ", h("a", { href: "metodologia.html" }, "Jak to liczymy?")), ...notes.map((n) => h("p", { class: "notes" }, n)));

  // Quotes to cite: courts finding an insurer's payment too low, in the most similar cases.
  const quotable = similar.filter((c) => c.q > 0).slice(0, 4);
  if (quotable.length) {
    parts.push(h("h3", {}, "Co sądy pisały o zaniżonych wypłatach w podobnych sprawach"));
    const box = h("div", {}, h("p", { class: "notes" }, "Wczytywanie cytatów…"));
    parts.push(box);
    Promise.all(quotable.map(async (c) => ({ c, qs: await quotesFor(c.j) }))).then((items) => {
      box.replaceChildren(...items.flatMap(({ c, qs }) => qs.v.slice(0, 1).map((t) => quoteBlock(t, c.j, "verdict-quote"))));
    });
  }

  parts.push(h("h3", {}, "Najbardziej podobne sprawy"));
  const list = h("div", {}, similar.slice(0, SHOWN).map((c) => caseCard(c, q)));
  parts.push(list);
  if (similar.length > SHOWN) {
    const btn = h("button", { type: "button", class: "link", onclick: () => {
      list.append(...similar.slice(SHOWN).map((c) => caseCard(c, q))); btn.remove();
    } }, `Pokaż wszystkie (${similar.length})`);
    parts.push(btn);
  }
  return { stats, nodes: parts };
}

// ------------------------------------------------------------------ start

async function main() {
  try {
    data = await loadData();
  } catch (e) {
    $("#form").replaceChildren(h("p", {}, "Nie udało się wczytać danych. Uruchom aplikację przez serwer HTTP (zobacz README w katalogu app/)."));
    return;
  }
  renderCategories();
  renderFactors();
  for (const el of document.querySelectorAll(".base-year")) el.textContent = data.inflation.base;
  for (const r of document.querySelectorAll("input[name=role]")) r.addEventListener("change", onRoleChange);
  $("#search").addEventListener("input", onSearch);
  $("#form").addEventListener("submit", (e) => { e.preventDefault(); renderResults(); });
  if (location.hash.length > 1) prefillFromHash();
}

// Pre-fill from the URL fragment, e.g.
//   #rola=p&kat=noga,glowa&u=10&wiek=35&okol=psychika&oferta=15000          (injured)
//   #rola=b&rel=rodzic&okol_b=wiez_ze_zmarlym&oferta_b=50000                 (a close person died)
//   #rola=pb&kat=noga&oferta=15000&rel=malzonek&oferta_b=60000               (both)
// With rola=b alone, "oferta"/"okol"/"wiek" are accepted too (older links).
// The fragment never leaves the browser (it isn't sent to the server), so nothing is logged.
function prefillFromHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  const set = (sel, v) => { if (v != null) $(sel).value = v; };
  const rola = p.get("rola") || "p";
  const active = ["p", "b"].filter((r) => rola.includes(r));
  for (const box of document.querySelectorAll("input[name=role]")) box.checked = active.includes(box.value);
  onRoleChange();
  const onlyB = active.length === 1 && active[0] === "b";
  const list = (k) => (p.get(k) || "").split(",").filter(Boolean);
  const tick = (sel, ids) => {
    for (const id of ids) { const i = document.querySelector(`${sel} input[value="${CSS.escape(id)}"]`); if (i) i.checked = true; }
  };
  tick("#categories", list("kat"));
  tick("#factors-p", onlyB ? [] : list("okol"));
  tick("#factors-b", onlyB ? list("okol").concat(list("okol_b")) : list("okol_b"));
  set("#uszczerbek", p.get("u"));
  set("#age", onlyB ? null : p.get("wiek"));
  set("#age-b", p.get("wiek_b") ?? (onlyB ? p.get("wiek") : null));
  set("#relation", p.get("rel"));
  set("#offer-p", onlyB ? null : p.get("oferta"));
  set("#offer-b", p.get("oferta_b") ?? (onlyB ? p.get("oferta") : null));
  set("#since", p.get("od"));
  if (p.get("ceny") === "nom") $("#real").checked = false;
  renderResults();
}

main();
