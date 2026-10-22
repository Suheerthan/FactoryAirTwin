"use strict";
/* FactoryAir Twin: app shell, plant overview, Leak Hunter, self-calibration
   and capex advisors. Reuses $, t, tf, fmt, inr, api, api2, ctx, stop and
   refreshTickets from app.js (loaded first). */

const SVGNS = "http://www.w3.org/2000/svg";
const twin = {overview: null, longrun: null, validation: null, layout: null, seed: 11,
              open: new Set(), traces: [], survey: null, testIndex: 0, loaded: {}};
const TRACE_COLOURS = ["#0f2730", "#1c6a96", "#17845a", "#b06d00", "#8a3ea8", "#b93a2a"];

function el(tag, attrs = {}, text = "") {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text !== "") e.textContent = text;
  return e;
}
function h(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "html") e.innerHTML = v; else e.setAttribute(k, v);
  }
  children.flat().forEach(c => e.append(c instanceof Node ? c : document.createTextNode(String(c))));
  return e;
}
const rupees = v => v == null ? "—" : `₹${inr(Math.round(v))}`;
async function getJSON(url) {
  const r = await fetch(url);
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `Request failed (${r.status}).`);
  return d;
}

/* ================================================================ router */
const VIEWS = ["overview", "live", "leaks", "gateway", "mydata", "calibration", "savings", "maintenance", "trust", "settings"];
function showView(name) {
  if (!VIEWS.includes(name)) name = "overview";
  document.querySelectorAll(".view").forEach(v => { v.hidden = v.dataset.view !== name; });
  document.querySelectorAll(".nav a").forEach(a => a.classList.toggle("active", a.dataset.view === name));
  if (name !== "live" && typeof stop === "function") stop();
  if (name === "leaks" && !twin.loaded.leaks) { twin.loaded.leaks = true; loadLayout(); }
  if (name === "calibration" && !twin.loaded.cal) { twin.loaded.cal = true; loadCalibration(); }
  if (name === "savings" && !twin.loaded.savings) { twin.loaded.savings = true; runHeat(); runVsd(); }
  if (typeof window.onViewShown === "function") window.onViewShown(name);
  window.scrollTo({top: 0});
}
function route() {
  const hash = location.hash.replace("#", "");
  const legacy = {model: "trust", validation: "trust", connectivity: "settings", optimiser: "savings", stage4: "maintenance"};
  showView(legacy[hash] || hash || "overview");
}
window.addEventListener("hashchange", route);

/* ================================================================ overview */
async function loadOverview() {
  try {
    twin.overview = await getJSON("/api/overview");
    renderOverview();
  } catch (error) {
    $("ov-sub").textContent = error.message;
  }
}

function renderOverview() {
  const o = twin.overview;
  if (!o) return;
  const s = o.summary, L = o.ladder;
  if (o.site) $("ov-site").textContent = o.site;
  $("ov-waste").textContent = rupees(L.electricity_inr_month);
  $("ov-sub").textContent = tf("ov.sub", fmt(L.electricity_share_of_bill_pct, 0), inr(s.monthly_cost_inr));
  $("ov-bill").textContent = rupees(s.monthly_cost_inr);
  $("ov-leakpct").textContent = s.latest_leak_pct == null ? "—" : `${fmt(s.latest_leak_pct, 1)}%`;
  const verified = o.events.find(e => e.kind === "leak_reduced");
  $("ov-verified").textContent = verified ? rupees(verified.inr_per_month) : "—";
  $("ov-fuel").textContent = rupees(L.fuel_inr_month);
  drawLeakIndex($("ov-leak-chart"), o, true);
  drawDaily(o);
  renderStory(o);
  renderLadder(L);
  renderWhatsApp(o);
  setTicketBadge(o.open_tickets);
}

function nightSeries(o) {
  const nights = (twin.longrun && twin.longrun.nights) || null;
  return o.daily.filter(d => d.leak_pct_tonight != null).map(d => ({x: d.day, y: d.leak_pct_tonight,
    label: nights ? nights[d.day]?.label : `${d.weekday}`}));
}

function drawLeakIndex(svg, o, dark) {
  svg.replaceChildren();
  const W = 520, H = 220, L = 40, R = 14, T = 16, B = 30;
  const pts = nightSeries(o);
  if (!pts.length) return;
  const maxY = Math.max(5, Math.ceil(Math.max(...pts.map(p => p.y)) / 5) * 5);
  const x = i => L + i / 13 * (W - L - R);
  const y = v => H - B - v / maxY * (H - T - B);
  const gridC = dark ? "#1d3b45" : "#e2ddd1", textC = dark ? "#9fbcc4" : "#5a6a6f";
  for (let v = 0; v <= maxY; v += maxY / 4) {
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: gridC}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end", fill: textC}, `${Math.round(v)}%`));
  }
  o.daily.forEach(d => {
    if (d.day % 2 === 0) svg.append(el("text", {x: x(d.day), y: H - 10, "text-anchor": "middle", fill: textC}, `${t(d.weekday)} ${d.day + 1}`));
  });
  (o.events || []).forEach(e => {
    const colour = e.kind === "new_leak" ? "#f2a516" : "#33c48a";
    svg.append(el("line", {x1: x(e.night), x2: x(e.night), y1: T, y2: H - B, stroke: colour, "stroke-dasharray": "4 4", "stroke-width": 1.5}));
    svg.append(el("text", {x: x(e.night) + (e.kind === "new_leak" ? -6 : 6), y: T + 10, fill: colour,
      "text-anchor": e.kind === "new_leak" ? "end" : "start", "font-weight": 700},
      e.kind === "new_leak" ? t("new leak") : t("repair verified")));
  });
  const d = pts.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const area = `${d} L${x(pts.at(-1).x).toFixed(1)},${y(0)} L${x(pts[0].x).toFixed(1)},${y(0)} Z`;
  svg.append(el("path", {d: area, fill: dark ? "#7fc4ea22" : "#7fc4ea33"}));
  svg.append(el("path", {d, fill: "none", stroke: "#7fc4ea", "stroke-width": 3, "stroke-linejoin": "round"}));
  pts.forEach(p => {
    const c = el("circle", {cx: x(p.x), cy: y(p.y), r: 4.5, fill: dark ? "#0b2129" : "#fff", stroke: "#7fc4ea", "stroke-width": 2.5});
    c.append(el("title", {}, `${p.label}: ${fmt(p.y, 1)}%`));
    svg.append(c);
  });
}

function drawDaily(o) {
  const svg = $("ov-daily-chart");
  svg.replaceChildren();
  const W = 620, H = 220, L = 42, R = 10, T = 12, B = 30;
  const maxY = Math.ceil(Math.max(...o.daily.map(d => d.kwh)) / 20) * 20 || 20;
  const bw = (W - L - R) / o.daily.length;
  const y = v => H - B - v / maxY * (H - T - B);
  for (let v = 0; v <= maxY; v += maxY / 4) {
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end"}, Math.round(v)));
  }
  svg.append(el("text", {x: 4, y: T + 2, "font-size": 11}, "kWh"));
  o.daily.forEach((d, i) => {
    const x0 = L + i * bw + bw * 0.16, w = bw * 0.68;
    const leak = Math.min(d.leak_kwh_per_day || 0, d.kwh), unl = Math.min(d.unloaded_kwh, d.kwh - leak);
    const rest = Math.max(0, d.kwh - leak - unl);
    let top = y(0);
    [[rest, "#5d7b84"], [unl, "#c5c0b4"], [leak, "#f2a516"]].forEach(([v, c]) => {
      const hgt = y(0) - y(v);
      if (hgt > 0) { svg.append(el("rect", {x: x0, y: top - hgt, width: w, height: hgt, fill: c})); top -= hgt; }
    });
    const g = el("rect", {x: L + i * bw, y: T, width: bw, height: H - T - B, fill: "transparent"});
    g.append(el("title", {}, `${d.weekday} ${d.day + 1}: ${fmt(d.kwh, 1)} kWh · ₹${inr(d.cost_inr)} · ${tf("leak.tip", fmt(d.leak_kwh_per_day || 0, 1))}`));
    svg.append(g);
    svg.append(el("text", {x: L + i * bw + bw / 2, y: H - 10, "text-anchor": "middle", "font-size": 11}, t(d.weekday).slice(0, 3)));
  });
}

