"use strict";
// VAL3 vault view. Every vault text reaches the page as JSON and is inserted with textContent, never as markup.

const VIA = [["capture", "capture (extractor)", "--t1"], ["save", "agent's own save", "--t4"],
  ["import", "import", "--t5"], ["tool-add", "tool registered by a person", "--t3"],
  ["not-recorded", "not recorded", "--line"]];
const VIA_LABEL = Object.fromEntries(VIA.map(([k, l]) => [k, l]));
const KINDS = ["fact", "recipe", "preference", "decision", "tool"];
const STATES = ["live", "superseded", "withdrawn", "unverified", "no-evidence", "approved-by-person"];
const SORTS = [["-recorded", "newest first"], ["recorded", "oldest first"], ["ref", "ref"], ["title", "title"],
  ["kind", "kind"], ["state", "state"]];
const VIEWS = { overview: "Overview", map: "Map", entries: "Entries", sessions: "Sessions", names: "Names" };

const main = document.getElementById("main"), panel = document.getElementById("panel");
const tabs = document.getElementById("tabs");
let summary = null, usage = null, selected = null, view = "overview";
let filter = { q: "", kind: "", index: "", state: "", via: "", session: "", from: "", to: "", vault: "",
  sort: "-recorded", page: 1 };
let nameQuery = "", mapVault = "", mapMode = "index", mapData = null, nodes = null, redraw = null;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = String(text);
  return n;
}
function returned(n) { return n === null || n === undefined ? "not recorded" : n === 0 ? "not seen" : n + "×"; }
function since() { return usage && usage.since ? usage.since.slice(0, 10) : null; }
function card(title) { const c = el("section", "card"); c.append(el("h2", "", title)); return c; }
function day(iso) { return iso ? iso.slice(0, 10) : "not recorded"; }
function api(path) {
  return fetch("api/" + path, { headers: { Accept: "application/json" } }).then(r => r.json().then(data => {
    if (!r.ok) throw new Error(data.error || ("HTTP " + r.status));
    return data;
  }));
}
function failure(where, err) { where.replaceChildren(el("p", "bad-text", "Could not load: " + err.message)); }
function link(ref, onclick) {
  const b = el("button", "link ref", ref); b.type = "button";
  b.onclick = onclick || (() => select(ref));
  return b;
}
function states(row) {
  const out = [];
  if (row.superseded) out.push(["superseded", "warn"]);
  if (row.withdrawn) out.push(["withdrawn", "bad"]);
  if (row.live && !row.evidence.length && !row.approved_by_person) out.push(["unverified", "warn"]);
  else if (row.live) out.push(["live", "ok"]);
  return out.map(([s, c]) => el("span", "pill " + c, s));
}
function failed(vaults) {
  const bad = vaults.filter(v => !v.ok);
  if (!bad.length) return null;
  return el("p", "bad-text", "Not read: " + bad.map(v => v.name + " (" + v.reason + ")").join(", ") +
    ". Their figures are left out, not counted as zero.");
}

// ---------- header ----------
function header() {
  document.getElementById("project").textContent = summary.project;
  document.getElementById("when").textContent = "vault view · data at " + summary.at.replace("T", " ") +
    " · read-only · VAL3 writes no export";
  const list = document.getElementById("vaults");
  list.replaceChildren();
  summary.vaults.forEach(v => {
    const line = el("div");
    line.append(el("span", "ref", v.name), el("span", "muted", " " + v.role));
    if (!v.ok) line.append(el("span", "bad-text", " · not read: " + v.reason));
    else {
      if (v.description) line.append(el("span", "", " · " + v.description));
      if (v.mirror) line.append(el("span", "muted", " · mirror of " + v.mirror.source + ", synced " +
        v.mirror.synced_at.replace("T", " ")));
    }
    list.append(line);
  });
}

