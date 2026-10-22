"use strict";
/* Live gateway (virtual edge node) and bring-your-own-CSV screens.
   Reuses $, t, tf, fmt, inr, api, el, h, card, rupees, getJSON from app.js / twin.js. */

const gw = {timer: null, rows: [], lastT: 0, emu: null, lastTotals: null};

/* ================================================================ live gateway */
async function gwControl(body, logText) {
  try {
    gw.emu = await api("/api/edge/emulator", body);
    if (logText) gwLog(logText);
    renderGwButtons();
    pollGateway();
  } catch (error) { gwLog(error.message); }
}

function gwLog(text) {
  const li = h("li", {}, `${new Date().toLocaleTimeString()} · ${text}`);
  $("gw-log").prepend(li);
  while ($("gw-log").children.length > 30) $("gw-log").lastChild.remove();
}

function renderGwButtons() {
  const e = gw.emu;
  if (!e) return;
  $("gw-start").disabled = e.running;
  $("gw-stop").disabled = !e.running;
  $("gw-prod").setAttribute("aria-pressed", String(!e.production));
  $("gw-prod").textContent = e.production ? t("End shift (go idle)") : t("Start shift (production on)");
  $("gw-leak").setAttribute("aria-pressed", String(e.new_leak_open));
  $("gw-leak").textContent = e.new_leak_open ? t("Repair the new leak") : t("Open a new leak");
}

async function pollGateway() {
  try {
    const d = await getJSON(`/api/edge/live?since=${gw.lastT}`);
    gw.emu = d.emulator;
    renderGwButtons();
    if (d.rows.length) {
      if (d.rows[0].t <= gw.lastT - 3600 || (gw.rows.length && d.rows[0].t < gw.rows.at(-1).t)) gw.rows = [];
      gw.rows.push(...d.rows);
      gw.lastT = d.rows.at(-1).t;
      if (gw.rows.length > 900) gw.rows = gw.rows.slice(-900);
    } else if (!d.status.buffer_seconds) {
      gw.rows = []; gw.lastT = 0;
    }
    $("gw-generated").textContent = inr(d.emulator.frames_generated);
    $("gw-accepted").textContent = inr(d.totals.accepted);
    $("gw-rejected").textContent = tf("gw.rejected", inr(d.totals.rejected));
    $("gw-stage-ingest").classList.toggle("warn", d.totals.rejected > 0);
    const hl = d.health;
    $("gw-health").textContent = hl ? t(`w.${hl.overall}`).toUpperCase() : "—";
    $("gw-health-note").textContent = hl ? Object.entries(hl.sensors).map(([k, v]) => `${t(k.split("_")[0])} ${t("w." + v)}`).join(" · ") : t("waiting for 60 s of data");
    $("gw-stage-health").classList.toggle("warn", hl?.overall === "degraded");
    $("gw-stage-health").classList.toggle("bad", hl?.overall === "failed");
    $("gw-alerts").textContent = d.alerts.length;
    $("gw-stage-twin").classList.toggle("warn", d.alerts.length > 0);
    $("gw-clock").textContent = gw.rows.length ? new Date(gw.lastT * 1000).toLocaleTimeString() : "—";
    const issues = [];
    Object.entries(d.totals.reasons).forEach(([why, n]) => issues.push(tf("gw.rejectline", n, t(why))));
    (hl?.issues || []).forEach(text => issues.push(text));
    d.alerts.forEach(a => issues.push(`${t(a.title)}: ${a.evidence}`));
    $("gw-issues").replaceChildren(...(issues.length ? issues.map(x => h("li", {}, x)) : [h("li", {class: "muted"}, t("Nothing yet."))]));
    drawGateway();
  } catch (error) { /* the next poll retries */ }
}