function renderStory(o) {
  const list = $("ov-story");
  list.replaceChildren();
  const id = o.identified;
  list.append(h("li", {}, h("span", {class: "when"}, tf("story.when", t("Mon"), 1)),
    h("strong", {}, t("Twin calibrated itself from the first quiet night")),
    tf("story.calib", fmt(id.effective_volume_m3, 2), fmt(id.nameplate_receiver_m3, 2), fmt(id.specific_power_kw_per_m3_min, 2))));
  o.events.forEach(e => {
    const night = o.daily[e.night];
    const cls = e.kind === "new_leak" ? "bad" : "good";
    list.append(h("li", {class: cls}, h("span", {class: "when"}, tf("story.night", t(night.weekday), e.night + 1)),
      h("strong", {}, t(e.title)),
      e.kind === "new_leak" ? tf("story.leak", fmt(Math.abs(e.delta_m3_min), 3)) : tf("story.fixed", fmt(Math.abs(e.delta_m3_min), 3)),
      " ", h("span", {class: "amt"}, `${e.kind === "new_leak" ? "+" : "−"}${rupees(e.inr_per_month)}/${t("month")}`)));
  });
  const last = o.daily.filter(d => d.leak_pct_tonight != null).at(-1);
  if (last) list.append(h("li", {}, h("span", {class: "when"}, tf("story.night", t(last.weekday), last.day + 1)),
    h("strong", {}, t("Background leakage still growing slowly")),
    tf("story.now", fmt(last.leak_pct_tonight, 1), inr(o.summary.latest_leak_inr_month))));
}

function renderLadder(L) {
  const box = $("ov-ladder");
  box.replaceChildren();
  const max = Math.max(...L.items.map(i => i.inr_per_month));
  L.items.forEach(item => {
    const skip = item.payback_months != null && item.payback_months > 36;
    const tag = skip ? h("span", {class: "tag no"}, t("not worth it"))
      : item.kind === "fuel" ? h("span", {class: "tag fuel"}, t("fuel, not electricity"))
      : item.payback_months === 0 ? h("span", {class: "tag now"}, t("free · do today")) : "";
    const pay = item.payback_months == null ? "" : item.payback_months === 0 ? t("no cost") : tf("payback.m", fmt(item.payback_months, 1));
    box.append(h("div", {class: `rung ${item.kind === "fuel" ? "fuel" : ""} ${skip ? "skip" : ""}`},
      h("div", {class: "rung-title"}, t(item.title), tag, h("small", {}, ladderDetail(item))),
      h("div", {class: "rung-value"}, `${rupees(item.inr_per_month)}/${t("month")}`, h("small", {}, pay)),
      h("div", {class: "rung-bar"}, h("span", {style: `width:${Math.max(3, 100 * item.inr_per_month / max)}%`}))));
  });
  $("ov-ladder-note").textContent = t(L.note);
}

function ladderDetail(item) {
  if (LANG !== "ta") return item.detail;
  const map = {leak: "நேற்றிரவு சோதனையின்படி கசிவு இன்னும் உள்ளது. கசிவு வேட்டை மண்டலத்தைக் காட்டும்; இரவுச் சோதனை பழுதை உறுதிப்படுத்தும்.",
    unloaded: "மின்சாரத்தின் ஒரு பகுதி காற்றே தராமல் வீணாகிறது. நிறுத்த டைமரைக் குறைக்கவும்.",
    pressure: "உற்பத்தியின்போது அழுத்தம் தேவைக்கும் பாதுகாப்பு வரம்புக்கும் மேல் உள்ளது.",
    heat: "எண்ணெய் குளிரூட்டியிலிருந்து வெப்பத்தை எடுத்து 60 °C நீராக மாற்றி LPG-ஐ குறைக்கலாம்.",
    vsd: "சராசரி தேவை கொள்ளளவில் ஒரு பகுதி மட்டுமே; முதலீடு திரும்ப நீண்ட காலம் ஆகும்."};
  return map[item.key] || item.detail;
}

function renderWhatsApp(o) {
  const e = o.events.find(x => x.kind === "new_leak");
  const box = $("ov-whatsapp");
  if (!e) { box.textContent = t("No new-leak alert in this fortnight."); return; }
  const night = o.daily[e.night];
  const site = o.site || "Compressor room 01";
  box.innerHTML = tf("wa.msg", site, `${t(night.weekday)} ${e.night + 1}`, fmt(Math.abs(e.delta_m3_min), 3),
    inr(e.inr_per_month), t(o.daily[e.night + 1]?.weekday || night.weekday));
}

function setTicketBadge(n) {
  const b = $("nav-tickets");
  b.hidden = !n; b.textContent = n || "";
}
async function refreshTicketBadge() {
  try {
    const tickets = await getJSON("/api/maintenance/tickets");
    setTicketBadge(tickets.filter(x => !["resolved", "cancelled"].includes(x.state)).length);
  } catch (e) { /* badge is cosmetic */ }
}

/* ================================================================ Leak Hunter */
async function loadLayout() {
  try {
    twin.layout = await getJSON(`/api/leaks/layout?seed=${twin.seed}`);
    $("lh-seed").textContent = `#${twin.seed}`;
    const proc = $("lh-procedure");
    proc.replaceChildren(...twin.layout.procedure.map((step, i) => h("li", {}, t(step))));
    drawSchematic();
    drawDecay();
  } catch (error) { $("lh-test-result").textContent = error.message; }
}

function zoneShare(id) {
  if (!twin.survey) return null;
  return twin.survey.zones.find(z => z.id === id) || null;
}

function drawSchematic() {
  const svg = $("lh-schematic");
  svg.replaceChildren();
  const zones = twin.layout.zones;
  const AIR = "#4fa3d1", INK = "#0f2730";
  const shares = twin.survey ? twin.survey.zones : null;
  const maxShare = shares ? Math.max(...shares.map(z => z.share_pct)) : 1;
  // header main
  svg.append(el("line", {x1: 108, y1: 150, x2: 500, y2: 150, stroke: AIR, "stroke-width": 7, "stroke-linecap": "round"}));
  // compressor + receiver
  const comp = el("g", {});
  comp.append(el("rect", {x: 14, y: 118, width: 54, height: 64, rx: 6, fill: INK}));
  comp.append(el("circle", {cx: 41, cy: 150, r: 15, fill: "none", stroke: "#7fc4ea", "stroke-width": 3}));
  comp.append(el("path", {d: "M41 150l8-8", stroke: "#f2a516", "stroke-width": 3, "stroke-linecap": "round"}));
  comp.append(el("rect", {x: 76, y: 128, width: 32, height: 44, rx: 14, fill: "#fbfaf6", stroke: INK, "stroke-width": 2.5}));
  comp.append(el("text", {x: 14, y: 200, "font-size": 12, fill: INK, "font-weight": 600}, t("Compressor + receiver")));
  svg.append(comp);
  const header = zones.find(z => z.id === "header");
  if (shares) {
    const zh = zoneShare("header");
    svg.append(el("text", {x: 14, y: 216, "font-size": 12, fill: "#8f5600", "font-weight": 700}, `${t("header")} ${fmt(zh.share_pct, 0)}%`));
  }
  zones.filter(z => z.id !== "header").forEach(z => {
    const up = z.y < 150;
    const isOpen = twin.open.has(z.id);
    const pipeEnd = up ? z.y + 26 : z.y - 26;
    svg.append(el("line", {x1: z.x, y1: 150, x2: z.x, y2: pipeEnd, stroke: isOpen ? AIR : "#b9c8cd",
      "stroke-width": 6, "stroke-linecap": "round", "stroke-dasharray": isOpen ? "" : "2 7"}));
    // zone box
    const share = zoneShare(z.id);
    const intensity = share ? Math.min(1, share.share_pct / maxShare) : 0;
    const fill = share ? `rgba(242,165,22,${0.15 + 0.75 * intensity})` : "#fbfaf6";
    const bx = z.x - 74, by = up ? z.y - 34 : z.y - 18;
    svg.append(el("rect", {x: bx, y: by, width: 148, height: 52, rx: 8, fill, stroke: INK, "stroke-width": share && intensity === 1 ? 3 : 1.5}));
    const name = t(z.name);
    svg.append(el("text", {x: z.x, y: by + 21, "text-anchor": "middle", "font-size": 12.5, "font-weight": 600, fill: INK}, name.length > 24 ? name.slice(0, 23) + "…" : name));
    svg.append(el("text", {x: z.x, y: by + 39, "text-anchor": "middle", "font-size": 12, fill: share ? "#5a3600" : "#5a6a6f"},
      share ? `${fmt(share.share_pct, 0)}% · ₹${inr(share.inr_per_month)}/${t("month")}` : `${t("pipe")} ${fmt(z.pipe_m3 * 1000, 0)} L`));
    // valve (bow-tie) on the branch
    const vy = up ? 112 : 188;
    const g = el("g", {class: "valve", tabindex: 0, role: "button", "aria-pressed": String(isOpen),
      "aria-label": `${name}: ${isOpen ? t("valve open") : t("valve closed")}`});
    g.append(el("rect", {x: z.x - 22, y: vy - 16, width: 44, height: 32, fill: "transparent"}));
    g.append(el("path", {class: "valve-body", d: `M${z.x - 15},${vy - 10} L${z.x + 15},${vy + 10} L${z.x + 15},${vy - 10} L${z.x - 15},${vy + 10} Z`,
      fill: isOpen ? "#ffffff" : INK, stroke: INK, "stroke-width": 2, "stroke-linejoin": "round"}));
    g.append(el("text", {x: z.x + 22, y: vy + 4, "font-size": 11, fill: isOpen ? "#17845a" : "#5a6a6f", "font-weight": 600},
      isOpen ? t("OPEN") : t("CLOSED")));
    g.addEventListener("click", () => toggleValve(z.id));
    g.addEventListener("keydown", ev => { if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); toggleValve(z.id); } });
    svg.append(g);
  });
  svg.append(el("text", {x: 300, y: 292, "text-anchor": "middle", "font-size": 11.5, fill: "#5a6a6f"},
    t("Sky-blue lines carry compressed air (IS 2379 colour). Dashed = isolated.")));
  void header;
}