// ---------- overview ----------
function overview() {
  const ok = summary.vaults.filter(v => v.ok);
  const sum = key => ok.reduce((n, v) => n + v.counts[key], 0);
  const t = card("What the vaults hold"); const tiles = el("div", "tiles");
  [[sum("entries"), "entries"], [sum("live_with_evidence"), "live, with evidence"],
    [sum("unverified"), "unverified (live, no evidence, not approved by a person)"],
    [sum("superseded"), "superseded"], [sum("withdrawn"), "withdrawn"],
    [sum("approved_by_person"), "approved by a person"],
    usage && usage.log && since()
      ? [usage.vaults.filter(v => v.ok).reduce((n, v) => n + v.live_not_seen, 0), "live, not seen in a search on this machine since " + since()]
      : ["not recorded", "searches on this machine: no usage log in the last " + (usage ? usage.days : 30) + " days"]].forEach(([n, label]) => {
    const d = el("div", "tile"); d.append(el("b", "", n), el("span", "", label)); tiles.append(d);
  });
  t.append(tiles);
  const bad = failed(summary.vaults); if (bad) t.append(bad);

  const g = el("div", "grid2");
  const a = card("Admitted per day, by who wrote it");
  const perDay = {};
  ok.forEach(v => v.admitted_per_day.forEach(d => {
    const slot = perDay[d.day] || (perDay[d.day] = {});
    Object.entries(d.via).forEach(([k, n]) => { slot[k] = (slot[k] || 0) + n; });
  }));
  const days = Object.keys(perDay).sort().slice(-60);
  if (!days.length) a.append(el("p", "muted", "No entries yet."));
  else {
    const max = Math.max(...days.map(d => Object.values(perDay[d]).reduce((x, y) => x + y, 0)));
    const bars = el("div", "days");
    days.forEach(d => {
      const col = el("div", "day"), total = Object.values(perDay[d]).reduce((x, y) => x + y, 0);
      col.title = d + ": " + total + " entries";
      VIA.forEach(([k, , colour]) => {
        if (!perDay[d][k]) return;
        const s = el("i"); s.style.height = (110 * perDay[d][k] / max) + "px"; s.style.background = "var(" + colour + ")";
        col.append(s);
      });
      bars.append(col);
    });
    const ax = el("div", "axis");
    ax.append(el("span", "", days[0]), el("span", "", "busiest day: " + max + " entries"), el("span", "", days[days.length - 1]));
    const lg = el("div", "legend");
    VIA.forEach(([, label, colour]) => { const s = el("span"), i = el("i"); i.style.background = "var(" + colour + ")"; s.append(i, label); lg.append(s); });
    a.append(bars, ax, lg, el("p", "sub", "Who wrote an entry is recorded from this version on; earlier entries show \"not recorded\"."));
  }
  const b = card("Entries per index");
  const per = {};
  ok.forEach(v => v.indexes.forEach(i => { const k = i.name === null ? "(no index)" : i.name; per[k] = (per[k] || 0) + i.entries; }));
  const rows = Object.entries(per).sort((x, y) => y[1] - x[1] || x[0].localeCompare(y[0]));
  const max = Math.max(1, ...rows.map(r => r[1])), w = el("div", "bars");
  rows.forEach(([name, n], k) => {
    const r = el("div", "bar"), i = el("i"), cell = el("div");
    i.style.width = (100 * n / max) + "%"; i.style.background = "var(--t" + (1 + k % 6) + ")"; cell.append(i);
    const label = link(name, () => { resetFilter(); filter.index = name === "(no index)" ? "(none)" : name; go("entries"); });
    r.append(label, cell, el("em", "", n)); w.append(r);
  });
  b.append(w, el("p", "sub", "An entry can be in several indexes or in none: the rows do not add up to the total."));
  g.append(a, b);

  const look = card("Worth a look"); const list = el("div", "stack");
  const group = (label, items) => {
    const row = el("div"); row.append(el("span", "muted", label + ": "));
    if (!items.length) row.append(el("span", "muted", "none"));
    items.forEach((e, k) => { row.append(link(e.ref)); if (k < items.length - 1) row.append(", "); });
    list.append(row);
  };
  if (usage && usage.log && since()) group("Most returned by searches on this machine since " + since(), usage.most_returned.slice(0, 5));
  else list.append(el("p", "muted", "Times returned by a search: not recorded on this machine."));
  group("Superseded this week", ok.flatMap(v => v.superseded_this_week));
  group("Live without evidence, latest", ok.flatMap(v => v.unverified_latest));
  look.append(list, el("p", "sub", "Searches are recall and memory_search on this machine; prompts answered by the hooks are not counted."));
  main.append(t, g, look);
}

