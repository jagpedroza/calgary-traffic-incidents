/* Calgary Traffic Incidents — frontend (vanilla JS + Plotly). Data from /api/dashboard. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CENTER = { lat: 51.045, lon: -114.06 };
const CATEGORY_COLORS = {
  "Traffic incident": "#8a94a6",
  "Two-vehicle": "#2f6fdf",
  "Multi-vehicle": "#7b3fd6",
  "Single vehicle": "#1fa3a3",
  "Pedestrian": "#e0332b",
  "Cyclist": "#f08a1c",
  "Stalled vehicle": "#c9a227",
  "Traffic signals": "#2e9d4f",
  "Truck / bus": "#8d5a3b",
  "Debris / road hazard": "#d4549b",
  "Other": "#b7bcc6",
};
const GRAIN_LABEL = { hour: "per hour", day: "per day", week: "per week", month: "per month" };

const state = {
  meta: null,
  preset: "Today",
  start: "",
  end: "",
  quadrants: new Set(),
  categories: new Set(),
  search: "",
  blocking: false,
  mapMode: "points",
  data: null,
};
let inflight = null;

// ------------------------------------------------------------------ helpers
const fmt = (n, d = 0) => Number(n || 0).toLocaleString("en-CA", { maximumFractionDigits: d, minimumFractionDigits: d });
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const isDark = () => matchMedia("(prefers-color-scheme: dark)").matches;
const color = (cat) => CATEGORY_COLORS[cat] || "#999";
const dateLabel = (iso) => new Date(iso + "T12:00:00").toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" });
const timeLabel = (iso) => {
  const d = new Date(iso);
  return d.toLocaleString("en-CA", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false });
};

function baseLayout(extra = {}) {
  return {
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "Inter, system-ui, sans-serif", color: css("--text"), size: 12 },
    margin: { l: 44, r: 10, t: 6, b: 36 },
    xaxis: { gridcolor: css("--border"), zerolinecolor: css("--border") },
    yaxis: { gridcolor: css("--border"), zerolinecolor: css("--border") },
    showlegend: false,
    hoverlabel: { font: { family: "Inter, system-ui, sans-serif" } },
    ...extra,
  };
}
const CONFIG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["lasso2d", "select2d"] };

// ------------------------------------------------------------------ URL state
function readUrl() {
  const p = new URLSearchParams(location.search);
  if (p.get("preset") && state.meta.presets.includes(p.get("preset"))) state.preset = p.get("preset");
  state.start = p.get("start") || "";
  state.end = p.get("end") || "";
  if (p.has("quadrants")) state.quadrants = new Set(p.get("quadrants").split(",").filter(Boolean));
  if (p.has("categories")) state.categories = new Set(p.get("categories").split(",").filter(Boolean));
  state.search = p.get("search") || "";
  state.blocking = p.get("blocking") === "true";
  if (p.get("map") === "heat") state.mapMode = "heat";
}

function apiParams() {
  const p = new URLSearchParams({ preset: state.preset });
  if (state.preset === "Custom") { p.set("start", state.start); p.set("end", state.end); }
  if (state.quadrants.size !== state.meta.quadrants.length) p.set("quadrants", [...state.quadrants].join(","));
  if (state.categories.size !== state.meta.categories.length) p.set("categories", [...state.categories].join(","));
  if (state.search.trim()) p.set("search", state.search.trim());
  if (state.blocking) p.set("blocking", "true");
  return p;
}

function writeUrl() {
  const p = apiParams();
  if (state.mapMode === "heat") p.set("map", "heat");
  if (state.preset === "Today" && [...p.keys()].length === 1) p.delete("preset");
  const qs = p.toString();
  history.replaceState(null, "", qs ? `?${qs}` : location.pathname);
}

// ------------------------------------------------------------------ controls
function renderControls() {
  const m = state.meta;
  $("#presets").innerHTML = m.presets.map((p) =>
    `<button type="button" data-preset="${esc(p)}" aria-pressed="${p === state.preset}">${esc(p)}</button>`).join("");
  $("#quadrants").innerHTML = m.quadrants.map((q) =>
    `<button type="button" data-q="${esc(q)}" aria-pressed="${state.quadrants.has(q)}">${esc(q)}</button>`).join("");
  $("#categories").innerHTML = m.categories.map((c) =>
    `<label><input type="checkbox" value="${esc(c)}" ${state.categories.has(c) ? "checked" : ""}>
       <span class="tag" style="background:${color(c)}22;color:inherit;border-left:3px solid ${color(c)}">${esc(c)}</span></label>`).join("");
  const nCat = state.categories.size;
  $("#cat-summary").textContent = nCat === m.categories.length ? "All" : nCat === 0 ? "None" : `${nCat} selected`;
  $("#custom-range").hidden = state.preset !== "Custom";
  $("#start").value = state.start; $("#end").value = state.end;
  $("#start").min = $("#end").min = m.first_date;
  $("#start").max = $("#end").max = m.today;
  $("#search").value = state.search;
  $("#blocking").checked = state.blocking;
  document.querySelectorAll("#map-mode button").forEach((b) => b.setAttribute("aria-pressed", b.dataset.mode === state.mapMode));
}

function bindControls() {
  $("#presets").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.preset = b.dataset.preset;
    if (state.preset === "Custom" && !state.start) {
      state.end = state.meta.today; state.start = state.meta.today.slice(0, 8) + "01";
    }
    renderControls(); load();
  });
  $("#apply-range").addEventListener("click", () => {
    state.start = $("#start").value; state.end = $("#end").value;
    if (state.start && state.end) load();
  });
  $("#quadrants").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    const q = b.dataset.q;
    state.quadrants.has(q) ? state.quadrants.delete(q) : state.quadrants.add(q);
    renderControls(); load();
  });
  $("#categories").addEventListener("change", (e) => {
    e.target.checked ? state.categories.add(e.target.value) : state.categories.delete(e.target.value);
    renderControls(); load();
  });
  document.querySelectorAll("[data-cats]").forEach((b) => b.addEventListener("click", () => {
    state.categories = b.dataset.cats === "all" ? new Set(state.meta.categories) : new Set();
    renderControls(); load();
  }));
  document.addEventListener("click", (e) => {
    const dd = $("#cat-dropdown");
    if (dd.open && !dd.contains(e.target)) dd.open = false;
  });
  let t;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(t);
    t = setTimeout(() => { state.search = e.target.value; load(); }, 350);
  });
  $("#blocking").addEventListener("change", (e) => { state.blocking = e.target.checked; load(); });
  $("#map-mode").addEventListener("click", (e) => {
    const b = e.target.closest("button"); if (!b) return;
    state.mapMode = b.dataset.mode; renderControls(); writeUrl(); if (state.data) drawMap(state.data);
  });
  $("#csv").addEventListener("click", downloadCsv);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => state.data && draw(state.data));
}

// ------------------------------------------------------------------ data
async function load() {
  writeUrl();
  if (state.quadrants.size === 0 || state.categories.size === 0) {
    showNotice("Select at least one quadrant and one incident type.");
    return;
  }
  inflight?.abort();
  inflight = new AbortController();
  document.body.classList.add("loading");
  $("#period-label").textContent = "Loading…";
  try {
    const res = await fetch(`/api/dashboard?${apiParams()}`, { signal: inflight.signal });
    if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
    state.data = await res.json();
    draw(state.data);
  } catch (err) {
    if (err.name === "AbortError") return;
    showNotice(`Could not load data: ${err.message}`);
    $("#period-label").textContent = "";
  } finally {
    document.body.classList.remove("loading");
  }
}

function showNotice(msg) {
  const n = $("#notice");
  n.hidden = !msg; n.textContent = msg || "";
}

// ------------------------------------------------------------------ rendering
function draw(d) {
  const { period, kpis } = d;
  const range = period.start === period.end ? dateLabel(period.start) : `${dateLabel(period.start)} – ${dateLabel(period.end)}`;
  $("#period-label").textContent = `${period.preset} · ${range} · Calgary local time`;
  $("#grain-label").textContent = GRAIN_LABEL[period.grain];

  const cards = [
    ["Incidents", fmt(kpis.incidents)],
    ["Daily average", fmt(kpis.per_day, 1)],
    ["Vehicle collisions", fmt(kpis.collisions)],
    ["Pedestrians / cyclists", fmt(kpis.vulnerable)],
    ["Blocking lanes", `${fmt(kpis.pct_blocking)}%`],
    ["Latest", kpis.latest ? timeLabel(kpis.latest) : "—"],
  ];
  $("#kpis").innerHTML = cards.map(([l, v], i) =>
    `<div class="kpi"><div class="label">${l}</div><div class="value${i === cards.length - 1 ? " small" : ""}">${v}</div></div>`).join("");

  showNotice(kpis.incidents === 0
    ? (period.preset === "Today" ? "No incidents reported yet today. Try “Yesterday” or “Last 7 days”." : "No incidents match these filters.")
    : "");

  drawMap(d);
  drawTimeseries(d);
  drawHeatmap(d);
  drawCategories(d);
  drawTop(d);
  drawTable(d);
}

function drawMap(d) {
  const pts = d.points;
  const heat = state.mapMode === "heat";
  const trace = heat
    ? [{
        type: "densitymap", lat: pts.map((p) => p.lat), lon: pts.map((p) => p.lon), radius: pts.length > 2000 ? 7 : 14,
        colorscale: "YlOrRd", reversescale: false, showscale: false, opacity: 0.85,
        text: pts.map((p) => p.location), hovertemplate: "%{text}<extra></extra>",
      }]
    : Object.entries(groupBy(pts, (p) => p.category)).map(([cat, rows]) => ({
        type: "scattermap", mode: "markers", name: cat,
        lat: rows.map((p) => p.lat), lon: rows.map((p) => p.lon),
        marker: { size: pts.length > 2000 ? 5 : 10, color: color(cat), opacity: 0.85 },
        customdata: rows.map((p) => [p.location, timeLabel(p.time), p.description]),
        hovertemplate: "<b>%{customdata[0]}</b><br>%{customdata[1]}<br>%{customdata[2]}<extra>" + esc(cat) + "</extra>",
      }));
  Plotly.react("map", trace, {
    ...baseLayout({ margin: { l: 0, r: 0, t: 0, b: 0 } }),
    map: { style: isDark() ? "carto-darkmatter" : "carto-positron", center: CENTER, zoom: 9.7 },
    showlegend: !heat,
    legend: { orientation: "h", x: 0, y: 1, bgcolor: isDark() ? "rgba(26,29,35,.85)" : "rgba(255,255,255,.85)" },
  }, CONFIG);
}

function drawTimeseries(d) {
  const byCat = groupBy(d.timeseries, (r) => r.category);
  const traces = Object.entries(byCat).map(([cat, rows]) => ({
    type: "bar", name: cat, x: rows.map((r) => r.period), y: rows.map((r) => r.incidents),
    marker: { color: color(cat) }, hovertemplate: `${esc(cat)}: %{y}<extra></extra>`,
  }));
  Plotly.react("timeseries", traces, baseLayout({
    barmode: "stack", bargap: 0.15, showlegend: true,
    legend: { orientation: "h", y: -0.18 }, margin: { l: 44, r: 10, t: 6, b: 10 },
    xaxis: { type: "date", gridcolor: "rgba(0,0,0,0)" },
    yaxis: { gridcolor: css("--border"), title: { text: "Incidents" } },
  }), CONFIG);
}

function drawHeatmap(d) {
  const z = WEEKDAYS.map(() => Array(24).fill(null)); // empty cells show the background
  d.hour_weekday.forEach((r) => { z[r.weekday][r.hour] = r.incidents; });
  Plotly.react("heatmap", [{
    type: "heatmap", z, x: [...Array(24).keys()], y: WEEKDAYS, colorscale: "OrRd", showscale: false,
    hovertemplate: "%{y} %{x}:00 — %{z} incidents<extra></extra>", xgap: 1, ygap: 1,
  }], baseLayout({
    xaxis: { title: { text: "Hour" }, dtick: 3, gridcolor: "rgba(0,0,0,0)" },
    yaxis: { autorange: "reversed", gridcolor: "rgba(0,0,0,0)" },
  }), CONFIG);
}

function drawCategories(d) {
  const rows = [...d.by_category].reverse();
  Plotly.react("categories-chart", [{
    type: "bar", orientation: "h", y: rows.map((r) => r.category), x: rows.map((r) => r.incidents),
    marker: { color: rows.map((r) => color(r.category)) }, text: rows.map((r) => fmt(r.incidents)),
    textposition: "auto", hovertemplate: "%{y}: %{x}<extra></extra>",
  }], baseLayout({ margin: { l: 140, r: 10, t: 6, b: 30 }, yaxis: { automargin: true } }), CONFIG);
}

function drawTop(d) {
  const rows = [...d.top_locations].reverse();
  Plotly.react("top", [{
    type: "bar", orientation: "h", y: rows.map((r) => r.location), x: rows.map((r) => r.incidents),
    marker: { color: css("--accent") },
    customdata: rows.map((r) => [r.quadrant, r.pedestrians_cyclists]),
    hovertemplate: "<b>%{y}</b><br>%{x} incidents · %{customdata[0]}<br>Pedestrians/cyclists: %{customdata[1]}<extra></extra>",
  }], baseLayout({
    margin: { l: 10, r: 10, t: 6, b: 30 },
    yaxis: { automargin: true, tickfont: { size: 11 } },
    xaxis: { dtick: rows.length && rows[rows.length - 1].incidents < 6 ? 1 : undefined, gridcolor: css("--border") },
  }), CONFIG);
}

function drawTable(d) {
  const head = "<thead><tr><th>Time</th><th>Location</th><th>Type</th><th>Description</th></tr></thead>";
  const body = d.recent.slice(0, 200).map((r) => `<tr>
      <td class="time">${esc(timeLabel(r.local_time))}</td>
      <td>${esc(r.location)} <span class="muted">${esc(r.quadrant)}</span></td>
      <td><span class="tag" style="border-left:3px solid ${color(r.category)}">${esc(r.category)}</span></td>
      <td>${esc(r.description)}</td></tr>`).join("");
  $("#recent").innerHTML = head + `<tbody>${body || '<tr><td colspan="4" class="muted">No incidents.</td></tr>'}</tbody>`;
}

function downloadCsv() {
  if (!state.data) return;
  const cols = ["local_time", "location", "quadrant", "category", "description", "latitude", "longitude"];
  const q = (v) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const csv = [cols.join(","), ...state.data.recent.map((r) => cols.map((c) => q(r[c])).join(","))].join("\n");
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([csv], { type: "text/csv" }));
  a.download = `calgary-traffic-incidents-${state.data.period.start}_${state.data.period.end}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function groupBy(arr, fn) {
  return arr.reduce((acc, x) => ((acc[fn(x)] ||= []).push(x), acc), {});
}

// ------------------------------------------------------------------ boot
async function init() {
  try {
    state.meta = await (await fetch("/api/meta")).json();
  } catch {
    showNotice("Could not reach the API.");
    return;
  }
  state.preset = state.meta.default_preset;
  state.quadrants = new Set(state.meta.quadrants);
  state.categories = new Set(state.meta.categories);
  readUrl();
  renderControls();
  bindControls();
  load();
}
init();
