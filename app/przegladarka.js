// Searchable browser of the parser's results: filters over data/browse.json, full-text search
// through data/index/, and per-judgment details (with source sentences) from data/parser/.
import { fold, queryStems, matchRows, markAmounts, formatPLN } from "./logic.js";

const $ = (s) => document.querySelector(s);
const PAGE = 50;
const TYPE_LABELS = { "zadośćuczynienie": "Zadośćuczynienie", odszkodowanie: "Odszkodowanie", renta: "Renta", inne: "Inne" };
const ROLE_LABELS = { poszkodowany: "poszkodowany", osoba_najblizsza: "osoba bliska zmarłego" };
const AMOUNT_LABELS = [["claimed", "Żądana", "cl"], ["appropriate", "Uznana przez sąd za odpowiednią", "ap"],
                       ["paid", "Wypłacona wcześniej przez ubezpieczyciela", "pd"], ["awarded", "Zasądzona", "op"]];

let rows = [], meta = null, labels = null, catLabel = {};
const indexCache = new Map(), shardCache = new Map();
let shown = PAGE, current = [];
let sort = { key: "date", dir: "desc" };   // key: "date" | "total"

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
const get = (p) => fetch(p).then((r) => { if (!r.ok) throw new Error(p); return r.json(); });

// browse.json row: [id, sygnatura, court, date, instance, road, roles, maxTotal, cats, tooLow, nClaims, saosDate]
// (saosDate: SAOS's original date when it was an obvious typo that the export corrected)
const R = { id: 0, sygn: 1, court: 2, date: 3, inst: 4, road: 5, roles: 6, total: 7, cats: 8, tooLow: 9, claims: 10, saosDate: 11 };

// Rows without a value (no date / no amount read) always go last, whatever the direction.
function sortRows(list) {
  const col = sort.key === "total" ? R.total : R.date, sign = sort.dir === "asc" ? 1 : -1;
  return list.sort((a, b) => {
    const x = a[col], y = b[col];
    if (x == null || y == null) return (x == null) - (y == null);
    return (x < y ? -1 : x > y ? 1 : 0) * sign;
  });
}

function sortHeader(key, label, cls) {
  const active = sort.key === key;
  const arrow = active ? (sort.dir === "asc" ? " ▲" : " ▼") : "";
  return h("th", { class: cls, "aria-sort": active ? (sort.dir === "asc" ? "ascending" : "descending") : "none" },
    h("button", { type: "button", class: "sort", onclick: () => {
      sort = active ? { key, dir: sort.dir === "asc" ? "desc" : "asc" } : { key, dir: "desc" };
      sortRows(current); shown = PAGE; renderList(); syncHash();
    } }, label + arrow));
}

function dateCell(r) {
  if (!r[R.saosDate]) return r[R.date] || "";
  return h("span", { title: `Data poprawiona: w SAOS ${r[R.saosDate]} (oczywisty błąd pisarski w wyroku)` }, `${r[R.date]}*`);
}

async function indexShard(prefix) {
  if (!meta.prefixes.includes(prefix)) return {};
  if (!indexCache.has(prefix)) indexCache.set(prefix, get(`data/index/${prefix}.json`));
  return indexCache.get(prefix);
}

async function parserShard(id) {
  const n = String(id % meta.shards).padStart(3, "0");
  if (!shardCache.has(n)) shardCache.set(n, get(`data/parser/${n}.json`));
  return (await shardCache.get(n))[String(id)];
}

// ------------------------------------------------------------------ filtering

async function applyFilters() {
  const q = $("#q").value.trim();
  const sygn = fold($("#sygn").value.trim());
  const role = $("#role").value, cat = $("#cat").value, road = $("#road").value;
  const from = Number($("#from").value) || 0, to = Number($("#to").value) || 9999;
  const tooLow = $("#toolow").checked, amount = $("#amount").checked;
  let allowed = null;
  const notes = [];
  if (q) {
    const stems = queryStems(q, meta.stem);
    const ignored = stems.filter((s) => meta.unindexed.includes(s));
    const used = stems.filter((s) => !meta.unindexed.includes(s));
    if (ignored.length) notes.push(`Pominięto bardzo częste słowa: ${ignored.join(", ")}.`);
    if (used.length) {
      $("#status").textContent = "Szukanie…";
      const shards = Object.fromEntries(await Promise.all([...new Set(used.map((s) => s.slice(0, 2)))].map(async (p) => [p, await indexShard(p)])));
      allowed = matchRows(used, shards);
    }
  }
  current = [];
  rows.forEach((r, i) => {
    if (allowed && !allowed.has(i)) return;
    const year = Number((r[R.date] || "").slice(0, 4));
    if (year < from || year > to) return;
    if (role && !r[R.roles].includes(role)) return;
    if (cat && !r[R.cats].includes(cat)) return;
    if (road !== "" && String(r[R.road]) !== road) return;
    if (tooLow && !r[R.tooLow]) return;
    if (amount && r[R.total] == null) return;
    if (sygn && !fold(`${r[R.sygn]} ${r[R.court]}`).includes(sygn)) return;
    current.push(r);
  });
  sortRows(current);
  shown = PAGE;
  $("#status").textContent = `${current.length.toLocaleString("pl-PL")} orzeczeń` +
    (q ? ` zawierających w zdaniach źródłowych: „${q}”` : "") + ". " + notes.join(" ");
  renderList();
  syncHash();
}