// ---------- map ----------
function hash(text) { let h = 2166136261; for (const c of text) { h ^= c.codePointAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
function prng(seed) { let x = seed || 1; return () => { x ^= x << 13; x ^= x >>> 17; x ^= x << 5; return (x >>> 0) / 4294967296; }; }
function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
function layout(answer) {
  // deterministic: positions seeded by entry refs, nodes in id order, edges in the server's order
  const first = n => n.indexes[0] || "(no index)";
  const groups = [...new Set(answer.nodes.map(first))].sort(), centre = {};
  groups.forEach((g, i) => { const a = 2 * Math.PI * i / groups.length, r = groups.length > 1 ? 0.3 : 0; centre[g] = [0.5 + r * Math.cos(a), 0.5 + r * Math.sin(a)]; });
  const ps = answer.nodes.slice().sort((a, b) => Number(a.id) - Number(b.id)).map(n => {
    const j = prng(hash(n.ref)); return { n, x: centre[first(n)][0] + (j() - 0.5) * 0.2, y: centre[first(n)][1] + (j() - 0.5) * 0.2 };
  });
  const at = Object.fromEntries(ps.map(p => [p.n.ref, p])), rounds = ps.length > 800 ? 40 : 100;
  for (let it = 0; it < rounds; it++) {
    for (let i = 0; i < ps.length; i++) for (let k = i + 1; k < ps.length; k++) {
      const a = ps[i], b = ps[k], dx = a.x - b.x, dy = a.y - b.y, d2 = dx * dx + dy * dy + 1e-6;
      if (d2 < 0.003) { const f = 1.5e-5 / d2; a.x += dx * f; a.y += dy * f; b.x -= dx * f; b.y -= dy * f; }
    }
    answer.edges.forEach(e => {
      const a = at[e.from], b = at[e.to]; if (!a || !b) return;
      const w = e.type === "near" ? 0.02 : 0.01, dx = b.x - a.x, dy = b.y - a.y;
      a.x += dx * w; a.y += dy * w; b.x -= dx * w; b.y -= dy * w;
    });
    ps.forEach(p => { p.x = Math.min(0.97, Math.max(0.03, p.x)); p.y = Math.min(0.95, Math.max(0.05, p.y)); });
  }
  return ps;
}
function colourOf(n) {
  const pal = ["--t1", "--t2", "--t3", "--t4", "--t5", "--t6"];
  if (mapMode === "via") return [n.via || "not-recorded", (VIA.find(v => v[0] === (n.via || "not-recorded")) || VIA[4])[2]];
  if (mapMode === "age") {
    const days = (Date.parse(mapData.computed_at) - Date.parse(n.recorded)) / 864e5;
    return days < 7 ? ["last 7 days", "--t1"] : days < 30 ? ["7 to 30 days", "--t5"] : ["30 days or more", "--t3"];
  }
  const keyOf = m => mapMode === "kind" ? m.kind : mapMode === "session" ? (m.session || "unknown origin") : (m.indexes[0] || "(no index)");
  // The five most frequent values get a colour of their own; the rest share one, named "other", so no two share by chance.
  if (!rank || rank.mode !== mapMode || rank.data !== mapData) {
    const count = new Map(); mapData.vault.nodes.forEach(m => count.set(keyOf(m), (count.get(keyOf(m)) || 0) + 1));
    const order = [...count.entries()].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : 1)).map(e => e[0]);
    rank = { mode: mapMode, data: mapData, top: order.length <= pal.length ? order : order.slice(0, pal.length - 1) };
  }
  const key = keyOf(n), at = rank.top.indexOf(key);
  return at < 0 ? ["other", pal[pal.length - 1]] : [key, pal[at]];
}
let rank = null;
function map() {
  const c = card("Map of entries"), f = el("div", "filters"); main.append(c);
  const okVaults = summary.vaults.filter(v => v.ok).map(v => v.name);
  if (!mapVault || !okVaults.includes(mapVault)) mapVault = okVaults[0] || "";
  const vs = el("select"); vs.setAttribute("aria-label", "vault"); okVaults.forEach(v => { const o = el("option", "", v); o.value = v; vs.append(o); });
  vs.value = mapVault; vs.onchange = () => { mapVault = vs.value; mapData = null; render(); };
  const ms = el("select"); ms.setAttribute("aria-label", "colour");
  [["index", "colour by index"], ["via", "colour by who wrote it"], ["age", "colour by age"], ["kind", "colour by kind"], ["session", "colour by origin session"]]
    .forEach(([v, l]) => { const o = el("option", "", l); o.value = v; ms.append(o); });
  ms.value = mapMode; ms.onchange = () => { mapMode = ms.value; if (redraw) redraw(); };
  f.append(vs, ms, el("span", "sub", "Approximate layout by closeness in meaning, not a classification. Solid arrow: supersedes. Dashed: a recipe cites it. Faint: nearest by vector. Hollow: not live. Ring: unverified. Click a dot."));
  const cv = el("canvas"), info = el("div", "sub"), lg = el("div", "legend");
  c.append(f, cv, lg, info);
  if (!mapVault) { info.textContent = "No vault could be read."; return; }
  const draw = () => {
    if (!nodes || !cv.isConnected) return;
    const r = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1; cv.width = r.width * dpr; cv.height = r.height * dpr;
    const g = cv.getContext("2d"); g.scale(dpr, dpr); const W = r.width, H = r.height, at = Object.fromEntries(nodes.map(p => [p.n.ref, p]));
    g.clearRect(0, 0, W, H);
    mapData.vault.edges.forEach(e => {
      const p = at[e.from], q = at[e.to]; if (!p || !q) return;
      g.beginPath(); g.setLineDash(e.type === "cites" ? [5, 4] : []); g.globalAlpha = e.type === "near" ? 0.35 : 1;
      g.strokeStyle = e.type === "near" ? css("--line") : css("--fg"); g.lineWidth = e.type === "near" ? 1 : 1.6;
      g.moveTo(p.x * W, p.y * H); g.lineTo(q.x * W, q.y * H); g.stroke(); g.globalAlpha = 1;
      if (e.type === "supersedes") {
        const ang = Math.atan2((q.y - p.y) * H, (q.x - p.x) * W), tx = q.x * W - 8 * Math.cos(ang), ty = q.y * H - 8 * Math.sin(ang);
        g.setLineDash([]); g.beginPath(); g.moveTo(tx, ty); g.lineTo(tx - 7 * Math.cos(ang - 0.5), ty - 7 * Math.sin(ang - 0.5));
        g.lineTo(tx - 7 * Math.cos(ang + 0.5), ty - 7 * Math.sin(ang + 0.5)); g.closePath(); g.fillStyle = css("--fg"); g.fill();
      }
    });
    g.setLineDash([]);
    const seen = new Map();
    nodes.forEach(p => {
      const [key, v] = colourOf(p.n), col = css(v), x = p.x * W, y = p.y * H, unverified = p.n.live && !p.n.evidence.length && !p.n.approved_by_person;
      seen.set(key, v);
      g.globalAlpha = filter.session && (p.n.session || "(unknown)") !== filter.session ? 0.2 : 1;
      const times = usage && usage.returned[p.n.ref] || 0, rad = 4.5 + Math.min(6, times * 0.6);
      g.beginPath(); g.arc(x, y, rad, 0, 7);
      if (p.n.live && !unverified) { g.fillStyle = col; g.fill(); }
      else { g.fillStyle = css("--bg"); g.fill(); g.strokeStyle = col; g.lineWidth = 2; g.stroke(); }
      if (unverified) { g.beginPath(); g.arc(x, y, 2, 0, 7); g.fillStyle = col; g.fill(); }
      if (p.n.ref === selected) { g.beginPath(); g.arc(x, y, rad + 4, 0, 7); g.strokeStyle = css("--fg"); g.lineWidth = 2; g.stroke(); }
    });
    g.globalAlpha = 1;
    lg.replaceChildren(); [...seen.entries()].sort().slice(0, 24).forEach(([l, v]) => { const sp = el("span"), i = el("i"); i.style.background = "var(" + v + ")"; sp.append(i, l); lg.append(sp); });
    if (seen.size > 24) lg.append(el("span", "", (seen.size - 24) + " more"));
    lg.append(el("span", "", usage && usage.log ? "dot size: times returned by a search on this machine" : "dot size: uniform (no usage log on this machine)"));
  };
  redraw = draw;
  cv.onclick = ev => {
    if (!nodes) return;
    const r = cv.getBoundingClientRect(), x = (ev.clientX - r.left) / r.width, y = (ev.clientY - r.top) / r.height;
    let best = null, bd = 1e9;
    nodes.forEach(p => { const d = ((p.x - x) * r.width) ** 2 + ((p.y - y) * r.height) ** 2; if (d < bd) { bd = d; best = p; } });
    if (best && bd < 100) select(best.n.ref);
  };
  const show = data => {
    mapData = data; const a = data.vault;
    if (!a.ok) { info.replaceChildren(el("span", "bad-text", a.name + " could not be read: " + a.reason)); return; }
    nodes = layout(a); draw();
    info.textContent = "Shown: " + a.shown + " of " + a.total + " entries of " + a.name + " (the most recent)" +
      (a.left_out ? "; " + a.left_out + " older ones left out" : "") + "; " + a.without_vector + " shown without a vector" +
      (a.space ? " in the space " + a.space.model + " (" + a.space.dims + " dimensions)" : " (no vectors)") +
      "; computed at " + data.computed_at.replace("T", " ") + ", kept 10 minutes." +
      (filter.session ? " Session " + (filter.session === "(unknown)" ? "unknown origin" : filter.session) + " highlighted." : "");
  };
  if (mapData && mapData.vault.name === mapVault) show(mapData);
  else { info.textContent = "Computing the map…"; nodes = null; api("map?" + query({ vault: mapVault })).then(show).catch(err => failure(info, err)); }
}

