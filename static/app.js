"use strict";
const $ = id => document.getElementById(id);
let simulation = null, cursor = 0, timer = null, busy = false;
const ns = "http://www.w3.org/2000/svg";
const fmt = (value, digits = 2) => value == null ? "N/A" : Number(value).toFixed(digits);
const time = seconds => `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
const inr = value => value == null ? "N/A" : Number(value).toLocaleString("en-IN");

function node(tag, attributes = {}, text = "") {
  const e = document.createElementNS(ns, tag);
  Object.entries(attributes).forEach(([k, v]) => e.setAttribute(k, v));
  if (text) e.textContent = text;
  return e;
}

function chart(id, field, min, max, color, band = false) {
  const svg = $(id), w = 780, h = 180, left = 46, right = 16, top = 15, bottom = 30;
  svg.replaceChildren();
  const x = t => left + t / 1800 * (w - left - right);
  const y = v => h - bottom - (v - min) / (max - min) * (h - top - bottom);
  if (band) svg.append(node("rect", {x:left,y:y(7.4),width:w-left-right,height:y(6)-y(7.4),fill:"#e4f3eb"}));
  for (let i = 0; i < 4; i++) {
    const value = min + (max - min) * i / 3;
    svg.append(node("line", {x1:left,y1:y(value),x2:w-right,y2:y(value),stroke:"#dfe8eb"}));
    svg.append(node("text", {x:left-8,y:y(value)+4,"text-anchor":"end"},fmt(value,1)));
  }
  for (let minute = 0; minute <= 30; minute += 5) svg.append(node("text", {x:x(minute*60),y:h-6,"text-anchor":"middle"},`${minute}m`));
  if (!simulation) return;
  const points = simulation.readings.slice(0,cursor+1);
  const step = Math.max(1,Math.floor(points.length/700));
  const sampled = points.filter((_,index) => index % step === 0 || index === points.length-1);
  const d = sampled.map((r,i) => `${i ? "L" : "M"}${x(r.second).toFixed(2)},${y(Math.max(min,Math.min(max,r[field]))).toFixed(2)}`).join(" ");
  svg.append(node("path", {d,fill:"none",stroke:color,"stroke-width":2.3,"stroke-linejoin":"round"}));
  if (points.length) svg.append(node("line", {x1:x(points.at(-1).second),x2:x(points.at(-1).second),y1:top,y2:h-bottom,stroke:"#77929b","stroke-dasharray":"3 4"}));
}

function draw() {
  if (!simulation) return;
  const r = simulation.readings[cursor];
  $("pressure").textContent = fmt(r.pressure_bar);
  $("power").textContent = fmt(r.power_kw);
  $("energy").textContent = fmt(r.energy_kwh,3);
  $("state").textContent = tf("state.line", r.state, fmt(r.temperature_c,1));
  $("units").textContent = r.accepted_units;
  $("cost").textContent = tf("cost.line", fmt(r.energy_kwh * simulation.tariff_inr_kwh), fmt(simulation.tariff_inr_kwh));
  $("sec").textContent = r.accepted_units ? tf("sec.line", fmt(r.energy_kwh/r.accepted_units,4)) : t("sec.none");
  $("clock").textContent = `${time(r.second)} / 30:00`;
  $("scrubber").value = cursor;
  $("production").textContent = tf("prod.line", r.production_active ? t("prod.active") : t("prod.paused"), fmt(r.estimated_outflow_m3_min,3));
  chart("pressure-chart","pressure_bar",5.8,7.6,"#008561",true);
  chart("power-chart","power_kw",0,9,"#177bac");
  const visible = simulation.alerts.filter(a => a.second <= r.second);
  $("alert-count").textContent = visible.length;
  $("alerts").replaceChildren();
  if (!visible.length) {
    const p = document.createElement("p");p.className="empty";
    p.textContent=t("alerts.empty");$("alerts").append(p);
  }
  visible.forEach(a => {
    const box=document.createElement("article");box.className=`alert ${a.severity}`;
    const t=document.createElement("small");t.textContent=tf("alert.first", time(a.second));
    const h=document.createElement("h3");h.textContent=t(a.title);
    const p=document.createElement("p");p.textContent=a.evidence;
    const action=document.createElement("p");action.textContent=t(a.action);
    box.append(t,h,p,action);$("alerts").append(box);
  });
  const eligible = visible.some(a => a.key === simulation.scenario) && simulation.scenario !== "normal";
  $("compare").disabled = !eligible || busy;
  $("action-description").textContent = eligible ? ({
    leak: t("Assume inspection and repair reduce leakage, then compare two complete 30-minute runs."),
    unloaded: t("Test a shorter unloaded stop delay, then compare two complete 30-minute runs."),
    filter: t("Replace the intake filter in the simulation, then compare two complete 30-minute runs."),
    worn: t("Simulate an overhaul that restores the performance map, then compare two complete 30-minute runs.")
  })[simulation.scenario] : t("Choose a fault scenario and replay until an alert appears.");
  drawAi(r.second);
  drawStage3(r.second);
  drawImpact();
}

function drawAi(second) {
  const ai = simulation.ai;
  $("ai-panel").classList.remove("is-anomaly");
  $("ai-observations").replaceChildren();
  $("ai-observations").hidden = true;
  $("ai-score").textContent = "";
  if (!ai?.enabled) {
    $("ai-title").textContent = t("ai.notready");
    $("ai-detail").textContent = t(ai?.message) || t("ai.trainmsg");
    return;
  }
  const visible = ai.windows.filter(w => w.end_second <= second);
  const current = visible.at(-1);
  if (!current) {
    $("ai-title").textContent = t("ai.collecting");
    $("ai-detail").textContent = t("ai.nowindow");
    return;
  }
  $("ai-panel").classList.toggle("is-anomaly", current.is_anomaly);
  $("ai-title").textContent = current.is_anomaly ? t("ai.flag") : t("ai.noflag");
  const flags = visible.filter(w => w.is_anomaly);
  const lastFlag = flags.at(-1);
  $("ai-detail").textContent = tf("ai.detail", time(current.start_second), time(current.end_second), flags.length) + (lastFlag ? tf("ai.lastflag", time(lastFlag.end_second)) : t("ai.noruleout"));
  $("ai-score").textContent = tf("ai.score", fmt(current.score,6), fmt(current.threshold,6));
  current.observations.forEach(text => {
    const item = document.createElement("li");item.textContent = text;$("ai-observations").append(item);
  });
  $("ai-observations").hidden = !current.observations.length;
}

function drawStage3(second) {
  const s3 = simulation.ai_stage3;
  $("stage3-panel").classList.remove("is-anomaly");
  $("stage3-cause").hidden = true;
  $("stage3-probs").hidden = true;
  $("stage3-probs").replaceChildren();
  if (!s3?.enabled) {
    $("stage3-title").textContent = t("s3.notready");
    $("stage3-detail").textContent = t(s3?.message) || t("s3.trainmsg");
    return;
  }
  const visible = s3.windows.filter(w => w.end_second <= second);
  const current = visible.at(-1);
  if (!current) {
    $("stage3-title").textContent = t("ai.collecting");
    $("stage3-detail").textContent = t("s3.nowindow");
    return;
  }
  const title = s3.class_titles[current.predicted_class] || current.predicted_class;
  $("stage3-panel").classList.toggle("is-anomaly", current.is_fault);
  $("stage3-title").textContent = current.is_fault ? tf("s3.probable", t(title)) : t("s3.nofault");
  const faults = visible.filter(w => w.is_fault);
  const last = faults.at(-1);
  $("stage3-detail").textContent = tf("s3.detail", time(current.start_second), time(current.end_second), faults.length) + (last ? tf("s3.last", time(last.end_second), t(s3.class_titles[last.predicted_class])) : "");
  const cause = $("stage3-cause");
  cause.hidden = false;
  cause.textContent = current.is_fault
    ? tf("s3.cause.fault", t(title), fmt(current.probability*100,0), fmt(current.p_normal*100,0))
    : tf("s3.cause.state", t(title), fmt(current.probability*100,0));
  const probs = $("stage3-probs");
  probs.hidden = false;
  Object.entries(current.probabilities).forEach(([cls, p]) => {
    const row = document.createElement("div");row.className = "prob-row";
    const label = document.createElement("span");label.textContent = t(s3.class_titles[cls]);
    const bar = document.createElement("span");bar.className = "prob-bar";
    const fill = document.createElement("span");fill.style.width = `${Math.min(100, p*100)}%`;
    if (cls === current.predicted_class && current.is_fault) fill.classList.add("hot");
    bar.append(fill);
    const value = document.createElement("span");value.textContent = `${fmt(p*100,0)}%`;
    row.append(label, bar, value);probs.append(row);
  });
}

function drawImpact() {
  if (!simulation) return;
  const grid = Number($("grid-factor").value) || 0.79;
  const blocksPerMonth = 24 * 60 / 30 * 30; // 1440 continuous 30-minute blocks
  const kwh = simulation.summary.energy_kwh;
  $("impact-kwh").textContent = fmt(kwh, 3);
  $("impact-inr").textContent = `₹${inr(Math.round(kwh * blocksPerMonth * simulation.tariff_inr_kwh))}`;
  $("impact-co2").textContent = inr(Math.round(kwh * blocksPerMonth * grid));
}

async function loadModelStatus() {
  try {
    const response = await fetch("/api/ai/status");
    if (!response.ok) throw new Error(t("Could not read the local model status."));
    const status = await response.json();
    const s3 = await (await fetch("/api/stage3/status")).json();
    $("model-status").textContent = tf("model.status", t(status.enabled ? "Stage 2 IF trained" : "Stage 2 IF missing"), t(s3.enabled ? "Stage 3 classifier trained" : "Stage 3 classifier missing"));
    const report = status.report;
    if (!report) {
      $("validation-message").textContent = t(status.message) || t("Run train_model.py again to create a matching model and report, then restart app.py.");
    } else {
      const m = report.metrics, events = report.event_detection;
      $("validation-message").textContent = tf("valid.summary", report.train_runs, report.calibration_runs, report.test_runs);
      [["ai-precision",m.precision],["ai-recall",m.recall],["ai-f1",m.f1],["ai-fpr",m.false_positive_rate]].forEach(([id,value]) => {
        $(id).textContent = value == null ? "N/A" : `${fmt(value*100,1)}%`;
      });
      $("ai-confusion").textContent = `Window counts: ${m.true_positive} correctly flagged faults; ${m.false_positive} false alarms; ${m.false_negative} missed fault windows; ${m.true_negative} correctly unflagged normal windows. ${report.excluded_transition_windows} windows crossing fault onset were excluded.`;
      $("ai-events").textContent = `Fault events detected: ${events.detected}/${events.total} (${events.total-events.detected} missed). Median delay among detected events: ${fmt(events.median_delay_seconds_among_detected,0)} seconds. False-alarm episodes in ${fmt(report.normal_run_hours,1)} hours of entirely normal test runs: ${report.normal_run_alarm_episodes}.`;
      $("ai-performance-note").textContent = `The detector missed ${fmt(100*(1-m.recall),1)}% of labelled fault windows. One flag can count as detecting an event, so event detection and window recall answer different questions. Treat this as an experimental baseline; engineering rules remain available.`;
      $("validation-results").hidden = false;
      $("download-report").disabled = false;
    }
    const s3report = s3.report;
    if (!s3report) {
      $("stage3-message").textContent = t(s3.message) || t("Run train_stage3.py, then restart app.py.");
    } else {
      const ev = s3report.event_detection;
      $("stage3-val-title").textContent = tf("s3.valtitle", s3report.train_runs_per_scenario, s3report.test_runs_per_scenario);
      $("s3-f1").textContent = `${fmt(s3report.macro_f1*100,1)}%`;
      $("s3-target").textContent = s3report.macro_f1 >= 0.85 ? t("s3.target.met") : t("Target: ≥ 85%");
      $("s3-target").classList.toggle("target-met", s3report.macro_f1 >= 0.85);
      $("s3-events").textContent = `${ev.detected}/${ev.total}`;
      $("s3-delay").textContent = ev.median_delay_seconds_among_detected == null ? "N/A" : `${fmt(ev.median_delay_seconds_among_detected,0)} s`;
      $("s3-fa").textContent = `${s3report.normal_run_alarm_episodes} in ${fmt(s3report.normal_run_hours,1)} h`;
      $("stage3-rows").replaceChildren();
      Object.entries(s3report.per_class).forEach(([cls, m]) => {
        const row = document.createElement("tr");
        [t(s3report.class_titles[cls] || cls), `${fmt(m.precision*100,1)}%`, `${fmt(m.recall*100,1)}%`, `${fmt(m.f1*100,1)}%`].forEach(text => {
          const cell = document.createElement("td");cell.textContent = text;row.append(cell);
        });
        $("stage3-rows").append(row);
      });
      $("stage3-note").textContent = `Stage 3 test seeds are reserved and disjoint from Stage 2. Window recall is bounded when a fault is not observable in a given 60 s operating state; event-level hybrid detection combines the classifier with engineering rules.`;
      $("stage3-results").hidden = false;
      $("download-stage3").disabled = false;
    }
  } catch (error) {
    $("model-status").textContent = t("Model status unavailable");
    $("validation-message").textContent = error.message;
  }
}

function stop() {clearInterval(timer);timer=null;$("play").textContent=t("Play");}
function play() {
  if (!simulation || busy) return;
  if (timer) {stop();return;}
  if (cursor >= simulation.readings.length-1) cursor=0;
  $("play").textContent=t("Pause");
  timer=setInterval(() => {
    cursor=Math.min(simulation.readings.length-1,cursor+Number($("speed").value)/2);
    cursor=Math.floor(cursor);draw();
    if (cursor === simulation.readings.length-1) stop();
  },500);
}

function setBusy(value) {
  busy=value;$("run").disabled=value;$("scenario").disabled=value;$("tariff").disabled=value;
  $("play").disabled=value||!simulation;$("scrubber").disabled=value||!simulation;$("export").disabled=value||!simulation;
  $("compare").disabled=true;
}
async function api(path, payload) {
  const response=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
  const data=await response.json();
  if(!response.ok)throw new Error(data.error||"The request could not be completed.");
  return data;
}
function showError(error) {$("error").textContent=error.message||"Could not reach the local app. Check that app.py is still running.";$("error").hidden=false;}

async function loadScenario(event) {
  if(event)event.preventDefault();stop();setBusy(true);$("error").hidden=true;
  try {
    simulation=await api("/api/simulate",{scenario:$("scenario").value,tariff:Number($("tariff").value)});
    cursor=0;$("scenario-title").textContent=simulation.label;$("comparison").hidden=true;
    setBusy(false);draw();play();
    loadCatalog();
  } catch(error) {showError(error);setBusy(false);if(simulation)draw();}
}

async function compare() {
  if(!simulation||busy)return;stop();setBusy(true);$("error").hidden=true;
  try {
    const result=await api("/api/compare",{scenario:simulation.scenario,tariff:simulation.tariff_inr_kwh});
    $("comparison-action").textContent=`${t(result.action)} ${t(result.scope)}`;
    $("saving-percent").textContent=`${fmt(result.energy_reduction_pct)}%`;
    $("saving-detail").textContent=tf("saving.detail", fmt(result.savings_kwh,3), fmt(result.savings_inr));
    $("comparison-status").textContent=result.simulation_guardrails_pass ? t("SIMULATED PRESSURE & OUTPUT CHECKS PASS") : t("REVIEW SIMULATION CONSTRAINTS");
    $("comparison-limitation").textContent=t(result.limitation);
    const definitions=[
      ["Electricity", "energy_kwh",3," kWh"], ["Specific energy", "sec_kwh_per_unit",4," kWh/unit"],
      ["Simulated accepted units","accepted_units",0,""], ["Pressure-affected cycles","pressure_failed_cycles",0,""],
      ["Within pressure band","pressure_compliance_pct",2,"%"], ["Unloaded time","unloaded_seconds",0," s"],
      ["Peak power","peak_power_kw",2," kW"], ["Final receiver pressure","final_pressure_bar",3," bar(g)"]
    ];
    $("comparison-rows").replaceChildren();
    definitions.forEach(([label,key,digits,unit])=>{
      const row=document.createElement("tr");
      [t(label),`${fmt(result.baseline[key],digits)}${unit}`,`${fmt(result.after[key],digits)}${unit}`].forEach(text=>{const cell=document.createElement("td");cell.textContent=text;row.append(cell);});
      $("comparison-rows").append(row);
    });
    $("comparison").hidden=false;$("comparison").scrollIntoView({behavior:"smooth",block:"start"});
  } catch(error) {showError(error);} finally {setBusy(false);draw();}
}

function exportCsv() {
  if(!simulation)return;
  const keys=Object.keys(simulation.readings[0]);
  const body=[keys.join(","),...simulation.readings.map(r=>keys.map(k=>String(r[k])).join(","))].join("\r\n");
  const url=URL.createObjectURL(new Blob([body],{type:"text/csv;charset=utf-8"}));
  const a=document.createElement("a");a.href=url;a.download=`FactoryAirTwin_${simulation.scenario}_SIMULATED.csv`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}

/* ------------------------- Stage 3 optimiser panels ------------------------- */

function resultBox(id, html) {const box=$(id);box.innerHTML="";const p=document.createElement("p");p.innerHTML=html;box.append(p);}

async function runSetpoint() {
  if(!simulation)return;
  resultBox("setpoint-result",t("setpoint.searching"));
  try {
    const r = await api("/api/optimise/setpoint",{scenario:simulation.scenario,tariff:simulation.tariff_inr_kwh});
    if (!r.recommended) {
      resultBox("setpoint-result",t("setpoint.none"));
      return;
    }
    const rec = r.recommended;
    resultBox("setpoint-result",
      tf("setpoint.rec2", `${fmt(rec.cut_in_bar,1)} / ${fmt(rec.cut_out_bar,1)}`, fmt(rec.drop_bar,1), fmt(rec.savings_kwh,3),
         inr(rec.monthly_inr), inr(rec.co2_kg_month), fmt(rec.minimum_pressure_bar,2), fmt(rec.peak_power_kw,2)));
  } catch(error) {resultBox("setpoint-result",error.message);}
}

async function runSequence() {
  resultBox("sequence-result",t("seq.running"));
  try {
    const r = await api("/api/optimise/sequence",{tariff:simulation?simulation.tariff_inr_kwh:Number($("tariff").value)});
    let html = tf("seq.top", `<strong>${t(r.recommended)}</strong>`, fmt(r.saving_kwh_per_30min,3), inr(r.monthly_inr), inr(r.co2_kg_month)) + "<br>";
    Object.entries(r.strategies).forEach(([name, s]) => {
      const unloadedTotal = Object.values(s.unloaded_seconds).reduce((a,b)=>a+b,0);
      html += tf("seq.line", t(name), fmt(s.energy_kwh,3), unloadedTotal, fmt(s.minimum_pressure_bar,2)) + "<br>";
    });
    html += `<small>${r.note}</small>`;
    resultBox("sequence-result",html);
  } catch(error) {resultBox("sequence-result",error.message);}
}

async function runSchedule() {
  if(!simulation||!simulation.summary.sec_kwh_per_unit){resultBox("schedule-result",t("sched.needload"));return;}
  resultBox("schedule-result",t("sched.running"));
  try {
    const r = await api("/api/schedule",{
      sec_kwh_per_unit: simulation.summary.sec_kwh_per_unit,
      units_per_day: Number($("sched-units").value), shiftable_pct: Number($("sched-shift").value),
      peak_rate: Number($("sched-peak").value), off_peak_rate: Number($("sched-off").value),
      solar_rate: Number($("sched-solar").value), solar_share_pct: Number($("sched-solarshare").value)
    });
    resultBox("schedule-result",
      tf("sched.result2", fmt(r.daily_energy_kwh,1), fmt(r.shifted_kwh_per_day,1), inr(r.monthly_saving_inr), inr(r.co2_kg_month_solar_displacement)) +
      ` <small>${r.note}</small>`);
  } catch(error) {resultBox("schedule-result",error.message);}
}

async function loadCatalog() {
  if(!simulation)return;
  $("catalog-rows").replaceChildren();
  const loading=document.createElement("tr");const cell=document.createElement("td");
  cell.colSpan=8;cell.textContent=t("catalog.building");loading.append(cell);$("catalog-rows").append(loading);
  try {
    const r = await api("/api/catalog",{scenario:simulation.scenario,tariff:simulation.tariff_inr_kwh});
    $("catalog-rows").replaceChildren();
    if (!r.actions.length) {
      const row=document.createElement("tr");const c=document.createElement("td");
      c.colSpan=8;c.textContent=t("catalog.empty");
      row.append(c);$("catalog-rows").append(row);
      return;
    }
    r.actions.forEach((a,i) => {
      const row=document.createElement("tr");
      const cells=[String(i+1),t(a.title),t(a.action),inr(a.monthly_kwh),`₹${inr(a.monthly_inr)}`,inr(a.co2_kg_month),
        a.assumed_cost_inr?`₹${inr(a.assumed_cost_inr)}`:t("₹0 (settings)"),a.assumed_cost_inr?tf("days", fmt(a.payback_days,0)):t("Immediate")];
      cells.forEach(text=>{const c=document.createElement("td");c.textContent=text;row.append(c);});
      row.title=a.basis;
      $("catalog-rows").append(row);
    });
    $("catalog-note").textContent=tf("catalog.note", r.assumptions.note, r.assumptions.grid_co2_kg_per_kwh);
  } catch(error) {
    $("catalog-rows").replaceChildren();
    const row=document.createElement("tr");const c=document.createElement("td");
    c.colSpan=8;c.textContent=error.message;row.append(c);$("catalog-rows").append(row);
  }
}

async function loadSample() {
  try {
    const r = await fetch("/api/connectivity/sample");
    const data = await r.json();
    $("sample-payload").textContent =
`MQTT topics:
${data.mqtt.topics.join("\n")}