function toggleValve(id) {
  if (twin.open.has(id)) twin.open.delete(id); else twin.open.add(id);
  drawSchematic();
}

function drawDecay() {
  const svg = $("lh-decay");
  svg.replaceChildren();
  const W = 560, H = 240, L = 46, R = 14, T = 14, B = 32;
  const all = twin.traces.flatMap(tr => tr.trace);
  const minY = all.length ? Math.floor((Math.min(...all) - 0.05) * 10) / 10 : 6.0;
  const maxY = 7.3;
  const x = s => L + s / 120 * (W - L - R);
  const y = v => H - B - (v - minY) / (maxY - minY) * (H - T - B);
  for (let i = 0; i <= 4; i++) {
    const v = minY + (maxY - minY) * i / 4;
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end"}, fmt(v, 2)));
  }
  for (let s = 0; s <= 120; s += 30) svg.append(el("text", {x: x(s), y: H - 10, "text-anchor": "middle"}, `${s} s`));
  svg.append(el("text", {x: 4, y: T + 2, "font-size": 11}, "bar(g)"));
  // End-of-line labels, pushed apart so stacked traces stay readable.
  const labels = twin.traces.map((tr, i) => ({i, y: y(tr.trace.at(-1)) - 4}));
  labels.sort((a, b) => a.y - b.y);
  for (let k = 1; k < labels.length; k++) labels[k].y = Math.max(labels[k].y, labels[k - 1].y + 13);
  const overflow = labels.length ? labels.at(-1).y - (H - B - 2) : 0;
  if (overflow > 0) labels.forEach(l => { l.y -= overflow; });
  const labelY = Object.fromEntries(labels.map(l => [l.i, l.y]));
  twin.traces.forEach((tr, i) => {
    const colour = TRACE_COLOURS[i % TRACE_COLOURS.length];
    const d = tr.trace.map((v, s) => `${s ? "L" : "M"}${x(s).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
    svg.append(el("path", {d, fill: "none", stroke: colour, "stroke-width": 1.8, opacity: 0.9}));
    svg.append(el("text", {x: L + 8, y: labelY[i], "font-size": 11, fill: colour, "font-weight": 600,
      "paint-order": "stroke", stroke: "#fbfaf6", "stroke-width": 3}, tr.label));
  });
  if (!twin.traces.length) svg.append(el("text", {x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 13}, t("No test run yet")));
}

function branchLabel(ids) {
  if (!ids.length) return t("header only");
  const names = Object.fromEntries(twin.layout.zones.map(z => [z.id, t(z.name).split(" - ")[0]]));
  return "+ " + ids.map(i => names[i]).join(", ");
}

async function runDecayTest() {
  const open = [...twin.open];
  $("lh-test").disabled = true;
  try {
    const r = await api("/api/leaks/test", {seed: twin.seed, open_branches: open, test_index: twin.testIndex++});
    twin.traces.push({trace: r.trace_bar, label: branchLabel(open)});
    if (twin.traces.length > 6) twin.traces.shift();
    drawDecay();
    const sp = twin.longrun?.identified?.specific_power_kw_per_m3_min || 8.33;
    const tariff = twin.overview?.tariff_inr_kwh || 7;
    $("lh-test-result").textContent = tf("lh.test", branchLabel(open), fmt(r.drop_bar, 3), fmt(r.outflow_m3_min_at_7bar, 3),
      fmt(r.uncertainty_m3_min, 3), inr(Math.round(r.outflow_m3_min_at_7bar * sp * 24 * 30 * tariff)));
  } catch (error) { $("lh-test-result").textContent = error.message; }
  finally { $("lh-test").disabled = false; }
}

async function runSurvey() {
  $("lh-survey").disabled = true; $("lh-test").disabled = true;
  $("lh-test-result").textContent = t("Running the guided sequence: close all, then open one branch at a time…");
  try {
    const r = await api("/api/leaks/survey", {seed: twin.seed, reveal: $("lh-reveal").checked});
    twin.survey = null; twin.traces = []; twin.open.clear();
    const order = r.steps.map(s => s.open_branches);
    for (let i = 0; i < r.steps.length; i++) {
      twin.open = new Set(order[i]);
      twin.traces.push({trace: r.steps[i].trace_bar, label: i === 0 ? t("header only") : `+ ${t(twin.layout.zones.find(z => z.id === order[i].at(-1)).name).split(" - ")[0]}`});
      drawSchematic(); drawDecay();
      await new Promise(res => setTimeout(res, 420));
    }
    twin.survey = r;
    drawSchematic();
    renderAllocation(r);
    $("lh-test-result").textContent = tf("lh.surveydone", r.steps.length, r.duration_minutes, fmt(r.total_m3_min, 3), inr(r.total_inr_per_month));
  } catch (error) { $("lh-test-result").textContent = error.message; }
  finally { $("lh-survey").disabled = false; $("lh-test").disabled = false; }
}

function renderAllocation(r) {
  const box = $("lh-alloc");
  box.replaceChildren();
  const max = Math.max(...r.zones.map(z => z.est_m3_min), ...r.zones.map(z => z.validation_true_m3_min || 0));
  r.ranked.forEach((id, rank) => {
    const z = r.zones.find(x => x.id === id);
    const bar = h("div", {class: "alloc-bar"}, h("span", {style: `width:${100 * z.est_m3_min / max}%`}));
    if (z.validation_true_m3_min != null) {
      const mark = h("i", {style: `left:calc(${100 * z.validation_true_m3_min / max}% - 1px)`});
      mark.title = `${t("hidden true leak")}: ${fmt(z.validation_true_m3_min, 4)} m³/min`;
      bar.append(mark);
    }
    box.append(h("div", {class: `alloc-row ${rank === 0 ? "top" : ""}`},
      h("span", {}, t(z.name)), bar,
      h("span", {class: "v"}, `${fmt(z.share_pct, 0)}% · ${rupees(z.inr_per_month)}/${t("month")}`,
        h("small", {}, `${fmt(z.est_m3_min, 3)} ± ${fmt(z.uncertainty_m3_min, 3)} m³/min`))));
  });
  if (r.revealed) box.append(h("p", {class: "note"}, t("Black ticks mark the hidden true leak per zone, shown only to grade the method.")));
  const worst = r.zones.find(z => z.id === r.ranked[0]);
  $("lh-recommend").hidden = false;
  $("lh-recommend").textContent = tf("lh.recommend", t(worst.name), fmt(worst.share_pct, 0), inr(worst.inr_per_month));
  $("lh-ticket").hidden = false;
  $("lh-ticket-result").textContent = "";
}

async function raiseZoneTicket() {
  const r = twin.survey;
  if (!r) return;
  const worst = r.zones.find(z => z.id === r.ranked[0]);
  try {
    const tk = await api2("/api/maintenance/tickets", {
      fault_type: "leak", title: `Leak in ${worst.name} (Leak Hunter)`,
      evidence: `Valve-isolation survey on plant #${r.seed}: ${worst.share_pct}% of measured leakage, ${worst.est_m3_min} m³/min. ${r.method}`,
      predicted: {inr_per_month: worst.inr_per_month, kwh_per_day: worst.kwh_per_day},
      leak_zone: {seed: r.seed, zone: worst.id}
    });
    $("lh-ticket-result").textContent = tf("lh.ticket", tk.id);
    refreshTickets(); refreshTicketBadge();
  } catch (error) { $("lh-ticket-result").textContent = error.message; }
}