// ---------- entries ----------
function resetFilter() {
  filter = { q: "", kind: "", index: "", state: "", via: "", session: "", from: "", to: "", vault: "", sort: "-recorded", page: 1 };
}
function query(params) {
  return Object.entries(params).filter(([, v]) => v !== "" && v != null).map(([k, v]) => encodeURIComponent(k) + "=" + encodeURIComponent(v)).join("&");
}
function select_(id, key, options, any) {
  const s = el("select"); s.id = id; s.setAttribute("aria-label", any);
  [["", any], ...options.map(o => Array.isArray(o) ? o : [o, o])].forEach(([v, l]) => { const o = el("option", "", l); o.value = v; s.append(o); });
  s.value = filter[key];
  s.onchange = () => { filter[key] = s.value; filter.page = 1; load(); };
  return s;
}
function input(id, key, type, placeholder) {
  const i = el("input"); i.id = id; i.type = type; i.placeholder = placeholder; i.setAttribute("aria-label", placeholder);
  i.value = filter[key];
  i.onchange = () => { filter[key] = i.value; filter.page = 1; load(); };
  return i;
}
function table(headers) {
  const sc = el("div", "scroll"), tb = el("table"), th = el("thead"), tr = el("tr"), body = el("tbody");
  headers.forEach(h => tr.append(el("th", "", h))); th.append(tr); tb.append(th, body); sc.append(tb);
  return [sc, body];
}
function entryRow(r) {
  const tr = el("tr"); tr.tabIndex = 0; if (r.ref === selected) tr.className = "sel";
  tr.onclick = () => select(r.ref); tr.onkeydown = ev => { if (ev.key === "Enter") select(r.ref); };
  const st = el("td"); st.append(...states(r));
  tr.append(el("td", "ref", r.ref), el("td", "", r.title), el("td", "", r.kind), el("td", "", r.indexes.join(", ") || "none"),
    st, el("td", "", r.evidence.join(", ") || (r.approved_by_person ? "approved by a person" : "none")),
    el("td", "", day(r.recorded)), el("td", "", r.via ? (VIA_LABEL[r.via] || r.via) : "not recorded"), el("td", "", returned(r.returned)));
  return tr;
}
const ENTRY_HEADERS = ["Ref", "Title", "Kind", "Index", "State", "Evidence", "Recorded", "Written by", "Returned"];
let load = () => {};
function entries() {
  const c = card("Entries"); const f = el("div", "filters");
  const indexes = [...new Set(summary.vaults.filter(v => v.ok).flatMap(v => v.indexes.map(i => i.name)).filter(n => n !== null))].sort();
  f.append(input("f-q", "q", "search", "Text in title, statement or keywords"),
    select_("f-kind", "kind", KINDS, "any kind"),
    select_("f-index", "index", [...indexes, ["(none)", "no index"]], "any index"),
    select_("f-state", "state", STATES, "any state"),
    select_("f-via", "via", VIA.map(([k, l]) => [k, l]), "anyone"),
    select_("f-vault", "vault", summary.vaults.map(v => v.name), "every vault"),
    input("f-session", "session", "text", "Session id"),
    input("f-from", "from", "date", "From"), input("f-to", "to", "date", "To"),
    select_("f-sort", "sort", SORTS, "sort"));
  const [sc, body] = table(ENTRY_HEADERS);
  const count = el("div", "sub"), pager = el("div", "pager");
  c.append(f, sc, count, pager); main.append(c);
  load = () => {
    count.textContent = "Loading…";
    api("entries?" + query(filter)).then(data => {
      body.replaceChildren(...data.rows.map(entryRow));
      const pages = Math.max(1, Math.ceil(data.total / data.page_size));
      count.textContent = data.total + " entries match" + (filter.session === "(unknown)" ? " (unknown origin)" : "") +
        "; page " + data.page + " of " + pages;
      const bad = failed(data.vaults); if (bad) count.append(" ", bad);
      pager.replaceChildren();
      if (data.page > 1) { const p = el("button", "plain", "Previous"); p.type = "button"; p.onclick = () => { filter.page--; load(); }; pager.append(p); }
      if (data.page < pages) { const n = el("button", "plain", "Next"); n.type = "button"; n.onclick = () => { filter.page++; load(); }; pager.append(n); }
    }).catch(err => failure(count, err));
  };
  load();
}