Sample window payload:
${JSON.stringify(data.mqtt.payload, null, 2)}

Modbus register map (${data.modbus.note}):
${data.modbus.registers.map(x=>`  ${x.address}  ${x.signal}  [${x.unit}]`).join("\n")}`;
    $("sample-payload").hidden=false;
    $("sample-note").textContent=data.schneider_alignment;
  } catch(error) { $("sample-note").textContent=error.message; }
}

$("controls").addEventListener("submit",loadScenario);
$("play").addEventListener("click",play);
$("scrubber").addEventListener("input",()=>{stop();cursor=Number($("scrubber").value);draw();});
$("compare").addEventListener("click",compare);
$("export").addEventListener("click",exportCsv);
$("download-report").addEventListener("click",() => {window.location.href="/api/ai/report";});
$("download-stage3").addEventListener("click",() => {window.location.href="/api/stage3/report";});
$("tariff").addEventListener("change",drawImpact);
$("grid-factor").addEventListener("input",drawImpact);
$("setpoint-run").addEventListener("click",runSetpoint);
$("sequence-run").addEventListener("click",runSequence);
$("schedule-run").addEventListener("click",runSchedule);
$("load-sample").addEventListener("click",loadSample);
loadModelStatus();
loadScenario();

/* ------------------------------ Stage 4 panels ------------------------------ */

function ctx() {
  const role = $("role-select") ? $("role-select").value : "manager";
  const name = $("operator-name") && $("operator-name").value ? $("operator-name").value.trim() : "operator";
  return {role, operator: name};
}

async function api2(url, body, method = "POST") {
  const payloadBody = method === "POST" ? Object.assign({}, body || {}, ctx()) : body;
  const response = await fetch(url, method === "POST" ? {
    method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(payloadBody)
  } : {method});
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

async function loadStage4() {
  try {
    const cfg = await api2("/api/stage4/config", null, "GET");
    $("cfg-site").value = cfg.site_name || "";
    $("cfg-kw").value = cfg.rated_power_kw; $("cfg-vol").value = cfg.receiver_volume_m3;
    $("cfg-min").value = cfg.min_pressure_bar; $("cfg-in").value = cfg.cut_in_bar;
    $("cfg-out").value = cfg.cut_out_bar; $("cfg-max").value = cfg.max_pressure_bar;
    $("cfg-tariff").value = cfg.tariff_inr_kwh;
  } catch (error) { resultBox("cfg-result", error.message); }
  refreshTickets();
}

async function saveConfig() {
  try {
    const cfg = await api2("/api/stage4/config", {
      site_name: $("cfg-site").value, rated_power_kw: Number($("cfg-kw").value),
      receiver_volume_m3: Number($("cfg-vol").value), min_pressure_bar: Number($("cfg-min").value),
      cut_in_bar: Number($("cfg-in").value), cut_out_bar: Number($("cfg-out").value),
      max_pressure_bar: Number($("cfg-max").value), tariff_inr_kwh: Number($("cfg-tariff").value)
    });
    resultBox("cfg-result", `Saved ✔ Calibration stored for <strong>${cfg.site_name}</strong> (updated ${cfg.updated_at}).`);
  } catch (error) { resultBox("cfg-result", error.message); }
}

async function runHealth() {
  const corruptions = [];
  if ($("cor-dropout").checked) corruptions.push("dropout");
  if ($("cor-stuck").checked) corruptions.push("stuck_pressure");
  if ($("cor-noise").checked) corruptions.push("power_noise");
  if ($("cor-range").checked) corruptions.push("range_violation");
  resultBox("health-result", t("health.checking"));
  try {
    const r = await api2("/api/stage4/sensorhealth", {scenario: $("health-scenario").value, corruptions});
    const badge = {ok: t("health.ok"), degraded: t("health.degraded"), failed: t("health.failed")}[r.overall] || r.overall;
    let html = `<strong>${badge}</strong> · ${r.samples_checked} samples · sensors: ` +
      Object.entries(r.sensors).map(([k, s]) => `${k.split("_")[0]} ${t("w." + s.status)}`).join(" · ") + "<br>";
    html += r.issues.length ? r.issues.map(i => `• ${i}`).join("<br>") : "• No quality problems found.";
    if (r.corruptions_applied.length) html += `<br><small>${tf("health.injected", r.corruptions_applied.join(", "))}</small>`;
    resultBox("health-result", html);
  } catch (error) { resultBox("health-result", error.message); }
}

async function runDiagnose(createTicket = false) {
  const scenario = $("diag-scenario").value;
  resultBox("diag-result", createTicket ? t("diag.ticketing") : t("diag.running"));
  try {
    const r = await api2("/api/stage4/diagnose", {scenario});
    const d = r.diagnosis;
    const badge = {known: t("diag.known"), multiple: t("diag.multiple"), unknown: t("diag.unknown"), normal: t("diag.normal")}[d.state] || d.state;
    let html = `<strong>${badge}</strong><br>${t(d.explanation)}<br>` +
      `<small>${tf("diag.line", fmt(d.confidence, 2), d.rule_keys.join(", ") || "—", d.ai_flagged_windows, fmt(d.anomaly_ratio, 2))}</small>`;
    if (createTicket) {
      if (scenario === "normal") throw new Error("Run the diagnosis on a fault scenario first.");
      const tk = await api2("/api/stage4/ticket/from-diagnosis", {scenario});
      html += `<br><strong>${tf("diag.ticketed", tk.id, tk.fault_type)}</strong>`;
      refreshTickets();
    }
    resultBox("diag-result", html);
  } catch (error) { resultBox("diag-result", error.message); }
}

const NEXT_ACTIONS = {
  open: [["acknowledge", "Acknowledge"]],
  acknowledged: [["start_repair", "Start repair"], ["cancel", "Cancel"]],
  in_repair: [["verify", "Verify repair ✔"], ["cancel", "Cancel"]],
  verifying: [["resolve", "Resolve"]],
  resolved: [["reopen", "Reopen"]],
  cancelled: [["reopen", "Reopen"]]
};

async function refreshTickets() {
  try {
    const tickets = await api2("/api/maintenance/tickets", null, "GET");
    const tbody = $("ticket-rows");
    tbody.replaceChildren();
    if (!tickets.length) {
      const row = document.createElement("tr"), c = document.createElement("td");
      c.colSpan = 6; c.textContent = t("ticket.empty");
      row.append(c); tbody.append(row); return;
    }
    tickets.slice().reverse().forEach(tk => {
      const row = document.createElement("tr");
      const verification = tk.verification
        ? (tk.verification.status === "measured_simulated"
          ? tf("ticket.saved", tk.verification.saved_kwh_30min, inr(tk.verification.saved_inr_per_month)) +
            (tk.verification.deviation_pct_vs_predicted != null ? ` (${tk.verification.deviation_pct_vs_predicted}% vs predicted)` : "")
          : tk.verification.message || tk.verification.status)
        : "—";
      const cells = [tk.id, tk.fault_type, tk.title, t(tk.state), verification];
      cells.forEach(text => { const c = document.createElement("td"); c.textContent = text; row.append(c); });
      const actionsCell = document.createElement("td");
      (NEXT_ACTIONS[tk.state] || []).forEach(([action, label]) => {
        if (!roleCan(ACTION_PERMS[action])) return;
        const b = document.createElement("button"); b.type = "button"; b.textContent = label;
        b.addEventListener("click", () => ticketAct(tk.id, action));
        actionsCell.append(b, " ");
      });
      row.append(actionsCell);
      row.title = `Logs: ${tk.logs.map(l => `${l.at} ${l.operator} ${l.action}${l.note ? " — " + l.note : ""}`).join(" | ")}`;
      tbody.append(row);
    });
  } catch (error) {
    $("ticket-rows").replaceChildren();
    resultBox("cfg-result", error.message);
  }
}

async function ticketAct(id, action) {
  try {
    if (action === "verify") {
      const v = await api2(`/api/maintenance/tickets/${id}/verify`, {operator: "dashboard"});
      const message = v.status === "measured_simulated"
        ? tf("ticket.verified", v.saved_kwh_30min, inr(v.saved_inr_per_month))
        : t(v.message);
      resultBox("comb-result", `<strong>${id}:</strong> ${message}`);
    } else {
      await api2(`/api/maintenance/tickets/${id}/action`, {action, operator: "dashboard"});
    }
    refreshTickets();
  } catch (error) { resultBox("comb-result", error.message); }
}

async function runCombined() {
  const actions = [];
  if ($("act-repair").checked) actions.push("repair_leak");
  if ($("act-setpoint").checked) actions.push("setpoint");
  if ($("act-sequence").checked) actions.push("sequencing");
  if ($("act-schedule").checked) actions.push("scheduling");
  if (!actions.length) { resultBox("comb-result", t("comb.pick")); return; }
  resultBox("comb-result", t("comb.running"));
  try {
    const r = await api2("/api/optimise/combined", {scenario: $("comb-scenario").value, actions});
    let html = `<strong>${tf("comb.total", inr(r.total_inr_per_month), inr(r.total_co2_kg_per_month), fmt(r.pressure_compliance_pct, 1))}</strong><br>`;
    r.parts.forEach(p => { html += `• ${t(p.action)}: ${p.inr_per_month != null ? "₹" + inr(p.inr_per_month) + "/" + t("month") : "n/a"}<br>`; });
    html += `<small>${t(r.double_counting_note)}</small>`;
    resultBox("comb-result", html);
  } catch (error) { resultBox("comb-result", error.message); }
}

async function runStress() {
  resultBox("stress-result", t("stress.running"));
  try {
    const r = await api2("/api/stage4/stress", {});
    let html = `<table><thead><tr><th>${t("th.test")}</th><th>${t("th.health")}</th><th>${t("th.alerts")}</th><th>${t("th.diag")}</th></tr></thead><tbody>`;
    r.results.forEach(x => {
      const badge = {ok: "🟢", degraded: "🟡", failed: "🔴"}[x.health_overall] || "";
      html += `<tr><td>${x.test}<br><small>${t(x.description)}</small></td><td>${badge} ${t("w." + x.health_overall)}</td><td>${x.alerts_raised.join(", ") || "—"}</td><td>${t(x.diagnosis_state)}${x.diagnosis_faults.length ? " (" + x.diagnosis_faults.map(f => t(f)).join("+") + ")" : ""}</td></tr>`;
    });
    html += `</tbody></table><small>${r.note}</small>`;
    resultBox("stress-result", html);
  } catch (error) { resultBox("stress-result", error.message); }
}

$("cfg-save").addEventListener("click", saveConfig);
$("health-run").addEventListener("click", runHealth);
$("diag-run").addEventListener("click", () => runDiagnose(false));
$("diag-ticket").addEventListener("click", () => runDiagnose(true));
$("comb-run").addEventListener("click", runCombined);
$("stress-run").addEventListener("click", runStress);
loadStage4();


/* --------------------- Stage 4.5: roles / backup / history / i18n --------------------- */

const ROLE_CAN = {
  operator: ["view", "create_ticket", "acknowledge", "backup"],
  maintenance_engineer: ["view", "create_ticket", "acknowledge", "start_repair", "verify",
                         "resolve", "cancel", "reopen", "diagnose", "combined", "sensorhealth", "backup"],
  manager: ["view", "create_ticket", "acknowledge", "start_repair", "verify", "resolve", "cancel",
            "reopen", "diagnose", "combined", "sensorhealth", "config", "stress", "report",
            "restore", "backup"]
};
const ACTION_PERMS = {acknowledge: "acknowledge", start_repair: "start_repair", verify: "verify",
                      resolve: "resolve", cancel: "cancel", reopen: "reopen"};

function roleCan(perm) {
  const role = $("role-select") ? $("role-select").value : "manager";
  return (ROLE_CAN[role] || []).includes(perm);
}

function applyRoleGating() {
  const roleLabel = $("role-select").selectedOptions[0].textContent;
  $("role-current").textContent = roleLabel;
  const gates = [["cfg-save", "config"], ["stress-run", "stress"], ["restore-run", "restore"],
                 ["diag-run", "diagnose"], ["diag-ticket", "create_ticket"], ["comb-run", "combined"],
                 ["health-run", "sensorhealth"], ["cfg-site", "config"], ["cfg-kw", "config"],
                 ["cfg-vol", "config"], ["cfg-min", "config"], ["cfg-in", "config"],
                 ["cfg-out", "config"], ["cfg-max", "config"], ["cfg-tariff", "config"]];
  gates.forEach(([id, perm]) => { const el = $(id); if (el) el.disabled = !roleCan(perm); });
  $("pdf-link").style.opacity = roleCan("report") ? "1" : "0.45";
  $("pdf-link").style.pointerEvents = roleCan("report") ? "auto" : "none";
  const r = $("role-select").value;
  $("pdf-link").href = "/api/report/pdf?role=" + r;
  $("role-matrix").innerHTML = t("role.matrix");
}

async function runBackup() {
  window.location.href = "/api/stage4/backup?role=" + $("role-select").value;
  resultBox("backup-result", t("backup.downloading"));
}

async function runRestore() {
  const file = $("restore-file").files[0];
  if (!file) { resultBox("backup-result", t("backup.choose")); return; }
  const form = new FormData();
  form.append("backup", file);
  const query = new URLSearchParams(ctx()).toString();
  try {
    const response = await fetch("/api/stage4/restore?" + query, {method: "POST", body: form});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || "Restore failed.");
    resultBox("backup-result", tf("backup.restored", data.restored.join(", ")));
  } catch (error) { resultBox("backup-result", error.message); }
}

async function runHistory() {
  resultBox("history-result", t("history.loading"));
  try {
    const r = await api2("/api/stage4/modelhistory", null, "GET");
    let html = "";
    r.history.forEach(m => {
      html += `<strong>${m.model}</strong><br>`;
      if (!m.trained) { html += `<small>${m.note}</small><br><br>`; return; }
      html += tf("hist.trained", m.trained_at, m.sklearn_version) + "<br>";
      if (m.model_file_modified_utc) html += `<small>${tf("hist.file", m.model_file_modified_utc)}</small><br>`;
      if (m.evaluation) {
        const e = m.evaluation;
        const bits = [];
        if (e.macro_f1 != null) bits.push(`macro-F1 ${(e.macro_f1 * 100).toFixed(1)}%`);
        if (e.events_detected != null) bits.push(`events ${e.events_detected}`);
        if (e.per_class_f1) bits.push(Object.entries(e.per_class_f1).map(([k, v]) => `${k} ${(v * 100).toFixed(0)}%`).join(" · "));
        html += `<small>${bits.join(" · ")}</small><br>`;
      }
      html += "<br>";
    });
    html += `<small>${r.note}</small>`;
    resultBox("history-result", html);
  } catch (error) { resultBox("history-result", error.message); }
}

let LANG = "en";
const TA = {
  /* topbar / chrome */
  "TEAM VOLTS AND BOLTS": "வோல்ட்ஸ் & போல்ட்ஸ் அணி",
  "SIMULATED DATA · STAGE 4.5": "உருவகத் தரவு · நிலை 4.5",
  "Role": "பொறுப்பு", "Name": "பெயர்",
  "Operator": "இயக்குநர்", "Maintenance engineer": "பராமரிப்பு பொறியாளர்", "Plant manager": "ஆலை மேலாளர்",
  "SIMULATED · OPERATOR-LED": "உருவகம் · இயக்குநர் தலைமையில்",
  "PRODUCTION-AWARE · SIMULATED": "உற்பத்தி-விழிப்பு · உருவகம்",
  /* page title / toolbar */
  "COMPRESSOR ROOM / 01": "அமுக்கி அறை / 01",
  "Energy & operation monitor": "ஆற்றல் & செயல்பாட்டு கண்காணிப்பு",
  "Replay a 30-minute shift, detect hidden air losses and rank the cheapest fixes.":
    "ஒரு 30-நிமிட ஷிப்ட்டை மீண்டும் இயக்கி, மறைந்த காற்று இழப்புகளைக் கண்டறிந்து, மலிவான தீர்வுகளை வரிசைப்படுத்தவும்.",
  "Model assumptions": "மாதிரி அனுமானங்கள்",
  "Operating scenario": "இயக்க சூழல்", "Assumed price · ₹/kWh": "விலை அனுமானம் · ₹/kWh",
  "Rules + Stage 2 IF + Stage 3 classifier": "விதிகள் + நிலை 2 + நிலை 3 வகைப்படுத்தி",
  "Checking local models…": "உள்ளூர் மாதிரிகள் சரிபார்க்கப்படுகின்றன…",
  "Evaluation": "மதிப்பீடு",
  "Load scenario": "சூழலை இயக்கு", "Export full run": "முழு இயக்கத்தை ஏற்றுமதி செய்",
  "opt_normal": "சாதாரண இயக்கம்", "opt_leak": "காற்று கசிவு சந்தேகம்",
  "opt_unloaded": "நீடித்த சுமையற்ற இயக்கம்", "opt_filter": "படிப்படியான வடிகட்டி அடைப்பு",
  "opt_worn": "அமுக்கி தேய்மானம்",
  "Normal": "சாதாரணம்", "Leak": "கசிவு", "Unloaded": "சுமையற்றது", "Filter": "வடிகட்டி", "Worn": "தேய்மானம்",
  /* metrics */
  "Header pressure": "ஹெடர் அழுத்தம்", "Demo band: 6.0–7.4 bar(g)": "சோதனை வரம்பு: 6.0–7.4 பார்",
  "Electrical power": "மின் திறன்", "Waiting for simulation": "உருவகத்திற்காக காத்திருக்கிறது",
  "Electricity used": "பயன்படுத்திய மின்சாரம்", "Illustrative electricity cost": "விளக்க மின்சார செலவு",
  "Simulated accepted units": "உருவக ஏற்பு அலகுகள்", "Pressure-based production proxy": "அழுத்த அடிப்படையிலான உற்பத்தி அளவீடு",
  "units": "அலகுகள்",
  /* monitoring panel */
  "OPERATING HISTORY": "இயக்க வரலாறு",
  "Normal operation": "சாதாரண இயக்கம்", "Suspected air leak": "காற்று கசிவு சந்தேகம்",
  "Extended unloaded running": "நீடித்த சுமையற்ற இயக்கம்", "Progressive filter blockage": "படிப்படியான வடிகட்டி அடைப்பு",
  "Worn compressor": "அமுக்கி தேய்மானம்",
  "bar(g) · shaded operating band": "பார் · நிழலிடப்பட்ட இயக்க வரம்பு",
  "Compressor power": "அமுக்கி திறன்",
  "Play": "இயக்கு", "Pause": "இடைநிறுத்து", "Replay position": "மீளியக்க நிலை", "Speed": "வேகம்",
  "Production follows a fixed schedule in every comparison.": "ஒவ்வொரு ஒப்பீட்டிலும் உற்பத்தி நிலையான அட்டவணையைப் பின்பற்றுகிறது.",
  /* investigation */
  "INVESTIGATION": "விசாரணை", "Alerts & next action": "எச்சரிக்கைகள் & அடுத்த நடவடிக்கை",
  "Load and replay a scenario to inspect operating signals.": "இயக்க சிக்னல்களை ஆய்வு செய்ய ஒரு சூழலை இயக்கி மீண்டும் இயக்கவும்.",
  "EXPERIMENTAL ANOMALY DETECTOR · STAGE 2": "சோதனை முரண்பாட்டு கண்டறிதல் · நிலை 2",
  "Waiting for model status": "மாதிரி நிலைக்கு காத்திருக்கிறது",
  "Scores a trailing 60-second window every 30 seconds.": "ஒவ்வொரு 30 வினாடிக்கும் ஒரு 60-வினாடி சாளரம் மதிப்பிடப்படும்.",
  "An unusual pattern needs investigation. The score is not a fault probability or a confirmed cause. Range observations are not model explanations.":
    "அசாதாரண வடிவத்திற்கு விசாரணை தேவை. ஸ்கோர் என்பது குறைபாட்டு நிகழ்தகவோ உறுதிப்படுத்தப்பட்ட காரணமோ அல்ல.",
  "FAULT-TYPE CLASSIFIER · STAGE 3": "குறைபாட்டு வகை வகைப்படுத்தி · நிலை 3",
  "Names the probable fault from the same sensor-light signals.": "அதே சென்சார்-குறைந்த சிக்னல்களிலிருந்து சாத்தியமான குறைபாட்டை பெயரிடுகிறது.",
  "Trained on labelled simulated runs. Probabilities are model outputs, not field failure odds. Operator approval is required before any real action.":
    "லேபிள் செய்யப்பட்ட உருவக இயக்கங்களில் பயிற்றுவிக்கப்பட்டது. நிகழ்தகவுகள் மாதிரி வெளியீடுகள்; எந்த உண்மையான நடவடிக்கைக்கும் இயக்குநர் ஒப்புதல் தேவை.",
  "Test an intervention": "தலையீட்டை சோதிக்கவும்",
  "Choose a fault scenario and replay until an alert appears.": "குறைபாட்டு சூழலைத் தேர்ந்தெடுத்து, எச்சரிக்கை தோன்றும் வரை மீண்டும் இயக்கவும்.",
  "Approve & simulate action": "அங்கீகரித்து செயலை உருவகப்படுத்து",
  "Runs a matched simulation. No physical equipment is connected.": "பொருந்திய உருவகம் இயக்கப்படும்; உண்மையான உபகரணம் இணைக்கப்படவில்லை.",
  /* comparison */
  "BEFORE / AFTER": "முன் / பின்", "Calculated simulation results": "கணக்கிடப்பட்ட உருவக முடிவுகள்",
  "Electricity reduction": "மின்சாரக் குறைப்பு",
  "Metric": "அளவீடு", "Baseline": "அடிப்படை", "After action": "செயலுக்குப் பின்",
  "Electricity": "மின்சாரம்", "Specific energy": "தனித்துவ ஆற்றல்",
  "Pressure-affected cycles": "அழுத்தத்தால் பாதிக்கப்பட்ட சுழற்சிகள்", "Within pressure band": "அழுத்த வரம்பிற்குள்",
  "Unloaded time": "சுமையற்ற நேரம்", "Peak power": "உச்ச திறன்", "Final receiver pressure": "இறுதி ரிசீவர் அழுத்தம்",
  "SIMULATED PRESSURE & OUTPUT CHECKS PASS": "உருவக அழுத்த & வெளியீட்டு சரிபார்ப்புகள் தேர்ச்சி",
  "REVIEW SIMULATION CONSTRAINTS": "உருவக கட்டுப்பாடுகளை மதிப்பாய்வு செய்யவும்",
  /* optimiser */
  "OPTIMISER / SAVINGS ROADMAP": "உகப்பாக்கி / சிக்கன வரைபடம்",
  "What should be done first?": "முதலில் என்ன செய்ய வேண்டும்?",
  "Run electricity": "இயக்க மின்சாரம்", "Projected cost": "எதிர்பார்க்கப்படும் செலவு",
  "₹ / month, 24×7 extrapolation": "₹ / மாதம், 24×7 நீட்டிப்பு",
  "Projected emissions": "எதிர்பார்க்கப்படும் உமிழ்வு", "Grid factor kg CO₂e/kWh": "கிரிட் காரணி கி.கி CO₂e/kWh",
  "1 · Pressure setpoint": "1 · அழுத்த செட்பாயிண்ட்",
  "Searches lower cut-in / cut-out bands with full matched re-runs and accepts only options that keep output, pressure compliance ≥ 99% and the minimum-pressure guardrail.":
    "குறைந்த கட்-இன்/கட்-அவுட் வரம்புகளை முழு பொருத்திய மறு-இயக்கங்களுடன் தேடுகிறது; வெளியீடு, ≥99% அழுத்த இணக்கம், குறைந்தபட்ச அழுத்த பாதுகாப்பு ஆகியவற்றை காக்கும் விருப்பங்களே ஏற்கப்படும்.",
  "Find safest lower setpoint": "பாதுகாப்பான குறைந்த செட்பாயிண்டை கண்டறி",
  "2 · Multi-compressor sequencing": "2 · பல-அமுக்கி வரிசைப்படுத்தல்",
  "Simulates a two-compressor room on the same demand schedule and shows which machine should carry base load. The unit with lower specific power should lead.":
    "அதே தேவை அட்டவணையில் இரண்டு-அமுக்கி அறையை உருவகித்து, எந்த இயந்திரம் அடிப்படை சுமையை ஏற்க வேண்டும் என காட்டுகிறது.",
  "Compare sequences": "வரிசைகளை ஒப்பிடு",
  "3 · Tariff-aware scheduling": "3 · கட்டண-விழிப்பு திட்டமிடல்",
  "Moves a configurable share of daily compressed-air energy out of peak tariff hours into off-peak or solar-rich periods. Illustrative arithmetic, not a physics run.":
    "தினசரி அழுத்தக் காற்று ஆற்றலின் ஒரு பகுதியை உச்ச கட்டண நேரங்களிலிருந்து உச்சமல்லாத / சூரிய நேரங்களுக்கு மாற்றுகிறது. இது விளக்க கணிதமே, இயற்பியல் இயக்கம் அல்ல.",
  "Units/day": "அலகுகள்/நாள்", "Shiftable %": "மாற்றக்கூடிய %", "Peak ₹": "உச்ச நேர ₹",
  "Off-peak ₹": "உச்சமல்லாத ₹", "Solar ₹": "சூரிய ₹", "Solar share %": "சூரிய பங்கு %",
  "Estimate schedule savings": "திட்ட சிக்கனத்தை மதிப்பிடு",
  "Ranked intervention catalogue": "தரவரிசை தலையீட்டு பட்டியல்",
  "Monthly figures extrapolate the 30-minute pattern continuously; costs are editable illustrative defaults.":
    "மாதாந்திர எண்கள் 30-நிமிட வடிவத்தை தொடர்ச்சியாக நீட்டிக்கின்றன; செலவுகள் திருத்தக்கூடிய விளக்க இயல்புநிலைகள்.",
  "#": "#", "Action": "நடவடிக்கை", "What to do": "என்ன செய்வது",
  "kWh / month": "kWh / மாதம்", "₹ / month": "₹ / மாதம்", "kg CO₂e / month": "கி.கி CO₂e / மாதம்",
  "Assumed cost": "அனுமான செலவு", "Payback": "மீட்புக்காலம்", "Loading catalogue…": "பட்டியல் ஏற்றப்படுகிறது…",
  "Lower pressure band": "அழுத்த வரம்பை குறை",
  "Locate and repair the leak (ultrasonic survey / isolation test).": "கசிவை கண்டறிந்து சரிசெய்யவும் (அல்ட்ராசோனிக் ஆய்வு / தனிமைப்படுத்தல் சோதனை).",
  "Set the efficient unit as base-load lead; use the smaller unit for peak trimming.": "திறன்மிக்க இயந்திரத்தை அடிப்படை சுமைக்கு முன்னிறுத்தவும்; உச்ச நேரத்திற்கு சிறிய இயந்திரத்தை பயன்படுத்தவும்.",
  /* stage 4 */
  "STAGE 4 / PRODUCTION READINESS": "நிலை 4 / உற்பத்தி-தயார்நிலை",
  "From prototype to plant-ready workflow": "முன்மாதிரியிலிருந்து ஆலை-தயார் பணிப்பாய்வு வரை",
  "1 · Plant setup & calibration": "1 · ஆலை அமைப்பு & அளவீடு",
  "Site calibration values used by Stage 4 checks and reports. Simulation physics stays on the illustrative demo constants.":
    "நிலை 4 சரிபார்ப்புகள் மற்றும் அறிக்கைகள் பயன்படுத்தும் தள அளவீட்டு மதிப்புகள். உருவக இயற்பியல் விளக்க மாறிலிகளிலேயே இயங்கும்.",
  "Site name": "தள பெயர்", "Rated kW": "மதிப்பிடப்பட்ட kW", "Receiver m³": "ரிசீவர் ம³",
  "Min pressure bar": "குறைந்தபட்ச அழுத்தம்", "Cut-in bar": "கட்-இன் பார்", "Cut-out bar": "கட்-அவுட் பார்",
  "Max pressure bar": "அதிகபட்ச அழுத்தம்", "Tariff ₹/kWh": "கட்டணம் ₹/kWh",
  "Save calibration": "அளவீட்டை சேமி",
  "2 · Sensor health monitoring": "2 · சென்சார் ஆரோக்கிய கண்காணிப்பு",
  "Checks the raw signal stream for missing, stale, stuck or impossible readings before any AI scoring. Optionally inject synthetic faults to test it.":
    "எந்த ஸ்கோரிங்கிற்கும் முன் சிக்னல்களில் விடுபட்ட, தேங்கிய, உறைந்த அல்லது சாத்தியமற்ற அளவீடுகளை சரிபார்க்கிறது. சோதிக்க செயற்கை குறைபாடுகளை செலுத்தலாம்.",
  "Scenario": "சூழல்", "Dropout": "சிگ்னல் விழுதல்", "Stuck pressure": "உறைந்த அழுத்தம்",
  "Power noise": "திறன் இரைச்சல்", "Range violation": "வரம்பு மீறல்",
  "Run health check": "ஆரோக்கிய சரிபார்ப்பை இயக்கு",
  "3 · Fault diagnosis (unknown & multiple faults)": "3 · குறைபாட்டு நோயறிதல் (தெரியாத & பல குறைபாடுகள்)",
  "Combines rules + Stage 2 + Stage 3 into one decision: a known fault, overlapping faults, or an unknown anomaly worth manual investigation.":
    "விதிகள் + நிலை 2 + நிலை 3 ஆகியவற்றை ஒரே முடிவாக இணைக்கிறது: அறிந்த குறைபாடு, மேற்பொருந்தும் குறைபாடுகள், அல்லது கைமுறை விசாரணைக்கு உரிய தெரியாத முரண்பாடு.",
  "Diagnose scenario": "சூழலை நோயறி", "Create ticket from diagnosis": "நோயறிதலிலிருந்து டிக்கெட் உருவாக்கு",
  "6 · Combined-action savings savings": "",
  "6 · Combined-action savings": "6 · ஒருங்கிணைந்த செயல் சிக்கனம்",
  "Repair + setpoint are measured in ONE combined re-simulation so overlapping savings are never counted twice. Sequencing and scheduling are added separately.":
    "பழுதுநீக்கம் + செட்பாயிண்ட் ஒரே ஒருங்கிணைந்த மறு-உருவகத்தில் அளவிடப்படுகின்றன; எனவே சிக்கனம் இருமுறை எண்ணப்படாது. வரிசைப்படுத்தல் & திட்டமிடல் தனியாக சேர்க்கப்படும்.",
  "Repair leak": "கசிவு பழுது", "Setpoint": "செட்பாயிண்ட்", "Sequencing": "வரிசைப்படுத்தல்", "Scheduling": "திட்டமிடல்",
  "Calculate combined savings": "ஒருங்கிணைந்த சிக்கனத்தை கணக்கிடு",
  "7 · Downloadable PDF report": "7 · பதிவிறக்கக்கூடிய PDF அறிக்கை",
  "Full prototype summary: calibration, sensor health, diagnosis, energy comparison, combined savings, maintenance history, stress tests, assumptions.":
    "முழு முன்மாதிரி சுருக்கம்: அளவீடு, சென்சார் ஆரோக்கியம், நோயறிதல், ஆற்றல் ஒப்பீடு, ஒருங்கிணைந்த சிக்கனம், பராமரிப்பு வரலாறு, ஸ்ட்ரெஸ் சோதனைகள், அனுமானங்கள்.",
  "Download PDF report": "PDF அறிக்கையை பதிவிறக்கு",
  "Generated on demand · labelled SIMULATED on every page.": "தேவைக்கேற்ப உருவாக்கப்படும் · ஒவ்வொரு பக்கத்திலும் உருவகம் என குறிக்கப்படும்.",
  "8 · Stress-test mode": "8 · ஸ்ட்ரெஸ்-சோதனை முறை",
  "Battery of hostile conditions: sensor dropouts, stuck transmitter, heavy noise, a 25% demand jump, and leak + filter at the same time.":
    "கடுமையான சூழல்களின் தொகுப்பு: சிக்னல் விழுதல், உறைந்த டிரான்ஸ்மிட்டர், கடும் இரைச்சல், 25% தேவை உயர்வு, மற்றும் கசிவு + வடிகட்டி ஒரே நேரத்தில்.",
  "Run full stress battery": "முழு ஸ்ட்ரெஸ் தொகுப்பை இயக்கு",
  "👥 User roles & access": "👥 பயனர் பொறுப்புகள் & அணுகல்",
  "💾 Backup & restore": "💾 காப்புப்பிரதி & மீட்டமை",
  "Download calibration, tickets and reports as one ZIP; restore it on any machine. Restore accepts only whitelisted JSON files and validates them first.":
    "அளவீடு, டிக்கெட்டுகள், அறிக்கைகளை ஒரே ZIP ஆக பதிவிறக்கலாம்; எந்த இயந்திரத்திலும் மீட்டமைக்கலாம். அனுமதிக்கப்பட்ட JSON கோப்புகள் மட்டுமே ஏற்கப்பட்டு, முதலில் சரிபார்க்கப்படும்.",
  "Download backup ZIP": "காப்பு ZIP பதிவிறக்கு", "Restore ZIP": "ZIP மீட்டமை",
  "Restore from file (manager)": "கோப்பிலிருந்து மீட்டமை (மேலாளர்)",
  "🕓 Model version history": "🕓 மாதிரி பதிப்பு வரலாறு",
  "When each local model was trained, with which scikit-learn, and its recorded evaluation. Retrain any time with train_model.py / train_stage3.py.":
    "ஒவ்வொரு உள்ளூர் மாதிரியும் எப்போது, எந்த scikit-learn உடன் பயிற்றுவிக்கப்பட்டது மற்றும் அதன் மதிப்பீடு. எப்போது வேண்டுமானாலும் மறுபயிற்சி செய்யலாம்.",
  "Load model history": "மாதிரி வரலாற்றை ஏற்று",
  "4 & 5 · Maintenance workflow & post-repair verification": "4 & 5 · பராமரிப்பு பணிப்பாய்வு & பழுதுக்குப் பிந்தைய சரிபார்ப்பு",
  "Tickets persist in": "டிக்கெட்டுகள் சேமிக்கப்படுவது:",
  ". Lifecycle: open → acknowledged → in_repair → verifying → resolved, with a full operator log. Verification compares matched before/after simulated runs.":
    ". வாழ்க்கைச்சுழற்சி: திறப்பு → ஒப்புதல் → பழுதில் → சரிபார்ப்பு → தீர்வு, முழு இயக்குநர் பதிவுடன். சரிபார்ப்பு முன்/பின் உருவகங்களை ஒப்பிடுகிறது.",
  "ID": "ஐடி", "Fault": "குறைபாடு", "Title": "தலைப்பு", "State": "நிலை",
  "Verification": "சரிபார்ப்பு", "Actions": "செயல்கள்", "Loading tickets…": "டிக்கெட்டுகள் ஏற்றப்படுகின்றன…",
  "open": "திறந்தது", "acknowledged": "ஒப்புக்கொள்ளப்பட்டது", "in_repair": "பழுதில்",
  "verifying": "சரிபார்க்கப்படுகிறது", "resolved": "தீர்க்கப்பட்டது", "cancelled": "ரத்து",
  "Acknowledge": "ஒப்புக்கொள்", "Start repair": "பழுதைத் தொடங்கு", "Verify repair ✔": "பழுதை சரிபார் ✔",
  "Resolve": "தீர்", "Reopen": "மீண்டும் திற", "Cancel": "ரத்து செய்",
  /* validation */
  "MODEL EVALUATION / SIMULATED DATA": "மாதிரி மதிப்பீடு / உருவகத் தரவு",
  "How well did the detectors perform?": "கண்டறிதல்கள் எவ்வளவு சிறப்பாக செயல்பட்டன?",
  "Download Stage 2 report": "நிலை 2 அறிக்கை பதிவிறக்கு", "Download Stage 3 report": "நிலை 3 அறிக்கை பதிவிறக்கு",
  "Train the local model to see results from separate test runs.": "தனி சோதனை இயக்கங்களின் முடிவுகளை காண உள்ளூர் மாதிரியை பயிற்றுவிக்கவும்.",
  "Precision": "துல்லியம்", "Recall": "ரிக்கால்", "F1 score": "F1 ஸ்கோர்", "False-positive rate": "பொய்-நேர்மறை விகிதம்",
  "Correct fault flags / all flags": "சரியான குறைபாட்டு கொடிகள் / மொத்த கொடிகள்",
  "Flagged fault windows / all fault windows": "கொடியிடப்பட்ட குறைபாட்டு சாளரங்கள் / மொத்தம்",
  "Balance of precision and recall": "துல்லியம் மற்றும் ரிக்கால் சமநிலை",
  "Flagged normal windows / all normal windows": "கொடியிடப்பட்ட இயல்பு சாளரங்கள் / மொத்தம்",
  "STAGE 3 · SUPERVISED FAULT-TYPE CLASSIFIER": "நிலை 3 · மேற்பார்வை வகைப்படுத்தி",
  "Stage 3 evaluation": "நிலை 3 மதிப்பீடு",
  "Run train_stage3.py to create the Stage 3 model and report.": "நிலை 3 மாதிரி மற்றும் அறிக்கையை உருவாக்க train_stage3.py இயக்கவும்.",
  "Macro F1 (window)": "மேக்ரோ F1 (சாளரம்)", "Target: ≥ 85%": "இலக்கு: ≥ 85%",
  "Event detection": "நிகழ்வு கண்டறிதல்", "Hybrid classifier + rules": "கலப்பு வகைப்படுத்தி + விதிகள்",
  "Median delay": "இடைநிலை தாமதம்", "Among detected events": "கண்டறியப்பட்ட நிகழ்வுகளில்",
  "False-alarm episodes": "பொய்யெச்சரிக்கை நிகழ்வுகள்", "In entirely normal runs": "முழுமையாக இயல்பான இயக்கங்களில்",
  "Class": "வகுப்பு", "F1": "F1",
  /* assumptions & connectivity */
  "Edge connectivity & integration (Modbus / MQTT)": "விளிம்பு இணைப்பு & ஒருங்கிணைப்பு (Modbus / MQTT)",
  "Show sample MQTT payload & Modbus map": "மாதிரி MQTT பेलோடு & Modbus வரைபடம் காட்டு",
  "Model assumptions & prototype limits": "மாதிரி அனுமானங்கள் & முன்மாதிரி வரம்புகள்",
  "Illustrative equipment": "விளக்க உபகரணம்",
  "What the results mean": "முடிவுகளின் பொருள்",
  "Stage 3 additions": "நிலை 3 சேர்ப்புகள்",
  /* footer */
  "FactoryAir Twin · Challenge 04: Smart Manufacturing": "ஃபேக்டரிஏர் ட்வின் · சவால் 04: ஸ்மார்ட் உற்பத்தி",
  "Local prototype · STAGE 4.5 · One compressor room": "உள்ளூர் முன்மாதிரி · நிலை 4.5 · ஒரு அமுக்கி அறை",
  /* backend alert titles & actions */
  "Suspected leakage or unrecorded air use": "கசிவு அல்லது பதிவு செய்யப்படாத காற்று பயன்பாடு சந்தேகம்",
  "Inspect the air network. Branch sensing or an isolation test is needed to locate the source.":
    "காற்று வலையமைப்பை ஆய்வு செய்யவும்; மூலத்தை கண்டறிய கிளை உணர்தல் அல்லது தனிமைப்படுத்தல் சோதனை தேவை.",
  "Extended unloaded operation": "நீடித்த சுமையற்ற இயக்கம்",
  "Review the stop delay and restart requirements. Test a shorter delay in the simulator.":
    "நிறுத்த தாமதம் மற்றும் மறுதொடக்க தேவைகளை மதிப்பாய்வு செய்யவும்; உருவகத்தில் குறுகிய தாமதத்தை சோதிக்கவும்.",
  "Pressure below the demo minimum": "அழுத்தம் சோதனை குறைந்தபட்சத்திற்கு கீழே",
  "Review the capacity and demand assumptions before accepting any operating change.":
    "எந்த இயக்க மாற்றத்தையும் ஏற்கும் முன் திறன் மற்றும் தேவை அனுமானங்களை மதிப்பாய்வு செய்யவும்.",
  "Compressor deterioration suspected": "அமுக்கி தேய்மானம் சந்தேகம்",
  "Plan an inspection/overhaul window. Compare energy before and after service.":
    "ஆய்வு/பராமரிப்பு நேரத்தை திட்டமிடுங்கள்; சேவைக்கு முன்/பின் ஆற்றலை ஒப்பிடவும்.",
  "Filter blockage suspected": "வடிகட்டி அடைப்பு சந்தேகம்",
  "Check the intake filter differential pressure. Replace the element and re-test.":
    "உள்வரும் வடிகட்டியின் அழுத்த வேறுபாட்டை சரிபார்க்கவும்; பாகத்தை மாற்றி மீண்டும் சோதிக்கவும்."
};
const EN_TEXT = new Map(), EN_OPT = new Map();
document.querySelectorAll("[data-i18n]").forEach(el => EN_TEXT.set(el, el.textContent.trim()));
document.querySelectorAll("[data-i18n-opt]").forEach(el => EN_OPT.set(el, el.textContent.trim()));
const EN_T = {
  "state.line": "{0} · oil temperature {1} °C",
  "cost.line": "₹{0} at assumed ₹{1}/kWh",
  "sec.line": "{0} kWh / simulated accepted unit",
  "sec.none": "No completed production cycle yet",
  "prod.line": "{0} · Estimated total outflow {1} m³/min at reference conditions.",
  "prod.active": "Production active", "prod.paused": "Production paused",
  "alert.first": "First detected {0} · Rule alert",
  "alerts.empty": "No rule has triggered in the replay so far. Continue the shift to inspect changes.",
  "ai.notready": "Local model not ready",
  "ai.trainmsg": "Run train_model.py, then restart app.py.",
  "ai.collecting": "Collecting the first 60 seconds",
  "ai.nowindow": "No model score is available before a complete window.",
  "ai.flag": "Unusual operating pattern", "ai.noflag": "No anomaly in the latest window",
  "ai.detail": "Window {0}–{1}. {2} flagged window(s) so far.",
  "ai.lastflag": " Most recent flag: {0}.", "ai.noruleout": " This does not rule out a fault.",
  "ai.score": "Score {0} · alert above {1}",
  "s3.notready": "Stage 3 model not ready",
  "s3.trainmsg": "Run train_stage3.py, then restart app.py.",
  "s3.probable": "Probable: {0}", "s3.nofault": "No fault pattern in the latest window",
  "s3.nowindow": "No classification before a complete window.",
  "s3.detail": "Window {0}–{1}. {2} fault window(s) so far.",
  "s3.last": " Most recent: {0} ({1}).",
  "s3.cause.fault": "{0} · probability {1}% · normal-window p {2}%",
  "s3.cause.state": "Most likely state: {0} ({1}%)",
  "model.status": "{0} · {1}",
  "valid.summary": "{0} normal training runs → {1} separate normal calibration runs → {2} held-out test runs. Each run is 30 minutes. Threshold fixed from normal calibration scores.",
  "s3.valtitle": "Stage 3 evaluation · {0} runs per scenario trained, {1} held out",
  "s3.target.met": "Target ≥ 85% — MET in this synthetic evaluation",
  "saving.detail": "{0} kWh · ₹{1} estimated saving",
  "setpoint.searching": "Searching pressure bands with matched re-runs…",
  "setpoint.none": "No lower band passed the production guardrails in this scenario. Keep the current setpoint.",
  "setpoint.rec2": "Recommend <strong>{0} bar(g)</strong> (−{1} bar). Saves {2} kWh per 30-min run ≈ <strong>₹{3}/month</strong>, {4} kg CO₂e/month. Minimum pressure kept at {5} bar(g); output and compliance guardrails pass. Peak power {6} kW.",
  "seq.running": "Simulating both lead/lag orders on the same demand…",
  "seq.top": "{0} saves {1} kWh per 30 min ≈ ₹{2}/month ({3} kg CO₂e).",
  "seq.line": "{0}: {1} kWh, unloaded {2} s, min pressure {3} bar(g).",
  "sched.running": "Computing illustrative schedule savings…",
  "sched.needload": "Load a scenario with production first.",
  "sched.result2": "Daily air energy {0} kWh; shifting {1} kWh out of peak saves <strong>₹{2}/month</strong>. Solar displacement avoids about {3} kg CO₂e/month.",
  "catalog.building": "Building ranked catalogue…",
  "catalog.empty": "No paid interventions identified for this scenario — setpoint and sequencing checks found no guarded saving.",
  "catalog.note": "{0} Grid factor {1} kg CO₂e/kWh.",
  "days": "{0} days",
  "health.checking": "Checking the signal stream…",
  "health.ok": "🟢 OK", "health.degraded": "🟡 DEGRADED", "health.failed": "🔴 FAILED",
  "w.ok": "ok", "w.degraded": "degraded", "w.failed": "failed",
  "health.injected": "Injected faults: {0}",
  "diag.running": "Diagnosing…", "diag.ticketing": "Creating ticket from diagnosis…",
  "diag.known": "🎯 KNOWN FAULT", "diag.multiple": "🧩 MULTIPLE FAULTS",
  "diag.unknown": "❓ UNKNOWN ANOMALY", "diag.normal": "🟢 NORMAL",
  "diag.line": "Confidence {0} · Rules: {1} · AI-flagged windows: {2} · anomaly ratio: {3}",
  "diag.ticketed": "Ticket {0} created ({1}) — see the maintenance table below.",
  "ticket.empty": "No tickets yet. Diagnose a fault scenario and create one.",
  "ticket.saved": "Saved {0} kWh/30 min ≈ ₹{1}/mo",
  "ticket.verified": "Verified: saved {0} kWh per 30 min ≈ ₹{1}/month.",
  "comb.running": "Running the combined re-simulation…",
  "comb.pick": "Pick at least one action.",
  "comb.total": "Total ≈ ₹{0}/month · {1} kg CO₂e/month · compliance {2}%",
  "stress.running": "Running 5 stress scenarios (physics + corruption + detection)…",
  "th.test": "Test", "th.health": "Health", "th.alerts": "Alerts", "th.diag": "Diagnosis",
  "history.loading": "Reading local model metadata…",
  "hist.trained": "Trained: {0} · scikit-learn {1}",
  "hist.file": "Model file last written: {0}",
  "backup.downloading": "Backup ZIP downloading…",
  "backup.choose": "Choose a backup ZIP first.",
  "backup.restored": "Restored ✔ {0}<br>Reload the page to see the restored state.",
  "role.matrix": "Permissions — operator: raise & acknowledge · engineer: + repair, verify, resolve, diagnose, combined savings · manager: + calibration, stress tests, reports, restore.",
  "month": "month"
};

function t(en) {
  if (LANG === "ta") return TA[en] || EN_T[en] || en;
  return EN_T[en] || en;
}
function tf(key, ...vals) {
  let s = LANG === "ta" ? (TA[key] || EN_T[key] || key) : (EN_T[key] || key);
  vals.forEach((v, i) => { s = s.split("{" + i + "}").join(v); });
  return s;
}

function applyLang(lang) {
  LANG = lang;
  const useTa = lang === "ta";
  for (const [el, en] of EN_TEXT) el.textContent = t(en);
  for (const [el, en] of EN_OPT) el.textContent = useTa ? (TA["opt_" + el.dataset.i18nOpt] || en) : en;
  $("lang-toggle").textContent = useTa ? "English" : "தமிழ்";
  document.title = useTa ? "ஃபேக்டரிஏர் ட்வின் | அமுக்கி கண்காணிப்பு" : "FactoryAir Twin | Compressor monitor";
  try { localStorage.setItem("fa-lang", lang); } catch (e) {}
  if (typeof simulation !== "undefined" && simulation) draw();
  refreshTickets();
  applyRoleGating();
}


Object.assign(TA, {
  "seq.top": "{0} — 30 நிமிடத்திற்கு {1} கி.வா.மணி சிக்கனம் ≈ மாதம் ₹{2} ({3} கி.கி CO₂e).",
  "sched.result2": "தினசரி காற்று ஆற்றல் {0} கி.வா.மணி; உச்சத்திலிருந்து {1} கி.வா.மணி மாற்றம் → மாதம் <strong>₹{2}</strong> சிக்கனம். சூரிய இடப்பெயர்ச்சி மாதம் ~{3} கி.கி CO₂e தவிர்க்கிறது.",
  "setpoint.rec2": "பரிந்துரை: <strong>{0} பார்</strong> (−{1} பார்). ஒரு 30-நிமிட இயக்கத்தில் {2} கி.வா.மணி சிக்கனம் ≈ <strong>மாதம் ₹{3}</strong>, {4} கி.கி CO₂e/மாதம். குறைந்தபட்ச அழுத்தம் {5} பார்; வெளியீடு & இணக்க பாதுகாப்புகள் தேர்ச்சி. உச்ச திறன் {6} கி.வா.",
  "month": "மாதம்",
  "Assume inspection and repair reduce leakage, then compare two complete 30-minute runs.": "ஆய்வு மற்றும் பழுது கசிவை குறைக்கும் எனக் கொண்டு, இரண்டு முழு 30-நிமிட இயக்கங்களை ஒப்பிடவும்.",
  "Test a shorter unloaded stop delay, then compare two complete 30-minute runs.": "குறுகிய சுமையற்ற நிறுத்த தாமதத்தை சோதித்து, இரண்டு முழு 30-நிமிட இயக்கங்களை ஒப்பிடவும்.",
  "Replace the intake filter in the simulation, then compare two complete 30-minute runs.": "உருவகத்தில் உள்வரும் வடிகட்டியை மாற்றி, இரண்டு முழு 30-நிமிட இயக்கங்களை ஒப்பிடவும்.",
  "Simulate an overhaul that restores the performance map, then compare two complete 30-minute runs.": "செயல்திறன் வரைபடத்தை மீட்டமைக்கும் பராமரிப்பை உருவகித்து, இரண்டு முழு 30-நிமிட இயக்கங்களை ஒப்பிடவும்.",
  "efficient unit leads": "திறன்மிக்க இயந்திரம் முன்னணியில்",
  "smaller unit leads": "சிறிய இயந்திரம் முன்னணியில்"
});


Object.assign(TA, {
  "Create ticket from diagnosis": "நோயறிதலிலிருந்து டிக்கெட் உருவாக்கு",
  "Download Stage 3 report": "நிலை 3 அறிக்கையை பதிவிறக்கு",
  "Export full run": "முழு இயக்கத்தை ஏற்றுமதி செய்",
  "Alerts & next action": "எச்சரிக்கைகள் & அடுத்த நடவடிக்கை",
  "Calculated simulation results": "கணக்கிடப்பட்ட உருவக முடிவுகள்",
  "Filter": "வடிகட்டி", "Leak": "கசிவு", "Unloaded": "சுமையற்றது", "Worn": "தேய்மானம்",
  "Maintenance engineer": "பராமரிப்பு பொறியாளர்", "Plant manager": "ஆலை மேலாளர்",
  "Progressive filter blockage": "படிப்படியான வடிகட்டி அடைப்பு",
  "Suspected air leak": "சந்தேகிக்கப்படும் காற்று கசிவு",
  "Among detected events": "கண்டறியப்பட்ட நிகழ்வுகளில்",
  "Demo band: 6.0–7.4 bar(g)": "காட்சி வரம்பு: 6.0–7.4 பார்(கி)",
  "Hybrid classifier + rules": "கலப்பு வகைப்படுத்தி + விதிகள்",
  "Illustrative electricity cost": "விளக்க மின்சார செலவு",
  "In entirely normal runs": "முற்றிலும் இயல்பான இயக்கங்களில்",
  "Pressure-based production proxy": "அழுத்த அடிப்படை உற்பத்தி அளவீடு",
  "Target: ≥ 85%": "இலக்கு: ≥ 85%",
  "Waiting for simulation": "உருவகத்திற்காக காத்திருக்கிறது",
  "Assumed price · ₹/kWh": "விலை அனுமானம் · ₹/கி.வா.மணி",
  "Cut-in bar": "இணைப்பு அழுத்தம் பார்", "Cut-out bar": "துண்டிப்பு அழுத்தம் பார்",
  "Dropout": "சிக்னல் விழல்", "F1 score": "F1 ஸ்கோர்",
  "False-positive rate": "தவறு-நேர்மறை விகிதம்",
  "Grid factor kg CO₂e/kWh": "கிரிட் காரணி கி.கி CO₂e/kWh",
  "Name": "பெயர்", "Peak ₹": "உச்ச கட்டணம் ₹", "Projected cost": "கணிக்கப்பட்ட செலவு",
  "Range violation": "வரம்பு மீறல்", "Rated kW": "மதிப்பிடப்பட்ட கி.வா",
  "Recall": "ரீகால்", "Receiver m³": "ரிசீவர் ம³", "Replay position": "ரீப்ளே நிலை",
  "Restore ZIP": "ZIP மீட்டமை", "Scheduling": "திட்டமிடல்", "Sequencing": "வரிசைப்படுத்தல்",
  "Setpoint": "செட்பாயிண்ட்", "Shiftable %": "மாற்றக்கூடிய %", "Solar share %": "சூரிய பங்கு %",
  "Solar ₹": "சூரிய கட்டணம் ₹", "Speed": "வேகம்", "Stuck pressure": "உறைந்த அழுத்தம்",
  "Tariff ₹/kWh": "கட்டணம் ₹/கி.வா.மணி",
  "Action": "செயல்", "Actions": "நடவடிக்கைகள்", "After action": "செயலுக்கு பிறகு",
  "Baseline": "அடிப்படை", "F1": "F1", "Fault": "குறைபாடு", "Payback": "மீட்புக்காலம்",
  "State": "நிலை", "Title": "தலைப்பு", "What to do": "என்ன செய்ய வேண்டும்",
  "kg CO₂e / month": "கி.கி CO₂e / மாதம்", "₹ / month": "₹ / மாதம்",
  "opt_leak_sim": "சந்தேகிக்கப்படும் காற்று கசிவு",
  "opt_filter_sim": "படிப்படியான வடிகட்டி அடைப்பு",
  "opt_worn_sim": "தேய்மான இயந்திர வரைபடம்",
  "opt_normal_sim": "இயல்பு இயக்கம்",
  "opt_none_sim": "குறைபாடு இல்லை (இயல்பு)"
});

$("role-select").addEventListener("change", () => { applyRoleGating(); refreshTickets(); });
$("lang-toggle").addEventListener("click", () =>
  applyLang($("lang-toggle").textContent === "தமிழ்" ? "ta" : "en"));
$("backup-run").addEventListener("click", runBackup);
$("restore-run").addEventListener("click", runRestore);
$("history-run").addEventListener("click", runHistory);
applyRoleGating();
let savedLang = "en";
try { savedLang = localStorage.getItem("fa-lang") || "en"; } catch (e) {}
applyLang(savedLang);