function renderList() {
  const table = h("table", { class: "browse" },
    h("thead", {}, h("tr", {}, h("th", {}, "Sygnatura"), h("th", {}, "Sąd"), sortHeader("date", "Data", null),
      sortHeader("total", "Kwota sądowa", "num"), h("th", {}, "Odczytane cechy"))),
    h("tbody", {}, current.slice(0, shown).map((r) => {
      const tags = [
        r[R.road] ? null : "parser: nie wypadek drogowy",
        r[R.roles].includes("b") ? "osoba bliska" : null,
        ...r[R.cats].map((c) => catLabel[c]),
        r[R.tooLow] ? "„wypłata zaniżona”" : null,
        r[R.inst] === 2 ? "II instancja" : null,
      ].filter(Boolean);
      return h("tr", { tabindex: "0", onclick: () => openDetail(r[R.id]), onkeydown: (e) => { if (e.key === "Enter") openDetail(r[R.id]); } },
        h("td", {}, h("a", { href: `#j=${r[R.id]}`, onclick: (e) => { e.preventDefault(); openDetail(r[R.id]); } }, r[R.sygn])),
        h("td", {}, r[R.court] || ""), h("td", { class: "nowrap" }, dateCell(r)),
        h("td", { class: "num" }, r[R.total] != null ? formatPLN(r[R.total]) : "–"),
        h("td", {}, h("div", { class: "tags" }, tags.map((t) => h("span", { class: "tag" }, t)))));
    })));
  const more = current.length > shown
    ? h("button", { type: "button", class: "link", onclick: () => { shown += PAGE; renderList(); } }, `Pokaż kolejne (${Math.min(PAGE, current.length - shown)} z ${current.length - shown})`)
    : null;
  $("#list").replaceChildren(h("div", { class: "table-wrap" }, table), more || "");
}

// ------------------------------------------------------------------ detail

function sentence(text, value) {
  const parts = markAmounts(text, value);
  const note = value != null && !parts.some((p) => p.mark)
    ? h("cite", {}, "Tej kwoty nie ma w zdaniu wprost: parser wyliczył ją (np. rozbił kwotę łączną według uzasadnienia albo dodał wcześniejszą wypłatę).")
    : null;
  return h("blockquote", {}, parts.map((p) => (p.mark ? h("mark", {}, p.text) : p.text)), note);
}

function claimBlock(c) {
  const facts = [
    ROLE_LABELS[c.role] || c.role, c.relation, c.died ? "poszkodowany zmarł" : null,
    c.age != null ? `wiek ${c.age}` : null, c.u != null ? `uszczerbek ${c.u}%` : null,
    c.pc ? `przyczynienie ${c.pc}%` : null,
  ].filter(Boolean);
  return h("div", { class: "card claim" },
    h("h3", {}, `Powód/powódka: ${c.who}`),
    h("p", { class: "notes" }, facts.join(" · ")),
    c.injuries.length ? h("p", {}, h("strong", {}, "Obrażenia odczytane z uzasadnienia: "), c.injuries.join("; ")) : null,
    c.awards.map((a) => h("div", { class: "award" },
      h("h4", {}, `${TYPE_LABELS[a.type] || a.type}${a.monthly ? " (miesięcznie)" : ""}`),
      h("dl", { class: "amounts" }, AMOUNT_LABELS.flatMap(([key, label, src]) => {
        const sources = a.src[src] || [];
        return [h("dt", {}, label, ": ", h("strong", {}, a[key] != null ? formatPLN(a[key]) : "nie odczytano")),
          h("dd", {}, sources.length ? sources.map((t) => sentence(t, a[key]))
            : h("span", { class: "notes" }, a[key] != null ? "wyliczona z innych kwot" : "brak zdania źródłowego"))];
      })))));
}