// ---------- sessions ----------
function sessions() {
  const c = card("Sessions that wrote to these vaults"); main.append(c);
  api("sessions").then(data => {
    const [sc, body] = table(["Session", "Project", "Machine", "Days", "Entries", "Written by", "Superseded since",
      "Withdrawn since", "Extractor cost recorded on this machine"]);
    data.sessions.forEach(s => {
      const tr = el("tr"); tr.tabIndex = 0;
      const open = () => { resetFilter(); filter.session = s.session === null ? "(unknown)" : s.session; go("entries"); };
      tr.onclick = open; tr.onkeydown = ev => { if (ev.key === "Enter") open(); };
      const via = Object.entries(s.via).map(([k, n]) => (VIA_LABEL[k] || k) + " " + n).join(", ");
      const x = s.extractor;
      const cost = x ? "$" + x.cost_usd.toFixed(4) + " · " + x.calls + " calls · " + x.runs + " runs · " + x.skipped + " records skipped as already processed"
        : "not in this machine's log";
      tr.append(el("td", "ref", s.session === null ? "unknown origin" : s.session), el("td", "", s.project), el("td", "", s.machine),
        el("td", "", day(s.first) + (s.days.length > 1 ? " – " + day(s.last) + " (" + s.days.length + " days)" : "")),
        el("td", "", s.entries), el("td", "", via), el("td", "", s.superseded), el("td", "", s.withdrawn), el("td", "", cost));
      body.append(tr);
    });
    c.append(sc, el("div", "sub", "Cost comes from the drainer's finished runs on this machine (extract.log): no manual val3 extract, no failed attempt, no cost per entry." +
      (data.extractor_log ? "" : " This machine has no extractor log.")));
    const bad = failed(data.vaults); if (bad) c.append(bad);
  }).catch(err => failure(c, err));
}