/* ================================================================ calibration */
async function ensureLongrun() {
  if (!twin.longrun) twin.longrun = await getJSON("/api/twin/longrun");
  return twin.longrun;
}

async function loadCalibration() {
  try {
    const [lr, v] = await Promise.all([ensureLongrun(), getJSON("/api/twin/validation")]);
    twin.validation = v;
    renderCalibration(lr, v);
  } catch (error) { $("id-cards").textContent = error.message; }
}

function card(label, value, note, cls = "") {
  return h("div", {class: `id-card ${cls}`}, h("span", {}, label), h("strong", {}, value), h("small", {}, note));
}

function renderCalibration(lr, v) {
  const id = lr.identified, last = lr.nights.filter(n => n.ok).at(-1), curve = id.power_curve;
  $("id-cards").replaceChildren(
    card(t("Effective storage"), `${fmt(id.effective_volume_m3, 2)} m³`, tf("id.vol", fmt(id.nameplate_receiver_m3, 2))),
    card(t("Leak, last night"), `${fmt(last.leak_m3_min, 3)} m³/min`, tf("id.leak", fmt(last.leak_pct_of_capacity, 1)), "warn"),
    card(t("Specific power"), `${fmt(id.specific_power_kw_per_m3_min, 2)}`, tf("id.sp", fmt(id.nameplate_specific_power, 2))),
    card(t("Motor health"), `${fmt(curve.power_ratio_vs_nameplate * 100, 0)}%`, tf("id.curve", fmt(curve.kw_at_7bar, 2), t(curve.verdict)),
      curve.power_ratio_vs_nameplate > 1.05 ? "warn" : "ok"),
    card(t("Nights used"), `${id.nights_used}/13`, t("only confident nights feed the plant model"), "ok"));
  const body = $("id-nights");
  body.replaceChildren();
  const eventNights = new Set(lr.events.map(e => e.night));
  lr.nights.forEach(n => {
    const row = h("tr", {class: eventNights.has(n.night) ? "event-row" : ""},
      h("td", {}, translateLabel(n.label)), h("td", {}, n.complete_cycles ?? "—"),
      h("td", {}, n.leak_fraction_time == null ? "—" : `${fmt(n.leak_fraction_time * 100, 1)}%`),
      h("td", {}, n.leak_fraction_slope == null ? "—" : `${fmt(n.leak_fraction_slope * 100, 1)}%`),
      h("td", {}, fmt(n.leak_m3_min, 3)), h("td", {}, fmt(n.effective_volume_m3, 3)),
      h("td", {}, h("span", {class: `conf ${n.confidence}`}, t(n.confidence))),
      h("td", {}, fmt(n.validation_true_leak_m3_min, 3)));
    body.append(row);
  });
  $("val-cards").replaceChildren(
    card(t("Leak error"), `${fmt(v.leak_mean_abs_error_pct, 1)}%`, t("mean absolute, 30 unseen plants"), "ok"),
    card(t("Storage error"), `${fmt(v.volume_mean_abs_error_pct, 1)}%`, t("estimated, not typed in"), "ok"),
    card(t("Within ±10%"), `${v.within_10pct_leak}/${v.identified}`, t("plants with leak error under 10%"), "ok"));
  $("val-protocol").textContent = t(v.protocol);
  drawValidation(v);
}

function translateLabel(label) {
  if (LANG !== "ta") return label;
  return label.replace(/\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b/g, m => t(m));
}

function drawValidation(v) {
  const svg = $("val-chart");
  svg.replaceChildren();
  const W = 520, H = 240, L = 48, R = 14, T = 12, B = 34;
  const rows = v.rows.filter(r => r.ok);
  const maxV = Math.ceil(Math.max(...rows.map(r => Math.max(r.true_leak_m3_min, r.est_leak_m3_min))) * 20) / 20;
  const x = val => L + val / maxV * (W - L - R);
  const y = val => H - B - val / maxV * (H - T - B);
  for (let i = 0; i <= 4; i++) {
    const val = maxV * i / 4;
    svg.append(el("line", {x1: L, x2: W - R, y1: y(val), y2: y(val), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: y(val) + 4, "text-anchor": "end"}, fmt(val, 2)));
    svg.append(el("text", {x: x(val), y: H - 14, "text-anchor": "middle"}, fmt(val, 2)));
  }
  svg.append(el("path", {d: `M${x(0)},${y(0)} L${x(maxV)},${y(maxV * 1.1)} L${x(maxV)},${y(maxV * 0.9)} Z`, fill: "#17845a18"}));
  svg.append(el("line", {x1: x(0), y1: y(0), x2: x(maxV), y2: y(maxV), stroke: "#17845a", "stroke-dasharray": "5 4"}));
  rows.forEach(r => {
    const c = el("circle", {cx: x(r.true_leak_m3_min), cy: y(r.est_leak_m3_min), r: 5, fill: "#1c6a96", "fill-opacity": 0.75, stroke: "#fff"});
    c.append(el("title", {}, `${t("Plant")} ${r.plant}: ${t("true")} ${r.true_leak_m3_min} · ${t("estimated")} ${r.est_leak_m3_min} (${r.leak_error_pct}%)`));
    svg.append(c);
  });
  svg.append(el("text", {x: W - R, y: H - 2, "text-anchor": "end", "font-size": 11.5}, t("hidden true leak, m³/min →")));
  svg.append(el("text", {x: 6, y: T + 4, "font-size": 11.5}, t("↑ estimated")));
}

async function runIdentify() {
  $("id-result").textContent = t("Identifying…");
  try {
    const r = await api("/api/twin/identify", {source: "scenario", scenario: $("id-scenario").value});
    const lt = r.leak_test, pc = r.power_curve, un = r.unloaded;
    const parts = [];
    if (lt.ok) parts.push(tf("id.scen.leak", fmt(lt.leak_m3_min, 3), fmt(lt.leak_pct_of_capacity, 1), t(lt.method), t(lt.confidence)));
    else parts.push(t(lt.reason));
    if (pc) parts.push(tf("id.scen.curve", fmt(pc.power_ratio_vs_nameplate * 100, 0), t(pc.verdict)));
    if (un) parts.push(tf("id.scen.unl", fmt(un.unloaded_energy_share_pct, 1)));
    $("id-result").textContent = parts.join(" · ");
  } catch (error) { $("id-result").textContent = error.message; }
}