function drawGateway() {
  const svg = $("gw-chart");
  svg.replaceChildren();
  const W = 620, H = 300, L = 44, R = 40, T = 10, mid = 160, B = 26;
  const rows = gw.rows;
  const pY = v => T + (7.4 - v) / (7.4 - 5.8) * (mid - T - 14);
  const kY = v => mid + 10 + (9 - v) / 9 * (H - B - mid - 10);
  for (const v of [6, 6.5, 7, 7.4]) {
    svg.append(el("line", {x1: L, x2: W - R, y1: pY(v), y2: pY(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: pY(v) + 4, "text-anchor": "end"}, fmt(v, 1)));
  }
  for (const v of [0, 4, 8]) {
    svg.append(el("line", {x1: L, x2: W - R, y1: kY(v), y2: kY(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: W - R + 6, y: kY(v) + 4}, `${v}`));
  }
  svg.append(el("text", {x: 4, y: T + 8, "font-size": 11}, "bar"));
  svg.append(el("text", {x: W - R + 4, y: mid + 4, "font-size": 11}, "kW"));
  if (!rows.length) {
    svg.append(el("text", {x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 13}, t("Press Start streaming")));
    return;
  }
  const t0 = rows.at(-1).t - 900;
  const x = tt => L + Math.max(0, tt - t0) / 900 * (W - L - R);
  let start = null;
  rows.forEach((r, i) => {  // production shading
    if (r.prod && start === null) start = r.t;
    if ((!r.prod || i === rows.length - 1) && start !== null) {
      svg.append(el("rect", {x: x(start), y: T, width: Math.max(1, x(r.t) - x(start)), height: H - B - T, fill: "#e3f1f9"}));
      start = null;
    }
  });
  let dp = "", dk = "", prev = null;
  rows.forEach(r => {
    const gap = prev !== null && r.t - prev > 2;  // dropouts show as breaks
    dp += `${dp && !gap ? "L" : "M"}${x(r.t).toFixed(1)},${pY(Math.max(5.8, Math.min(7.4, r.p))).toFixed(1)}`;
    dk += `${dk && !gap ? "L" : "M"}${x(r.t).toFixed(1)},${kY(Math.max(0, Math.min(9, r.kw))).toFixed(1)}`;
    prev = r.t;
  });
  svg.append(el("path", {d: dp, fill: "none", stroke: "#1c6a96", "stroke-width": 1.8}));
  svg.append(el("path", {d: dk, fill: "none", stroke: "#5d7b84", "stroke-width": 1.4}));
  for (let m = 0; m <= 15; m += 5) svg.append(el("text", {x: L + m / 15 * (W - L - R), y: H - 8, "text-anchor": "middle"}, `−${15 - m} min`));
}

function startGatewayPolling() {
  if (gw.timer) return;
  pollGateway();
  gw.timer = setInterval(pollGateway, 1000);
}
function stopGatewayPolling() { clearInterval(gw.timer); gw.timer = null; }

async function gwIdentify() {
  $("gw-id-result").textContent = t("Identifying…");
  try {
    const r = await api("/api/twin/identify", {source: "edge"});
    const lt = r.leak_test;
    $("gw-id-result").textContent = lt.ok
      ? tf("gw.id", fmt(lt.leak_m3_min, 3), fmt(lt.leak_pct_of_capacity, 1), lt.effective_volume_m3 ? fmt(lt.effective_volume_m3, 2) : "—",
           t(lt.method), t(lt.confidence), lt.complete_cycles ?? 0)
      : t(lt.reason);
  } catch (error) { $("gw-id-result").textContent = error.message; }
}

/* ================================================================ your data */
async function runUpload(event) {
  event.preventDefault();
  const file = $("up-file").files[0];
  if (!file) return;
  const form = new FormData();
  form.append("csv", file);
  form.append("fad", $("up-fad").value); form.append("kw", $("up-kw").value); form.append("tariff", $("up-tariff").value);
  $("up-run").disabled = true;
  $("up-cards").replaceChildren(h("p", {class: "muted"}, t("Reading the file and testing every idle window…")));
  try {
    const response = await fetch("/api/twin/upload", {method: "POST", body: form});
    const r = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(r.error || (response.status === 413 ? t("File too large (8 MB max).") : `Upload failed (${response.status}).`));
    renderUpload(r);
  } catch (error) {
    $("up-cards").replaceChildren(h("p", {class: "callout"}, error.message));
    $("up-windows").replaceChildren(); $("up-meta").textContent = "";
  } finally { $("up-run").disabled = false; }
}

function renderUpload(r) {
  const s = r.summary;
  const cards = [
    card(t("Leak flow"), s.leak_m3_min == null ? "—" : `${fmt(s.leak_m3_min, 3)} m³/min`,
      s.leak_pct_of_capacity == null ? t("no idle window could be tested") : tf("id.leak", fmt(s.leak_pct_of_capacity, 1)), "warn"),
    card(t("Leak cost"), rupees(s.leak_inr_per_month), tf("up.co2", inr(s.leak_co2_kg_per_month ?? 0)), "warn"),
    card(t("Effective storage"), s.effective_volume_m3 == null ? "—" : `${fmt(s.effective_volume_m3, 2)} m³`,
      s.effective_volume_m3 == null ? t("needs load/unload cycling while idle") : t("estimated, not typed in")),
    card(t("Specific power"), s.specific_power_kw_per_m3_min == null ? "—" : fmt(s.specific_power_kw_per_m3_min, 2), t("kW per m³/min")),
    card(t("Unloaded running"), s.unloaded_share_pct == null ? "—" : `${fmt(s.unloaded_share_pct, 1)}%`, t("of electricity buys no air")),
    card(t("Motor health"), s.power_ratio_vs_nameplate == null ? "—" : `${fmt(s.power_ratio_vs_nameplate * 100, 0)}%`, t("loaded power vs nameplate at 7 bar"),
      s.power_ratio_vs_nameplate > 1.05 ? "warn" : "ok"),
  ];
  $("up-cards").replaceChildren(...cards);
  $("up-meta").textContent = tf("up.meta", inr(r.rows_used), fmt(r.hours_covered, 1), fmt(r.sample_interval_s, 0), fmt(r.loaded_threshold_kw, 2),
    r.windows_identified, r.idle_windows, r.power_from_current ? t("power computed from motor current") : t("power read directly")) + " " + t(r.note);
  $("up-windows").replaceChildren(...r.tests.map(w => h("tr", {},
    h("td", {}, w.window), h("td", {}, t(w.source)), h("td", {}, w.complete_cycles ?? "—"),
    h("td", {}, w.ok ? fmt(w.leak_m3_min, 3) : "—"), h("td", {}, w.ok ? `${fmt(w.leak_pct_of_capacity, 1)}%` : "—"),
    h("td", {}, w.ok && w.effective_volume_m3 ? fmt(w.effective_volume_m3, 2) : "—"),
    h("td", {}, w.ok ? h("span", {class: `conf ${w.confidence}`}, t(w.confidence)) : t(w.reason || "—")))));
}

/* ================================================================ i18n */
Object.assign(EN_T, {
  "gw.rejected": "{0} rejected", "w.not_fitted": "not fitted",
  "gw.rejectline": "{0} frame(s) rejected: {1}",
  "gw.id": "Leak {0} m³/min ({1}% of capacity) · storage {2} m³ · {3} · confidence {4} · {5} load cycles in the idle window.",
  "up.co2": "{0} kg CO₂e / month",
  "up.meta": "{0} rows over {1} h, one every {2} s. Loaded/unloaded split learned at {3} kW. {4} of {5} idle windows identified; {6}."
});
Object.assign(TA, {
  "Live gateway": "நேரடி நுழைவாயில்", "Your data": "உங்கள் தரவு",
  "One-page owner report": "ஒரு பக்க உரிமையாளர் அறிக்கை", "One-page owner report (EN / தமிழ்)": "ஒரு பக்க உரிமையாளர் அறிக்கை (EN / தமிழ்)",
  "The clip-on edge node, running in software. It sends the exact frames the ESP32 firmware will send, through the same validation, rules and self-calibration. Break it on purpose and watch the gateway cope.":
    "கிளிப்-ஆன் விளிம்புக் கருவி, மென்பொருளில் இயங்குகிறது. ESP32 ஃபர்ம்வேர் அனுப்பும் அதே சட்டகங்களை அதே சரிபார்ப்பு, விதிகள், சுய அளவீடு வழியாக அனுப்புகிறது. வேண்டுமென்றே பழுதாக்கி நுழைவாயில் எப்படிச் சமாளிக்கிறது என்று பாருங்கள்.",
  "Virtual edge node": "மெய்நிகர் விளிம்புக் கருவி", "frames generated": "உருவான சட்டகங்கள்", "Ingest & validate": "பெறுதல் & சரிபார்ப்பு",
  "Sensor health": "சென்சார் நலம்", "Twin": "இரட்டை", "rule alerts": "விதி எச்சரிக்கைகள்",
  "Drive the virtual plant": "மெய்நிகர் ஆலையை இயக்கு", "Start streaming": "ஓட்டத்தைத் தொடங்கு", "Stop": "நிறுத்து",
  "End shift (go idle)": "ஷிப்ட் முடி (ஓய்வு நிலை)", "Start shift (production on)": "ஷிப்ட் தொடங்கு (உற்பத்தி)",
  "Open a new leak": "புதிய கசிவைத் திற", "Repair the new leak": "புதிய கசிவைச் சரிசெய்",
  "Inject a fault the gateway must survive": "நுழைவாயில் தாங்க வேண்டிய பழுதைச் செலுத்து",
  "Wi-Fi dropout 30 s": "Wi-Fi துண்டிப்பு 30 வி", "Stuck transmitter": "உறைந்த டிரான்ஸ்மிட்டர்", "Impossible spike": "சாத்தியமற்ற உச்சம்",
  "Clock jumps back": "கடிகாரம் பின்னோக்கித் தாவுகிறது", "Reset gateway": "நுழைவாயிலை மீட்டமை",
  "Last 15 minutes from the node": "கருவியிலிருந்து கடைசி 15 நிமிடங்கள்", "pressure bar(g)": "அழுத்தம் பார்", "power kW": "மின்திறன் kW",
  "production on": "உற்பத்தி நடக்கிறது", "Press Start streaming": "ஓட்டத்தைத் தொடங்கு அழுத்தவும்",
  "Self-calibration on the live stream": "நேரடி ஓட்டத்தில் சுய அளவீடு",
  "End the shift, let the plant idle for a few virtual minutes, then identify. The node's machine-on contact tells the twin which seconds were idle.":
    "ஷிப்ட்டை முடித்து, சில மெய்நிகர் நிமிடங்கள் ஆலையை ஓய்வில் விட்டு, பிறகு அடையாளம் காணவும். எந்த வினாடிகள் ஓய்வு என்பதை கருவியின் இயந்திர-இயக்கத் தொடர்பு சொல்கிறது.",
  "Identify from the live buffer": "நேரடித் தரவிலிருந்து அடையாளம் காண்", "What the gateway rejected or flagged": "நுழைவாயில் நிராகரித்தவை / கொடியிட்டவை",
  "Nothing yet.": "இன்னும் எதுவும் இல்லை.", "Firmware for the real node:": "உண்மைக் கருவிக்கான ஃபர்ம்வேர்:", "CLI emulator:": "கட்டளை-வரி எமுலேட்டர்:",
  "waiting for 60 s of data": "60 வி தரவுக்காகக் காத்திருக்கிறது", "w.not_fitted": "பொருத்தப்படவில்லை", "not_fitted": "பொருத்தப்படவில்லை",
  "pressure": "அழுத்தம்", "power": "மின்திறன்", "temperature": "வெப்பநிலை",
  "gw.rejected": "{0} நிராகரிப்பு", "gw.rejectline": "{0} சட்டகம் நிராகரிக்கப்பட்டது: {1}",
  "pressure_bar outside physical range": "அழுத்தம் இயற்பியல் வரம்புக்கு வெளியே", "timestamp not increasing": "நேரமுத்திரை கூடவில்லை",
  "gw.id": "கசிவு {0} ம³/நிமி (கொள்ளளவில் {1}%) · சேமிப்பு {2} ம³ · {3} · நம்பகம் {4} · ஓய்வுச் சாளரத்தில் {5} சுமைச் சுழற்சிகள்.",
  "Try it with your own data": "உங்கள் சொந்தத் தரவில் முயற்சிக்கவும்",
  "Upload a pressure + power (or motor current) log from any data logger or energy meter. The twin finds the idle windows, runs the leak test and prices the waste for that plant.":
    "எந்த டேட்டா லாகர் அல்லது ஆற்றல் மீட்டரிலிருந்தும் அழுத்தம் + மின்திறன் (அல்லது மோட்டார் மின்னோட்டம்) பதிவைப் பதிவேற்றவும். இரட்டை ஓய்வுச் சாளரங்களைக் கண்டறிந்து, கசிவுச் சோதனை செய்து, அந்த ஆலையின் வீணைக் கணக்கிடுகிறது.",
  "Upload a CSV": "CSV பதிவேற்று", "Choose a CSV file (up to 8 MB)": "CSV கோப்பைத் தேர்ந்தெடுக்கவும் (8 MB வரை)",
  "Nameplate FAD m³/min": "பெயர்ப்பலகை FAD ம³/நிமி", "Nameplate kW": "பெயர்ப்பலகை kW", "Identify this plant": "இந்த ஆலையை அடையாளம் காண்",
  "No file handy?": "கோப்பு இல்லையா?", "Download a 36-hour sample logger file": "36-மணிநேர மாதிரி லாகர் கோப்பைப் பதிவிறக்கு",
  "Columns accepted: timestamp, pressure_bar, power_kw or current_a, optional production (1/0).": "ஏற்கும் நெடுவரிசைகள்: timestamp, pressure_bar, power_kw அல்லது current_a, விருப்ப production (1/0).",
  "What the twin found": "இரட்டை கண்டறிந்தவை", "Results appear here.": "முடிவுகள் இங்கே தோன்றும்.",
  "Every idle window tested": "சோதிக்கப்பட்ட ஒவ்வொரு ஓய்வுச் சாளரமும்", "Window": "சாளரம்", "Chosen by": "தேர்வு முறை", "Leak %": "கசிவு %",
  "Reading the file and testing every idle window…": "கோப்பைப் படித்து ஒவ்வொரு ஓய்வுச் சாளரத்தையும் சோதிக்கிறது…",
  "File too large (8 MB max).": "கோப்பு மிகப் பெரியது (அதிகபட்சம் 8 MB).",
  "Leak flow": "கசிவு ஓட்டம்", "Leak cost": "கசிவுச் செலவு", "no idle window could be tested": "எந்த ஓய்வுச் சாளரத்தையும் சோதிக்க முடியவில்லை",
  "needs load/unload cycling while idle": "ஓய்வில் சுமை/சுமையற்ற சுழற்சி தேவை", "kW per m³/min": "kW / ம³/நிமி",
  "Unloaded running": "சுமையற்ற இயக்கம்", "of electricity buys no air": "மின்சாரம் காற்றைத் தரவில்லை",
  "loaded power vs nameplate at 7 bar": "7 பாரில் பெயர்ப்பலகையுடன் ஒப்பிட்ட சுமை மின்திறன்",
  "power computed from motor current": "மோட்டார் மின்னோட்டத்திலிருந்து மின்திறன் கணக்கிடப்பட்டது", "power read directly": "மின்திறன் நேரடியாகப் படிக்கப்பட்டது",
  "up.co2": "மாதம் {0} கி.கி CO₂e",
  "up.meta": "{1} மணிநேரத்தில் {0} வரிசைகள், ஒவ்வொரு {2} வி. சுமை/சுமையற்ற பிரிப்பு {3} kW இல். {5} ஓய்வுச் சாளரங்களில் {4} அடையாளம் காணப்பட்டன; {6}.",
  "production flag off": "உற்பத்திக் கொடி அணைந்தது", "night 23:00-05:00": "இரவு 23:00-05:00", "whole file (assumed idle)": "முழுக் கோப்பு (ஓய்வு எனக் கொள்ளப்பட்டது)"
});

/* ================================================================ wiring */
$("gw-start").addEventListener("click", () => gwControl({action: "start", speed: Number($("gw-speed").value)}, t("Streaming started")));
$("gw-stop").addEventListener("click", () => gwControl({action: "stop"}, t("Stop")));
$("gw-speed").addEventListener("change", () => { if (gw.emu?.running) gwControl({action: "start", speed: Number($("gw-speed").value)}); });
$("gw-prod").addEventListener("click", () => gwControl({action: "production", on: !gw.emu?.production},
  gw.emu?.production ? t("End shift (go idle)") : t("Start shift (production on)")));
$("gw-leak").addEventListener("click", () => gwControl({action: "leak", on: !gw.emu?.new_leak_open},
  gw.emu?.new_leak_open ? t("Repair the new leak") : t("Open a new leak")));
document.querySelectorAll("[data-fault]").forEach(b => b.addEventListener("click", () =>
  gwControl({action: "fault", fault: b.dataset.fault}, b.textContent)));
$("gw-reset").addEventListener("click", () => { gw.rows = []; gw.lastT = 0; $("gw-id-result").textContent = ""; gwControl({action: "reset"}, t("Reset gateway")); });
$("gw-identify").addEventListener("click", gwIdentify);
$("up-form").addEventListener("submit", runUpload);
$("up-file").addEventListener("change", () => { $("up-file-name").textContent = $("up-file").files[0]?.name || t("Choose a CSV file (up to 8 MB)"); });

window.onViewShown = name => { if (name === "gateway") startGatewayPolling(); else stopGatewayPolling(); };
const previousLangHook = window.onLangChange;
window.onLangChange = () => { if (previousLangHook) previousLangHook(); renderGwButtons(); if (gw.rows.length) drawGateway(); };
applyLang(LANG);
if (location.hash === "#gateway") startGatewayPolling();

/* ================================================================ real-data self-calibration (Evidence) */
async function loadSelfcalEvidence() {
  try {
    const r = await getJSON("/api/evidence/selfcal");
    const s = r.summary;
    const leakNights = s.documented_leak_nights || [];
    const cards = [
      card(t("Depot nights identified"), `${s.nights_identified}/${s.nights_with_data}`, tf("sc.rows", inr(r.rows_read)), "ok"),
      card(t("Estimators agree"), s.median_disagreement_pct == null ? "—" : `±${fmt(s.median_disagreement_pct, 1)}%`, t("median time-ratio vs slope-ratio gap on real data"), "ok"),
      card(t("Normal-night leak"), s.median_leak_fraction_normal_nights == null ? "—" : `${fmt(s.median_leak_fraction_normal_nights * 100, 1)}%`, t("of compressor capacity, median")),
    ];
    if (s.auc_leak_nights_vs_normal != null) cards.push(card(t("Documented-leak nights rank higher"), fmt(s.auc_leak_nights_vs_normal, 2),
      t("probability vs a normal night (0.5 = chance)"), s.auc_leak_nights_vs_normal > 0.6 ? "ok" : "warn"));
    $("sc-cards").replaceChildren(...cards);
    $("sc-note").textContent = leakNights.map(n => tf("sc.night", n.night, fmt(n.leak_fraction * 100, 1), fmt(n.ratio_to_normal_median, 2), n.rank_high_to_low, n.of)).join(" · ")
      + (leakNights.length ? " · " : "") + r.limits.join(" ");
    drawSelfcal(r.nights.filter(n => n.ok));
  } catch (error) { /* stays "Not run yet." */ }
}

function drawSelfcal(nights) {
  const svg = $("sc-chart");
  if (!nights.length) return;
  svg.hidden = false; svg.replaceChildren();
  const W = 640, H = 220, L = 44, R = 10, T = 12, B = 28;
  const maxY = Math.ceil(Math.max(...nights.map(n => n.leak_fraction)) * 20) / 20 || 0.05;
  const bw = (W - L - R) / nights.length;
  const y = v => H - B - v / maxY * (H - T - B);
  for (let i = 0; i <= 4; i++) {
    const v = maxY * i / 4;
    svg.append(el("line", {x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: "#e2ddd1"}));
    svg.append(el("text", {x: L - 6, y: y(v) + 4, "text-anchor": "end"}, `${Math.round(v * 100)}%`));
  }
  nights.forEach((n, i) => {
    const r = el("rect", {x: L + i * bw, y: y(n.leak_fraction), width: Math.max(1, bw - 1), height: y(0) - y(n.leak_fraction),
      fill: n.documented_leak ? "#f2a516" : "#7fc4ea"});
    r.append(el("title", {}, `${n.night}: ${fmt(n.leak_fraction * 100, 1)}%${n.documented_leak ? " · " + t("documented leak") : ""}`));
    svg.append(r);
    if (i % Math.ceil(nights.length / 8) === 0) svg.append(el("text", {x: L + i * bw, y: H - 9, "font-size": 11}, n.night.slice(5)));
  });
}

Object.assign(EN_T, {"sc.rows": "{0} real readings read", "sc.night": "{0}: {1}% ({2}× normal, rank {3} of {4})"});
Object.assign(TA, {
  "Self-calibration on real data: MetroPT-3 depot nights": "உண்மைத் தரவில் சுய அளவீடு: MetroPT-3 பணிமனை இரவுகள்",
  "The same load/unload leak test, run on real TP3 pressure and motor current while the train is parked (01:00-05:00). No nameplate is needed for the leak fraction, so nothing is assumed.":
    "அதே சுமை/சுமையற்ற கசிவுச் சோதனை, ரயில் நிறுத்தப்பட்டிருக்கும்போது (01:00-05:00) உண்மையான TP3 அழுத்தம் மற்றும் மோட்டார் மின்னோட்டத்தில். கசிவுப் பங்குக்குப் பெயர்ப்பலகை தேவையில்லை; எதுவும் அனுமானிக்கப்படவில்லை.",
  "Not run yet.": "இன்னும் இயக்கப்படவில்லை.", "Depot nights identified": "அடையாளம் காணப்பட்ட பணிமனை இரவுகள்",
  "Estimators agree": "மதிப்பீடுகள் ஒத்துப்போகின்றன", "median time-ratio vs slope-ratio gap on real data": "உண்மைத் தரவில் நேர vs சரிவு விகித இடைவெளி (இடைநிலை)",
  "Normal-night leak": "சாதாரண இரவுக் கசிவு", "of compressor capacity, median": "அமுக்கி கொள்ளளவில், இடைநிலை",
  "Documented-leak nights rank higher": "பதிவான கசிவு இரவுகள் உயர் தரவரிசை", "probability vs a normal night (0.5 = chance)": "சாதாரண இரவை விட நிகழ்தகவு (0.5 = தற்செயல்)",
  "documented leak": "பதிவான கசிவு", "sc.rows": "{0} உண்மை அளவீடுகள் படிக்கப்பட்டன", "sc.night": "{0}: {1}% (சாதாரணத்தின் {2}×, {4} இல் {3}வது)"
});
loadSelfcalEvidence();