// ---------- names ----------
function names() {
  const c = card("Exact names shared by entries"); main.append(c);
  c.append(el("div", "sub", "Suggestions: words of at least 4 characters holding a digit, underscore, dot, slash or hyphen, not dates or plain numbers, found in titles and statements of two entries or more. No model involved."));
  const f = el("div", "filters"), box = el("input"); box.type = "search"; box.placeholder = "Find a name (also in keywords and evidence)";
  box.setAttribute("aria-label", box.placeholder); box.value = nameQuery;
  box.onchange = () => { nameQuery = box.value; render(); };
  f.append(box); c.append(f);
  if (nameQuery) {
    api("names?" + query({ q: nameQuery })).then(data => {
      const [sc, body] = table(ENTRY_HEADERS); body.append(...data.rows.map(entryRow));
      const back = el("button", "plain", "All names"); back.type = "button"; back.onclick = () => { nameQuery = ""; render(); };
      c.append(el("div", "sub", data.total + " entries contain this name" + (data.total > data.rows.length ? "; the newest " + data.rows.length + " shown" : "")), sc, back);
      const bad = failed(data.vaults); if (bad) c.append(bad);
    }).catch(err => failure(c, err));
    return;
  }
  api("names").then(data => {
    const w = el("div", "bars");
    if (!data.names.length) w.append(el("p", "muted", "No name is shared by two entries."));
    data.names.forEach(n => {
      const row = el("div");
      row.append(link(n.name, () => { nameQuery = n.name; render(); }),
        el("span", "muted", "  " + n.entries + " entries · " + n.not_live + " not live · last " + day(n.last)));
      w.append(row);
    });
    c.append(w);
    if (data.more) c.append(el("p", "sub", data.more + " more names not shown."));
    const bad = failed(data.vaults); if (bad) c.append(bad);
  }).catch(err => failure(c, err));
}