/* ================================================================ advisors */
async function runHeat() {
  const box = $("hr-result");
  box.textContent = t("Calculating…");
  try {
    const r = await api("/api/advisor/heat", {fuel: $("hr-fuel").value, usable_pct: Number($("hr-usable").value),
      kit_cost_inr: Number($("hr-cost").value)});
    box.innerHTML = tf("hr.result", inr(r.recovered_heat_kwh_month), inr(r.hot_water_litres_per_day), inr(r.inr_per_month),
      fmt(r.fuel_displaced_per_month, 0), t(r.fuel_unit), inr(r.co2_kg_month), r.payback_months == null ? "—" : fmt(r.payback_months, 1));
  } catch (error) { box.textContent = error.message; }
}

async function runVsd() {
  const box = $("vsd-result");
  box.textContent = t("Replaying 336 hours…");
  try {
    const r = await api("/api/advisor/vsd", {upgrade_cost_inr: Number($("vsd-cost").value),
      min_speed_pct: Number($("vsd-min").value), drive_loss_pct: Number($("vsd-loss").value)});
    const verdict = r.payback_months != null && r.payback_months <= 36 ? t("Worth a quote.") : t("Not worth it for this plant: fix leaks and the stop timer first.");
    box.innerHTML = tf("vsd.result", inr(r.fixed_speed_kwh_month), inr(r.vsd_kwh_month), fmt(r.saving_pct, 1), inr(r.inr_per_month),
      r.payback_months == null ? "—" : fmt(r.payback_months, 0), fmt(r.mean_flow_pct, 0)) + ` <strong>${verdict}</strong>`;
    drawVsd(r.profile_by_hour_of_day);
  } catch (error) { box.textContent = error.message; }
}

function drawVsd(profile) {
  const svg = $("vsd-chart");
  svg.replaceChildren();
  const W = 520, H = 200, L = 40, R = 10, T = 12, B = 28;
  const maxY = Math.ceil(Math.max(...profile.map(p => p.fixed_kwh)) * 2) / 2 || 1;
  const bw = (W - L - R) / 24;
  const y = v => H - B - v / maxY * (H - T - B);
  for (let i = 0; i <= 4; i++) {
    const v = maxY * i / 4;
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end"}, fmt(v, 1)));
  }
  profile.forEach((p, i) => {
    const x0 = L + i * bw;
    svg.append(el("rect", {x: x0 + bw * 0.12, y: y(p.fixed_kwh), width: bw * 0.38, height: y(0) - y(p.fixed_kwh), fill: "#5d7b84"}));
    svg.append(el("rect", {x: x0 + bw * 0.5, y: y(p.vsd_kwh), width: bw * 0.38, height: y(0) - y(p.vsd_kwh), fill: "#7fc4ea"}));
    if (i % 3 === 0) svg.append(el("text", {x: x0 + bw / 2, y: H - 9, "text-anchor": "middle", "font-size": 11}, `${String(p.hod).padStart(2, "0")}:00`));
  });
  svg.append(el("text", {x: W - R, y: T + 4, "text-anchor": "end", "font-size": 11.5}, t("kWh per hour · grey fixed speed · blue VSD")));
}

/* ================================================================ i18n */
Object.assign(EN_T, {
  "ov.sub": "That is {0}% of this compressor's ₹{1} monthly bill, lost to leaks, unloaded running and excess pressure. The first fixes cost almost nothing.",
  "story.when": "{0} {1}", "story.night": "Night of {0} {1}",
  "story.calib": "Effective storage {0} m³ (the tank nameplate says {1} m³; pipework adds the rest). Specific power {2} kW per m³/min.",
  "story.leak": "Leak flow jumped by {0} m³/min since the previous night's test.",
  "story.fixed": "After the repair the nightly leak fell by {0} m³/min. Measured, not predicted:",
  "story.now": "{0}% of capacity still leaks, worth ₹{1}/month. Run Leak Hunter at the next lunch break.",
  "payback.m": "payback {0} months",
  "leak.tip": "leak {0} kWh",
  "wa.msg": "<b>New air leak: {0}</b>\nLast night's automatic test found leakage up by {2} m³/min after the {1} shift, worth about <b>₹{3}/month</b>.\nPlease run Leak Hunter in the {4} lunch break.\nReply 1 to acknowledge.",
  "lh.test": "{0}: pressure fell {1} bar in 2 minutes → outflow {2} ± {3} m³/min (at 7 bar) ≈ ₹{4}/month if left open.",
  "lh.surveydone": "{0} decay tests, about {1} minutes of a lunch break. Total measured leakage {2} m³/min ≈ ₹{3}/month.",
  "lh.recommend": "Start the ultrasonic or soap-water walk in {0}: it carries {1}% of the measured leakage (≈ ₹{2}/month).",
  "lh.ticket": "Ticket {0} raised. It is in the Maintenance tab, ready to acknowledge, repair and verify.",
  "id.vol": "tank nameplate {0} m³ · pipework included",
  "id.leak": "{0}% of compressor capacity",
  "id.sp": "kW per m³/min · nameplate {0}",
  "id.curve": "{0} kW at 7 bar · {1}",
  "id.scen.leak": "Leak {0} m³/min ({1}% of capacity) by {2}, confidence {3}",
  "id.scen.curve": "loaded power {0}% of nameplate, {1}",
  "id.scen.unl": "{0}% of electricity spent unloaded",
  "hr.result": "Recovers ≈ <strong>{0} kWh</strong> of heat a month → <strong>{1} litres/day</strong> of 60 °C water. Saves <strong>₹{2}/month</strong> ({3} {4} of fuel), {5} kg CO₂/month. Payback <strong>{6} months</strong>.",
  "vsd.result": "Fixed speed used {0} kWh/month; a VSD would use {1} kWh ({2}% less) → ₹{3}/month. Payback {4} months. Average demand {5}% of capacity."
});