async function openDetail(id) {
  const r = rows.find((x) => x[R.id] === id);
  const d = await parserShard(id);
  const box = $("#detail");
  box.hidden = false;
  if (!r || !d) { box.replaceChildren(h("p", {}, "Nie znaleziono orzeczenia.")); return; }
  const verdicts = d.passages.filter((p) => p[1] === "too_low");
  const factors = d.passages.filter((p) => p[2].length);
  // replaceChildren() doesn't flatten arrays: flatten here, or nested lists would print as text.
  box.replaceChildren(...[
    h("p", {}, h("button", { type: "button", class: "link", onclick: closeDetail }, "← Wróć do listy")),
    h("h2", {}, r[R.sygn]),
    h("p", { class: "notes" }, `${r[R.court] || ""}, ${r[R.date] || ""}${r[R.saosDate] ? ` (data poprawiona; w SAOS: ${r[R.saosDate]})` : ""}${r[R.inst] === 2 ? " · II instancja" : ""} · `,
      h("a", { href: d.url, target: "_blank", rel: "noopener" }, "pełna treść orzeczenia w SAOS")),
    h("div", { class: "card" },
      h("h3", {}, "Decyzje parsera"),
      h("ul", {},
        h("li", {}, r[R.road] ? "Uznane za sprawę z wypadku drogowego ze szkodą na osobie." : "Odrzucone: parser uznał, że to nie jest sprawa z wypadku drogowego ze szkodą na osobie."),
        r[R.cats].length ? h("li", {}, `Kategorie obrażeń: ${r[R.cats].map((c) => catLabel[c]).join(", ")}.`) : null,
        h("li", {}, `Odczytane roszczenia: ${r[R.claims]}.`),
        h("li", { class: "notes" }, `Ślad techniczny: ${d.analysis}`))),
    d.claims.length ? h("h3", {}, "Odczytane kwoty i zdania, z których pochodzą") : h("p", {}, "Parser nie odczytał żadnych roszczeń."),
    h("p", { class: "notes" }, "Zaznaczona liczba w zdaniu to kwota, którą parser stamtąd odczytał."),
    d.claims.map(claimBlock),
    verdicts.length ? [h("h3", {}, "Sąd o wcześniejszej wypłacie ubezpieczyciela"),
      ...verdicts.map((p) => h("blockquote", { class: "verdict-quote" }, p[3]))] : null,
    factors.length ? [h("h3", {}, "Okoliczności wskazane przez sąd"),
      ...factors.map((p) => h("blockquote", {}, p[3], h("cite", {}, p[2].map((f) => labels.factors[f]).join(" · "))))] : null,
  ].flat(2).filter((x) => x != null));
  $("#list").hidden = true; $("#filters").hidden = true; $("#status").hidden = true;
  history.replaceState(null, "", `#j=${id}`);
  box.scrollIntoView({ block: "start" });
}

function closeDetail() {
  $("#detail").hidden = true;
  $("#list").hidden = false; $("#filters").hidden = false; $("#status").hidden = false;
  syncHash();
}

// Filters live in the URL fragment (never sent to a server), so a search can be bookmarked or shared.
function syncHash() {
  const p = new URLSearchParams();
  for (const id of ["q", "sygn", "role", "cat", "from", "to", "road"]) if ($(`#${id}`).value) p.set(id, $(`#${id}`).value);
  for (const id of ["toolow", "amount"]) if ($(`#${id}`).checked) p.set(id, "1");
  if (sort.key !== "date" || sort.dir !== "desc") p.set("sort", `${sort.key}-${sort.dir}`);
  history.replaceState(null, "", p.toString() ? `#${p}` : location.pathname);
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  for (const id of ["q", "sygn", "role", "cat", "from", "to", "road"]) if (p.has(id)) $(`#${id}`).value = p.get(id);
  for (const id of ["toolow", "amount"]) $(`#${id}`).checked = p.get(id) === "1";
  const [key, dir] = (p.get("sort") || "").split("-");
  if (["date", "total"].includes(key) && ["asc", "desc"].includes(dir)) sort = { key, dir };
  return p.get("j");
}

async function main() {
  try {
    [rows, meta, labels] = await Promise.all([get("data/browse.json"), get("data/browse-meta.json"), get("data/labels.json")]);
  } catch {
    $("#status").textContent = "Nie udało się wczytać danych (uruchom `orzeczenia export` i serwer HTTP).";
    return;
  }
  catLabel = Object.fromEntries(labels.categories.map((c) => [c.id, c.label]));
  $("#cat").append(...labels.categories.map((c) => h("option", { value: c.id }, c.label)));
  const j = readHash();
  let timer = null;
  $("#filters").addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(applyFilters, 250); });
  $("#filters").addEventListener("submit", (e) => { e.preventDefault(); applyFilters(); });
  await applyFilters();
  if (j) openDetail(Number(j));
}

main();