// ---------- entry panel ----------
function dl(pairs) {
  const d = el("dl"); pairs.forEach(([k, v]) => d.append(el("dt", "", k), el("dd", "", v))); return d;
}
function refs(label, list) {
  const p = el("p"); p.append(label + " ");
  list.forEach((r, k) => { p.append(link(r)); if (k < list.length - 1) p.append(", "); });
  return p;
}
function showPanel(data) {
  const e = data.entry; panel.replaceChildren();
  const top = el("div"); top.append(el("span", "ref", e.ref), " ", ...states(e));
  panel.append(top, el("h3", "", e.title), el("p", "", e.statement));
  if (e.keywords) panel.append(el("p", "muted", "keywords: " + e.keywords));
  panel.append(dl([["Kind", e.kind], ["Indexes", e.indexes.join(", ") || "none"], ["Recorded", e.recorded.replace("T", " ")],
    ["Written by", e.via ? (VIA_LABEL[e.via] || e.via) : "not recorded"], ["Session", e.session === null ? "unknown" : e.session],
    ["Machine", e.machine], ["Project", e.project], ["Vault", e.vault + " (" + e.role + ")"],
    ["Returned", e.returned === null ? "not recorded on this machine" : (e.returned === 0 ? "not seen" : e.returned + " times") + " by a search on this machine since " + (since() || "the log began")],
    ["Read", e.read === null ? "not recorded on this machine" : e.read + " times with show or memory_read"]]));
  panel.append(el("h2", "", "Evidence"));
  if (!e.evidence_items.length) panel.append(el("p", "muted", e.approved_by_person ? "None: approved by a person." : "None."));
  e.evidence_items.forEach(x => {
    panel.append(el("h4", "", x.kind + (x.recorded ? " · " + day(x.recorded) : "")));
    if (x.reproduction) panel.append(el("div", "ev", "$ " + x.reproduction + (x.reproduction_cut ? "\n… cut at 4 000 characters" : "")));
    panel.append(el("div", "ev", x.quote + (x.quote_cut ? "\n… cut at 4 000 characters" : "")));
  });
  panel.append(el("h2", "", "Relations"));
  let any = false;
  if (e.supersedes.length) { any = true; panel.append(refs("supersedes", e.supersedes.map(s => s.ref))); }
  if (e.superseded_by.length) { any = true; panel.append(refs("superseded by", e.superseded_by.map(s => s.ref))); }
  if (e.withdrawal) {
    any = true; const w = e.withdrawal;
    panel.append(el("p", "bad-text", "withdrawn (" + w.cause + ") " + day(w.at) + " by " + w.by + ": " + w.reason));
    (w.evidence || []).forEach(x => {
      panel.append(el("h4", "", "withdrawal evidence · " + x.kind));
      if (x.reproduction) panel.append(el("div", "ev", "$ " + x.reproduction));
      panel.append(el("div", "ev", x.quote));
      const calls = Array.isArray(x.calls) ? x.calls : [];
      if (calls.length) {
        const same = calls.filter(c => c.reproduction_is_command === true).length, n = calls.length + (x.more_calls || 0);
        panel.append(el("p", "muted", "printed by " + (n === 1 ? "" : n + " calls: ") + calls.map(c => typeof c.tool === "string" && /^[A-Za-z0-9_.:!-]{1,128}$/.test(c.tool) ? c.tool : "?").join(", ") + (x.more_calls ? ", " + x.more_calls + " more not listed" : "")
          + (same ? " (" + (n === 1 ? "" : same + " ran ") + "the command above, checked)" : " (the line above is a description, not checked)")));
      }
    });
  }
  if (e.steps.length) {
    any = true; const ol = el("ol");
    e.steps.forEach(s => {
      const li = el("li", "", s.action);
      if (s.facts.length) li.append(refs(" · cites", s.facts));
      if (s.tool) li.append(refs(" · tool", [s.tool]));
      ol.append(li);
    });
    panel.append(el("h4", "", "Steps"), ol);
  }
  if (e.cited_by.length) { any = true; panel.append(refs("cited by recipe", e.cited_by)); }
  if (e.close_pairs.length) {
    any = true; panel.append(el("h4", "", "Recorded close pairs"));
    e.close_pairs.forEach(p => { const line = refs("close to", [p.ref]); line.append(" (" + p.score + ") · " + (p.outcome ? "settled: " + p.outcome + " by " + p.settled_by : "not settled")); panel.append(line); });
  }
  const near = mapData && mapData.vault.ok ? mapData.vault.edges.filter(x => x.type === "near" && x.from === e.ref).map(x => x.to) : [];
  if (near.length) { any = true; panel.append(refs("nearest by vector (map)", near)); }
  if (!any) panel.append(el("p", "muted", "No relation recorded."));
  if (e.decisions.length) {
    panel.append(el("h4", "", "Decisions"));
    e.decisions.forEach(d => panel.append(el("p", "event", "review #" + d.review + " (" + d.reason + "): " + d.decision + " by " + d.by + " " + day(d.at))));
  }
  panel.append(el("h2", "", "History"));
  if (!e.events.length) panel.append(el("p", "muted", "No event recorded."));
  e.events.forEach(ev => panel.append(el("p", "event", ev.at.replace("T", " ") + " · " + ev.action + " · " + (ev.session || "no session") + " · " + ev.machine)));
}
function select(ref) {
  selected = ref;
  panel.replaceChildren(el("p", "muted", "Loading " + ref + "…"));
  api("entry?" + query({ ref })).then(showPanel).catch(err => failure(panel, err));
  if (view === "map" && redraw) redraw();
  main.querySelectorAll("tbody tr").forEach(tr => tr.classList.toggle("sel", tr.firstChild && tr.firstChild.textContent === ref));
}

// ---------- views ----------
Object.entries(VIEWS).forEach(([k, label]) => {
  const b = el("button", "", label); b.id = "tab-" + k; b.type = "button"; b.setAttribute("role", "tab");
  b.onclick = () => go(k); tabs.append(b);
});
function go(k) { if (location.hash === "#" + k) render(); else location.hash = k; }
function render() {
  view = VIEWS[location.hash.slice(1)] ? location.hash.slice(1) : "overview";
  [...tabs.children].forEach(b => b.setAttribute("aria-selected", String(b.id === "tab-" + view)));
  main.replaceChildren();
  if (!summary) return;
  ({ overview, map, entries, sessions, names })[view]();
}
function start() {
  mapData = null; nodes = null;
  main.replaceChildren(el("p", "muted", "Loading…"));
  Promise.all([api("summary"), api("usage").catch(() => null)])
    .then(([data, used]) => { summary = data; usage = used; header(); render(); }).catch(err => failure(main, err));
}
document.getElementById("reload").onclick = start;
addEventListener("hashchange", render);
addEventListener("resize", () => { if (view === "map" && redraw) redraw(); });
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (view === "map" && redraw) redraw(); });
start();