Object.assign(TA, {
  /* nav + shell */
  "Plant overview": "ஆலை மேலோட்டம்", "Live twin": "நேரடி இரட்டை", "Leak Hunter": "கசிவு வேட்டை",
  "Self-calibration": "சுய அளவீடு", "Savings plan": "சேமிப்புத் திட்டம்", "Maintenance": "பராமரிப்பு",
  "Evidence & limits": "சான்றுகள் & வரம்புகள்", "Site setup": "தள அமைப்பு",
  "Local prototype · runs offline · one compressor room": "உள்ளூர் முன்மாதிரி · இணையமின்றி இயங்கும் · ஒரு அமுக்கி அறை",
  /* overview */
  "last 14 days · simulated": "கடந்த 14 நாட்கள் · உருவகம்",
  "/ month of electricity buys no useful air": "/ மாதம் மின்சாரம் பயனுள்ள காற்றைத் தரவில்லை",
  "compressor bill / month": "அமுக்கி மின் கட்டணம் / மாதம்",
  "of capacity leaks (last night)": "கொள்ளளவு கசிகிறது (நேற்றிரவு)",
  "repair saving, measured overnight": "பழுது சேமிப்பு, இரவில் அளக்கப்பட்டது",
  "extra: fuel saved by heat recovery": "கூடுதல்: வெப்ப மீட்பால் எரிபொருள் சேமிப்பு",
  "Nightly leak test": "இரவுக் கசிவுச் சோதனை",
  "% of compressor capacity · pressure + power only": "அமுக்கி கொள்ளளவின் % · அழுத்தம் + மின்திறன் மட்டும்",
  "What the twin noticed this fortnight": "இந்த இரு வாரங்களில் இரட்டை கவனித்தவை",
  "Loading events…": "நிகழ்வுகள் ஏற்றப்படுகின்றன…",
  "Where the money is, cheapest fix first": "பணம் எங்கே போகிறது: மலிவான தீர்வு முதலில்",
  "Alert as the operator receives it": "இயக்குநருக்கு வரும் எச்சரிக்கை",
  "maintenance group": "பராமரிப்புக் குழு",
  "Message preview only. Delivery through a WhatsApp Business / SMS gateway is a deployment step, not part of this prototype.":
    "செய்தி முன்னோட்டம் மட்டுமே. WhatsApp Business / SMS வழியாக அனுப்புவது நிறுவல் படி; இந்த முன்மாதிரியின் பகுதி அல்ல.",
  "Daily electricity and what leaks cost": "தினசரி மின்சாரம் மற்றும் கசிவின் செலவு",
  "useful + other kWh": "பயனுள்ள + பிற kWh", "leak kWh (nightly test)": "கசிவு kWh (இரவுச் சோதனை)", "unloaded running": "சுமையற்ற இயக்கம்",
  "ov.sub": "இது இந்த அமுக்கியின் மாதாந்திர ₹{1} கட்டணத்தில் {0}% — கசிவு, சுமையற்ற இயக்கம், அதிக அழுத்தம் ஆகியவற்றால் வீணாகிறது. முதல் தீர்வுகளுக்கு கிட்டத்தட்ட செலவே இல்லை.",
  "story.when": "{0} {1}", "story.night": "{0} {1} இரவு",
  "Twin calibrated itself from the first quiet night": "முதல் அமைதியான இரவிலேயே இரட்டை தன்னை அளவீடு செய்தது",
  "story.calib": "பயனுள்ள சேமிப்பு {0} ம³ (தொட்டிப் பெயர்ப்பலகை {1} ம³; மீதி குழாய்களில்). தனித்திறன் {2} kW / ம³/நிமி.",
  "New leak detected overnight": "இரவில் புதிய கசிவு கண்டறியப்பட்டது",
  "Leak reduction confirmed overnight": "இரவில் கசிவு குறைவு உறுதிசெய்யப்பட்டது",
  "story.leak": "முந்தைய இரவுச் சோதனையை விட கசிவு {0} ம³/நிமி உயர்ந்தது.",
  "story.fixed": "பழுதுக்குப் பின் இரவுக் கசிவு {0} ம³/நிமி குறைந்தது. கணிப்பு அல்ல, அளவீடு:",
  "Background leakage still growing slowly": "பின்னணிக் கசிவு மெதுவாக இன்னும் வளர்கிறது",
  "story.now": "கொள்ளளவில் {0}% இன்னும் கசிகிறது — மாதம் ₹{1}. அடுத்த மதிய இடைவேளையில் கசிவு வேட்டையை இயக்கவும்.",
  "not worth it": "பயனில்லை", "fuel, not electricity": "எரிபொருள், மின்சாரம் அல்ல", "free · do today": "இலவசம் · இன்றே செய்",
  "no cost": "செலவில்லை", "payback.m": "மீட்பு {0} மாதம்",
  "Fix the remaining leaks": "மீதமுள்ள கசிவுகளை சரிசெய்", "Stop unloaded running": "சுமையற்ற இயக்கத்தை நிறுத்து",
  "Lower the pressure band": "அழுத்த வரம்பைக் குறை", "Recover compressor heat": "அமுக்கி வெப்பத்தை மீட்டெடு",
  "Variable-speed compressor": "மாறும்-வேக அமுக்கி",
  "Electricity items are indicative and overlap slightly (lower pressure also shrinks leaks); a VSD would replace the unloaded-running fix, so it is not added. Heat recovery saves fuel, not electricity, and is shown separately.":
    "மின்சார சேமிப்புகள் தோராயமானவை, சிறிது மேற்பொருந்தும் (குறைந்த அழுத்தம் கசிவையும் குறைக்கும்). VSD சுமையற்ற தீர்வை மாற்றும் என்பதால் கூட்டப்படவில்லை. வெப்ப மீட்பு எரிபொருளைச் சேமிக்கிறது; தனியாகக் காட்டப்படுகிறது.",
  "No new-leak alert in this fortnight.": "இந்த இரு வாரங்களில் புதிய கசிவு எச்சரிக்கை இல்லை.",
  "wa.msg": "<b>புதிய காற்றுக் கசிவு: {0}</b>\n{1} ஷிப்ட்டுக்குப் பின் நேற்றிரவு தானியங்கிச் சோதனையில் கசிவு {2} ம³/நிமி உயர்ந்துள்ளது — சுமார் <b>மாதம் ₹{3}</b>.\n{4} மதிய இடைவேளையில் கசிவு வேட்டையை இயக்கவும்.\nஒப்புக்கொள்ள 1 என பதிலளிக்கவும்.",
  "new leak": "புதிய கசிவு", "repair verified": "பழுது உறுதி", "leak.tip": "கசிவு {0} kWh",
  "Mon": "திங்", "Tue": "செவ்", "Wed": "புத", "Thu": "வியா", "Fri": "வெள்", "Sat": "சனி", "Sun": "ஞாயி",
  "month": "மாதம்",
  /* leak hunter */
  "The twin already knows how much air leaks. This tells the maintenance team where to walk first, using the branch valves the plant already has.":
    "எவ்வளவு காற்று கசிகிறது என்பது இரட்டைக்கு ஏற்கனவே தெரியும். ஆலையில் உள்ள கிளை வால்வுகளைக் கொண்டே, பராமரிப்புக் குழு முதலில் எங்கே தேட வேண்டும் என இது சொல்கிறது.",
  "Air network · click a valve to open or close it": "காற்று வலையமைப்பு · வால்வைத் திறக்க/மூட கிளிக் செய்யவும்",
  "Plant": "ஆலை", "New random plant": "புதிய சீரற்ற ஆலை",
  "Run 2-minute decay test": "2-நிமிட அழுத்தக் குறைவு சோதனை", "Run full guided survey": "முழு வழிகாட்டு ஆய்வை இயக்கு",
  "Show hidden true leaks (grading only)": "மறைந்த உண்மைக் கசிவைக் காட்டு (மதிப்பீட்டுக்கு மட்டும்)",
  "Pressure decay with the compressor stopped": "அமுக்கி நிறுத்தப்பட்ட நிலையில் அழுத்தக் குறைவு",
  "Open some valves and run a test, or run the full survey.": "சில வால்வுகளைத் திறந்து சோதனை செய்யவும், அல்லது முழு ஆய்வை இயக்கவும்.",
  "Where the leakage is": "கசிவு எங்கே உள்ளது",
  "Run the full guided survey to allocate leakage to each zone.": "ஒவ்வொரு மண்டலத்துக்கும் கசிவைப் பகிர முழு ஆய்வை இயக்கவும்.",
  "Raise repair ticket for the worst zone": "மோசமான மண்டலத்துக்கு பழுது டிக்கெட் எழுப்பு",
  "Compressor room & main header": "அமுக்கி அறை & முதன்மைக் குழாய்", "Bay 1 - CNC machining": "பிரிவு 1 - CNC இயந்திரம்",
  "Bay 2 - Assembly line": "பிரிவு 2 - அசெம்பிளி வரிசை", "Paint booth": "வண்ணம் தெளிக்கும் அறை", "Packing & dispatch": "பேக்கிங் & அனுப்புதல்",
  "Compressor + receiver": "அமுக்கி + ரிசீவர்", "header": "முதன்மைக் குழாய்", "pipe": "குழாய்",
  "valve open": "வால்வு திறந்துள்ளது", "valve closed": "வால்வு மூடியுள்ளது", "OPEN": "திறந்தது", "CLOSED": "மூடியது",
  "Sky-blue lines carry compressed air (IS 2379 colour). Dashed = isolated.": "வான்-நீலக் கோடுகள் அழுத்தக் காற்றைச் சுமக்கின்றன (IS 2379 நிறம்). புள்ளிக்கோடு = தனிமைப்படுத்தப்பட்டது.",
  "No test run yet": "இன்னும் சோதனை இல்லை", "header only": "முதன்மைக் குழாய் மட்டும்",
  "Running the guided sequence: close all, then open one branch at a time…": "வழிகாட்டு வரிசை: அனைத்தையும் மூடி, ஒவ்வொரு கிளையாகத் திறக்கிறது…",
  "lh.test": "{0}: 2 நிமிடத்தில் அழுத்தம் {1} பார் குறைந்தது → வெளியேற்றம் {2} ± {3} ம³/நிமி (7 பாரில்) ≈ திறந்தே விட்டால் மாதம் ₹{4}.",
  "lh.surveydone": "{0} சோதனைகள், மதிய இடைவேளையில் சுமார் {1} நிமிடம். மொத்தக் கசிவு {2} ம³/நிமி ≈ மாதம் ₹{3}.",
  "lh.recommend": "அல்ட்ராசோனிக் / சோப்பு-நீர் தேடலை {0} இல் தொடங்கவும்: அளந்த கசிவில் {1}% இங்கே (≈ மாதம் ₹{2}).",
  "lh.ticket": "டிக்கெட் {0} எழுப்பப்பட்டது. பராமரிப்புப் பகுதியில் ஒப்புதல், பழுது, சரிபார்ப்புக்குத் தயார்.",
  "hidden true leak": "மறைந்த உண்மைக் கசிவு",
  "Black ticks mark the hidden true leak per zone, shown only to grade the method.": "கருப்புக் குறிகள் ஒவ்வொரு மண்டலத்தின் மறைந்த உண்மைக் கசிவைக் காட்டுகின்றன — முறையை மதிப்பிட மட்டும்.",
  "Stop production (lunch or shift change) and let the compressor reach cut-out pressure.": "உற்பத்தியை நிறுத்தி (மதிய இடைவேளை / ஷிப்ட் மாற்றம்) அமுக்கி துண்டிப்பு அழுத்தத்தை அடையட்டும்.",
  "Switch the compressor off so it cannot reload during the test.": "சோதனையின்போது மீண்டும் இயங்காமல் இருக்க அமுக்கியை அணைக்கவும்.",
  "Close every branch valve: the first test measures the compressor room and main header only.": "அனைத்து கிளை வால்வுகளையும் மூடவும்: முதல் சோதனை அமுக்கி அறை மற்றும் முதன்மைக் குழாயை மட்டும் அளக்கும்.",
  "Open one branch at a time and repeat a 2-minute decay test after each opening.": "ஒவ்வொரு கிளையாகத் திறந்து, ஒவ்வொரு முறையும் 2-நிமிடச் சோதனையை மீண்டும் செய்யவும்.",
  "The twin differences successive tests to allocate leakage to each zone.": "அடுத்தடுத்த சோதனைகளின் வேறுபாட்டைக் கொண்டு இரட்டை ஒவ்வொரு மண்டலத்தின் கசிவையும் கணக்கிடுகிறது.",
  /* calibration */
  "The twin calibrates itself": "இரட்டை தன்னைத் தானே அளவீடு செய்கிறது",
  "No flow meter, no manual tuning. Every quiet night the twin runs a load/unload leak test on the two signals and re-learns this plant: storage volume, leak flow, specific power and motor health.":
    "ஓட்டமானி இல்லை, கைமுறைச் சீரமைப்பு இல்லை. ஒவ்வொரு அமைதியான இரவிலும் இரண்டு சிக்னல்களைக் கொண்டு சுமை/சுமையற்ற கசிவுச் சோதனை செய்து இந்த ஆலையை மீண்டும் கற்கிறது: சேமிப்புக் கொள்ளளவு, கசிவு ஓட்டம், தனித்திறன், மோட்டார் நலம்.",
  "Every night's test": "ஒவ்வொரு இரவுச் சோதனையும்", "Night": "இரவு", "Load cycles": "சுமைச் சுழற்சிகள்",
  "Leak (time ratio)": "கசிவு (நேர விகிதம்)", "Leak (slope ratio)": "கசிவு (சரிவு விகிதம்)", "Leak m³/min": "கசிவு ம³/நிமி",
  "Storage m³": "சேமிப்பு ம³", "Confidence": "நம்பகம்", "Hidden true leak": "மறைந்த உண்மைக் கசிவு",
  "Two independent estimators of the same leak. When they disagree the night is graded down instead of trusted.":
    "ஒரே கசிவுக்கு இரண்டு தனித்த மதிப்பீடுகள். அவை வேறுபட்டால் அந்த இரவு நம்பப்படாமல் தரம் குறைக்கப்படும்.",
  "Tested on 30 plants it has never seen": "இதுவரை காணாத 30 ஆலைகளில் சோதிக்கப்பட்டது",
  "Run the same identification on a 30-minute scenario": "அதே அடையாளம் காணலை 30-நிமிடச் சூழலில் இயக்கு",
  "Identify from pressure + power": "அழுத்தம் + மின்திறனிலிருந்து அடையாளம் காண்",
  "Effective storage": "பயனுள்ள சேமிப்பு", "Leak, last night": "கசிவு, நேற்றிரவு", "Specific power": "தனித்திறன்",
  "Motor health": "மோட்டார் நலம்", "Nights used": "பயன்பட்ட இரவுகள்",
  "only confident nights feed the plant model": "நம்பகமான இரவுகள் மட்டுமே ஆலை மாதிரிக்குச் செல்கின்றன",
  "id.vol": "தொட்டிப் பெயர்ப்பலகை {0} ம³ · குழாய்கள் உட்பட", "id.leak": "அமுக்கி கொள்ளளவில் {0}%",
  "id.sp": "kW / ம³/நிமி · பெயர்ப்பலகை {0}", "id.curve": "7 பாரில் {0} kW · {1}",
  "within nameplate band": "பெயர்ப்பலகை வரம்பிற்குள்", "deterioration suspected": "தேய்மானம் சந்தேகம்",
  "high": "உயர்", "medium": "நடுத்தர", "low": "குறைவு",
  "Leak error": "கசிவுப் பிழை", "mean absolute, 30 unseen plants": "சராசரி முழுப் பிழை, காணாத 30 ஆலைகள்",
  "Storage error": "சேமிப்புப் பிழை", "estimated, not typed in": "மதிப்பிடப்பட்டது, தட்டச்சு செய்யப்படவில்லை",
  "Within ±10%": "±10% க்குள்", "plants with leak error under 10%": "10% க்கும் குறைவான கசிவுப் பிழை கொண்ட ஆலைகள்",
  "true": "உண்மை", "estimated": "மதிப்பீடு", "hidden true leak, m³/min →": "மறைந்த உண்மைக் கசிவு, ம³/நிமி →", "↑ estimated": "↑ மதிப்பீடு",
  "Identifying…": "அடையாளம் காணப்படுகிறது…",
  "id.scen.leak": "கசிவு {0} ம³/நிமி (கொள்ளளவில் {1}%) — {2}, நம்பகம் {3}",
  "id.scen.curve": "சுமை மின்திறன் பெயர்ப்பலகையின் {0}%, {1}", "id.scen.unl": "மின்சாரத்தில் {0}% சுமையற்ற நிலையில் செலவாகிறது",
  "load/unload cycling (time-ratio + slope-ratio)": "சுமை/சுமையற்ற சுழற்சி (நேர + சரிவு விகிதம்)",
  "passive decay only (compressor did not reload)": "அழுத்தக் குறைவு மட்டும் (அமுக்கி மீண்டும் இயங்கவில்லை)",
  /* savings */
  "Every number is re-simulated or calculated from the two-week profile. The twin is allowed to say \"don't buy it\".":
    "ஒவ்வொரு எண்ணும் மறு-உருவகம் அல்லது இரு வார சுயவிவரத்திலிருந்து கணக்கிடப்பட்டது. \"வாங்க வேண்டாம்\" என்று சொல்லவும் இரட்டைக்கு அனுமதி உண்டு.",
  "Heat recovery: the saving nobody measures": "வெப்ப மீட்பு: யாரும் அளக்காத சேமிப்பு",
  "About three quarters of compressor electricity leaves as heat in the oil cooler. A plate heat exchanger turns it into hot water.":
    "அமுக்கி மின்சாரத்தில் சுமார் முக்கால் பங்கு எண்ணெய் குளிரூட்டியில் வெப்பமாக வெளியேறுகிறது. தட்டு வெப்பப் பரிமாற்றி அதை சுடுநீராக மாற்றுகிறது.",
  "Heat now comes from": "தற்போது வெப்பம் வருவது", "LPG": "LPG", "Diesel": "டீசல்", "Electric heater": "மின் ஹீட்டர்",
  "Usable heat %": "பயன்படும் வெப்பம் %", "Kit cost ₹": "கருவி செலவு ₹", "Calculate heat recovery": "வெப்ப மீட்பைக் கணக்கிடு",
  "Should this plant buy a variable-speed compressor?": "இந்த ஆலை மாறும்-வேக அமுக்கி வாங்க வேண்டுமா?",
  "Replays every hour of the two-week profile through a VSD model and compares it with the energy the fixed-speed machine actually used.":
    "இரு வார சுயவிவரத்தின் ஒவ்வொரு மணிநேரத்தையும் VSD மாதிரியில் மீண்டும் இயக்கி, நிலைவேக இயந்திரம் உண்மையில் பயன்படுத்திய ஆற்றலுடன் ஒப்பிடுகிறது.",
  "Upgrade cost ₹": "மேம்படுத்தல் செலவு ₹", "Min speed %": "குறைந்தபட்ச வேகம் %", "Drive loss %": "டிரைவ் இழப்பு %",
  "Replay profile with a VSD": "VSD உடன் சுயவிவரத்தை இயக்கு", "Calculating…": "கணக்கிடப்படுகிறது…", "Replaying 336 hours…": "336 மணிநேரம் மீண்டும் இயக்கப்படுகிறது…",
  "kg": "கி.கி", "litre": "லிட்டர்", "kWh": "kWh",
  "hr.result": "மாதம் ≈ <strong>{0} kWh</strong> வெப்பம் மீட்கப்படும் → நாளொன்றுக்கு <strong>{1} லிட்டர்</strong> 60 °C நீர். <strong>மாதம் ₹{2}</strong> சேமிப்பு ({3} {4} எரிபொருள்), மாதம் {5} கி.கி CO₂. மீட்புக்காலம் <strong>{6} மாதம்</strong>.",
  "vsd.result": "நிலைவேகம் மாதம் {0} kWh பயன்படுத்தியது; VSD {1} kWh ({2}% குறைவு) → மாதம் ₹{3}. மீட்புக்காலம் {4} மாதம். சராசரி தேவை கொள்ளளவில் {5}%.",
  "Worth a quote.": "விலைப்புள்ளி பெறத் தகுந்தது.", "Not worth it for this plant: fix leaks and the stop timer first.": "இந்த ஆலைக்குப் பயனில்லை: முதலில் கசிவுகளையும் நிறுத்த டைமரையும் சரிசெய்யவும்.",
  "kWh per hour · grey fixed speed · blue VSD": "மணிக்கு kWh · சாம்பல் நிலைவேகம் · நீலம் VSD",
  "For plants with two compressors. The default site has one, so this saving is not counted in the plant overview.":
    "இரண்டு அமுக்கிகள் உள்ள ஆலைகளுக்கு. இயல்புத் தளத்தில் ஒன்றுதான் உள்ளது; எனவே இந்தச் சேமிப்பு மேலோட்டத்தில் கணக்கிடப்படவில்லை.",
  "4 · Combined-action savings": "4 · ஒருங்கிணைந்த செயல் சிக்கனம்",
  /* maintenance / trust / settings */
  "From a diagnosis to a verified repair. Every step is logged with who did it, and the saving is measured afterwards, not promised.":
    "நோயறிதல் முதல் உறுதிசெய்யப்பட்ட பழுது வரை. ஒவ்வொரு படியும் யார் செய்தார் என்று பதிவாகிறது; சேமிப்பு வாக்குறுதியாக அல்ல, பின்னர் அளக்கப்படுகிறது.",
  "Repair tickets": "பழுது டிக்கெட்டுகள்",
  "Fault diagnosis (unknown & multiple faults)": "குறைபாட்டு நோயறிதல் (தெரியாத & பல குறைபாடுகள்)",
  "Sensor health monitoring": "சென்சார் ஆரோக்கிய கண்காணிப்பு", "Stress-test mode": "ஸ்ட்ரெஸ்-சோதனை முறை",
  "Downloadable PDF report": "பதிவிறக்கக்கூடிய PDF அறிக்கை",
  "What has been tested, on what data, and what has not. Every screen in this prototype runs on simulated physics; the real-data study below says how far that transfers.":
    "எது சோதிக்கப்பட்டது, எந்தத் தரவில், எது சோதிக்கப்படவில்லை. இந்த முன்மாதிரியின் ஒவ்வொரு திரையும் உருவக இயற்பியலில் இயங்குகிறது; கீழே உள்ள உண்மைத் தரவு ஆய்வு அது எவ்வளவு தூரம் பொருந்தும் எனச் சொல்கிறது.",
  "Real-data check: UCI MetroPT-3 (1.5 million readings)": "உண்மைத் தரவுச் சோதனை: UCI MetroPT-3 (15 லட்சம் அளவீடுகள்)",
  "Synthetic threshold on real data": "உண்மைத் தரவில் உருவக வரம்பு", "windows flagged: it does not transfer uncalibrated": "சாளரங்கள் கொடியிடப்பட்டன: அளவீடு இன்றி பொருந்தாது",
  "Ranking skill (AUC)": "தரவரிசைத் திறன் (AUC)", "weak but above chance": "பலவீனம், ஆனால் தற்செயலை விட மேல்",
  "Leak share in top-score cluster": "உயர் மதிப்பெண் கொத்தில் கசிவுப் பங்கு", "4.68% vs 1.99% base rate": "4.68% vs அடிப்படை 1.99%",
  "Conclusion: the anomaly model transfers directionally, not calibrated. That is why the field version recalibrates per site (recalibrate.py) and why the self-calibrating physics test, not the ML model, sets the money figures.":
    "முடிவு: முரண்பாட்டு மாதிரி திசையளவில் பொருந்துகிறது, அளவீட்டளவில் அல்ல. அதனால்தான் கள பதிப்பு ஒவ்வொரு தளத்துக்கும் மறு அளவீடு செய்கிறது; பண எண்களை ML மாதிரி அல்ல, சுய-அளவீட்டு இயற்பியல் சோதனை தீர்மானிக்கிறது.",
  "Model version history": "மாதிரி பதிப்பு வரலாறு", "What is not modelled": "மாதிரியில் இல்லாதவை",
  "Nameplate values, who can do what, backups and how a real gateway connects.": "பெயர்ப்பலகை மதிப்புகள், யார் என்ன செய்யலாம், காப்புப்பிரதிகள், உண்மையான நுழைவாயில் எப்படி இணைகிறது.",
  "Plant setup & calibration": "ஆலை அமைப்பு & அளவீடு", "User roles & access": "பயனர் பொறுப்புகள் & அணுகல்", "Backup & restore": "காப்புப்பிரதி & மீட்டமை"
});

window.onLangChange = () => {
  document.documentElement.lang = LANG === "ta" ? "ta" : "en";
  document.title = LANG === "ta" ? "ஃபேக்டரிஏர் ட்வின் | அமுக்கி கண்காணிப்பு" : "FactoryAir Twin | Compressor monitor";
  renderOverview();
  if (twin.layout) {
    $("lh-procedure").replaceChildren(...twin.layout.procedure.map(step => h("li", {}, t(step))));
    drawSchematic(); drawDecay();
    if (twin.survey) renderAllocation(twin.survey);
  }
  if (twin.longrun && twin.validation) renderCalibration(twin.longrun, twin.validation);
};

/* ================================================================ wiring */
$("lh-test").addEventListener("click", runDecayTest);
$("lh-survey").addEventListener("click", runSurvey);
$("lh-ticket").addEventListener("click", raiseZoneTicket);
$("lh-newplant").addEventListener("click", () => {
  twin.seed = 1 + Math.floor(Math.random() * 99999);
  twin.open.clear(); twin.traces = []; twin.survey = null; twin.testIndex = 0;
  $("lh-alloc").replaceChildren(h("p", {class: "muted"}, t("Run the full guided survey to allocate leakage to each zone.")));
  $("lh-recommend").hidden = true; $("lh-ticket").hidden = true;
  $("lh-test-result").textContent = t("Open some valves and run a test, or run the full survey.");
  loadLayout();
});
$("id-run").addEventListener("click", runIdentify);
$("hr-run").addEventListener("click", runHeat);
$("vsd-run").addEventListener("click", runVsd);

applyLang(LANG);  // re-apply now that the Tamil dictionary includes the new screens
route();
loadOverview().then(() => ensureLongrun().then(() => { if (twin.overview) renderStory(twin.overview); }).catch(() => {}));
