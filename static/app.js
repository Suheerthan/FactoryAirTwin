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
  $("state").textContent = `${r.state} · oil temperature ${fmt(r.temperature_c,1)} °C`;
  $("units").textContent = r.accepted_units;
  $("cost").textContent = `₹${fmt(r.energy_kwh * simulation.tariff_inr_kwh)} at assumed ₹${fmt(simulation.tariff_inr_kwh)}/kWh`;
  $("sec").textContent = r.accepted_units ? `${fmt(r.energy_kwh/r.accepted_units,4)} kWh / simulated accepted unit` : "No completed production cycle yet";
  $("clock").textContent = `${time(r.second)} / 30:00`;
  $("scrubber").value = cursor;
  $("production").textContent = `${r.production_active ? "Production active" : "Production paused"} · Estimated total outflow ${fmt(r.estimated_outflow_m3_min,3)} m³/min at reference conditions.`;
  chart("pressure-chart","pressure_bar",5.8,7.6,"#008561",true);
  chart("power-chart","power_kw",0,9,"#177bac");
  const visible = simulation.alerts.filter(a => a.second <= r.second);
  $("alert-count").textContent = visible.length;
  $("alerts").replaceChildren();
  if (!visible.length) {
    const p = document.createElement("p");p.className="empty";
    p.textContent="No rule has triggered in the replay so far. Continue the shift to inspect changes.";$("alerts").append(p);
  }
  visible.forEach(a => {
    const box=document.createElement("article");box.className=`alert ${a.severity}`;
    const t=document.createElement("small");t.textContent=`First detected ${time(a.second)} · Rule alert`;
    const h=document.createElement("h3");h.textContent=a.title;
    const p=document.createElement("p");p.textContent=a.evidence;
    const action=document.createElement("p");action.textContent=a.action;
    box.append(t,h,p,action);$("alerts").append(box);
  });
  const eligible = visible.some(a => a.key === simulation.scenario) && simulation.scenario !== "normal";
  $("compare").disabled = !eligible || busy;
  $("action-description").textContent = eligible ? ({
    leak: "Assume inspection and repair reduce leakage, then compare two complete 30-minute runs.",
    unloaded: "Test a shorter unloaded stop delay, then compare two complete 30-minute runs.",
    filter: "Replace the intake filter in the simulation, then compare two complete 30-minute runs.",
    worn: "Simulate an overhaul that restores the performance map, then compare two complete 30-minute runs."
  })[simulation.scenario] : "Choose a fault scenario and replay until an alert appears.";
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
    $("ai-title").textContent = "Local model not ready";
    $("ai-detail").textContent = ai?.message || "Run train_model.py, then restart app.py.";
    return;
  }
  const visible = ai.windows.filter(w => w.end_second <= second);
  const current = visible.at(-1);
  if (!current) {
    $("ai-title").textContent = "Collecting the first 60 seconds";
    $("ai-detail").textContent = "No model score is available before a complete window.";
    return;
  }
  $("ai-panel").classList.toggle("is-anomaly", current.is_anomaly);
  $("ai-title").textContent = current.is_anomaly ? "Unusual operating pattern" : "No anomaly in the latest window";
  const flags = visible.filter(w => w.is_anomaly);
  const lastFlag = flags.at(-1);
  $("ai-detail").textContent = `Window ${time(current.start_second)}–${time(current.end_second)}. ${flags.length} flagged window(s) so far.${lastFlag ? ` Most recent flag: ${time(lastFlag.end_second)}.` : " This does not rule out a fault."}`;
  $("ai-score").textContent = `Score ${fmt(current.score,6)} · alert above ${fmt(current.threshold,6)}`;
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
    $("stage3-title").textContent = "Stage 3 model not ready";
    $("stage3-detail").textContent = s3?.message || "Run train_stage3.py, then restart app.py.";
    return;
  }
  const visible = s3.windows.filter(w => w.end_second <= second);
  const current = visible.at(-1);
  if (!current) {
    $("stage3-title").textContent = "Collecting the first 60 seconds";
    $("stage3-detail").textContent = "No classification before a complete window.";
    return;
  }
  const title = s3.class_titles[current.predicted_class] || current.predicted_class;
  $("stage3-panel").classList.toggle("is-anomaly", current.is_fault);
  $("stage3-title").textContent = current.is_fault ? `Probable: ${title}` : "No fault pattern in the latest window";
  const faults = visible.filter(w => w.is_fault);
  const last = faults.at(-1);
  $("stage3-detail").textContent = `Window ${time(current.start_second)}–${time(current.end_second)}. ${faults.length} fault window(s) so far.${last ? ` Most recent: ${time(last.end_second)} (${s3.class_titles[last.predicted_class]}).` : ""}`;
  const cause = $("stage3-cause");
  cause.hidden = false;
  cause.textContent = current.is_fault
    ? `${title} · probability ${fmt(current.probability*100,0)}% · normal-window p ${fmt(current.p_normal*100,0)}%`
    : `Most likely state: ${title} (${fmt(current.probability*100,0)}%)`;
  const probs = $("stage3-probs");
  probs.hidden = false;
  Object.entries(current.probabilities).forEach(([cls, p]) => {
    const row = document.createElement("div");row.className = "prob-row";
    const label = document.createElement("span");label.textContent = s3.class_titles[cls];
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
    if (!response.ok) throw new Error("Could not read the local model status.");
    const status = await response.json();
    const s3 = await (await fetch("/api/stage3/status")).json();
    $("model-status").textContent = `${status.enabled ? "Stage 2 IF trained" : "Stage 2 IF missing"} · ${s3.enabled ? "Stage 3 classifier trained" : "Stage 3 classifier missing"}`;
    const report = status.report;
    if (!report) {
      $("validation-message").textContent = status.message || "Run train_model.py again to create a matching model and report, then restart app.py.";
    } else {
      const m = report.metrics, events = report.event_detection;
      $("validation-message").textContent = `${report.train_runs} normal training runs → ${report.calibration_runs} separate normal calibration runs → ${report.test_runs} held-out test runs. Each run is 30 minutes. Threshold fixed from normal calibration scores.`;
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
      $("stage3-message").textContent = s3.message || "Run train_stage3.py, then restart app.py.";
    } else {
      const ev = s3report.event_detection;
      $("stage3-val-title").textContent = `Stage 3 evaluation · ${s3report.train_runs_per_scenario} runs per scenario trained, ${s3report.test_runs_per_scenario} held out`;
      $("s3-f1").textContent = `${fmt(s3report.macro_f1*100,1)}%`;
      $("s3-target").textContent = s3report.macro_f1 >= 0.85 ? "Target ≥ 85% — MET in this synthetic evaluation" : "Target: ≥ 85%";
      $("s3-target").classList.toggle("target-met", s3report.macro_f1 >= 0.85);
      $("s3-events").textContent = `${ev.detected}/${ev.total}`;
      $("s3-delay").textContent = ev.median_delay_seconds_among_detected == null ? "N/A" : `${fmt(ev.median_delay_seconds_among_detected,0)} s`;
      $("s3-fa").textContent = `${s3report.normal_run_alarm_episodes} in ${fmt(s3report.normal_run_hours,1)} h`;
      $("stage3-rows").replaceChildren();
      Object.entries(s3report.per_class).forEach(([cls, m]) => {
        const row = document.createElement("tr");
        [(s3report.class_titles[cls] || cls), `${fmt(m.precision*100,1)}%`, `${fmt(m.recall*100,1)}%`, `${fmt(m.f1*100,1)}%`].forEach(text => {
          const cell = document.createElement("td");cell.textContent = text;row.append(cell);
        });
        $("stage3-rows").append(row);
      });
      $("stage3-note").textContent = `Stage 3 test seeds are reserved and disjoint from Stage 2. Window recall is bounded when a fault is not observable in a given 60 s operating state; event-level hybrid detection combines the classifier with engineering rules.`;
      $("stage3-results").hidden = false;
      $("download-stage3").disabled = false;
    }
  } catch (error) {
    $("model-status").textContent = "Model status unavailable";
    $("validation-message").textContent = error.message;
  }
}

function stop() {clearInterval(timer);timer=null;$("play").textContent="Play";}
function play() {
  if (!simulation || busy) return;
  if (timer) {stop();return;}
  if (cursor >= simulation.readings.length-1) cursor=0;
  $("play").textContent="Pause";
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
    $("comparison-action").textContent=`${result.action} ${result.scope}`;
    $("saving-percent").textContent=`${fmt(result.energy_reduction_pct)}%`;
    $("saving-detail").textContent=`${fmt(result.savings_kwh,3)} kWh · ₹${fmt(result.savings_inr)} estimated saving`;
    $("comparison-status").textContent=result.simulation_guardrails_pass ? "SIMULATED PRESSURE & OUTPUT CHECKS PASS" : "REVIEW SIMULATION CONSTRAINTS";
    $("comparison-limitation").textContent=result.limitation;
    const definitions=[
      ["Electricity", "energy_kwh",3," kWh"], ["Specific energy", "sec_kwh_per_unit",4," kWh/unit"],
      ["Simulated accepted units","accepted_units",0,""], ["Pressure-affected cycles","pressure_failed_cycles",0,""],
      ["Within pressure band","pressure_compliance_pct",2,"%"], ["Unloaded time","unloaded_seconds",0," s"],
      ["Peak power","peak_power_kw",2," kW"], ["Final receiver pressure","final_pressure_bar",3," bar(g)"]
    ];
    $("comparison-rows").replaceChildren();
    definitions.forEach(([label,key,digits,unit])=>{
      const row=document.createElement("tr");
      [label,`${fmt(result.baseline[key],digits)}${unit}`,`${fmt(result.after[key],digits)}${unit}`].forEach(text=>{const cell=document.createElement("td");cell.textContent=text;row.append(cell);});
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
  resultBox("setpoint-result","Searching pressure bands with matched re-runs…");
  try {
    const r = await api("/api/optimise/setpoint",{scenario:simulation.scenario,tariff:simulation.tariff_inr_kwh});
    if (!r.recommended) {
      resultBox("setpoint-result","No lower band passed the production guardrails in this scenario. Keep the current setpoint.");
      return;
    }
    const rec = r.recommended;
    resultBox("setpoint-result",
      `Recommend <strong>${fmt(rec.cut_in_bar,1)} / ${fmt(rec.cut_out_bar,1)} bar(g)</strong> (−${fmt(rec.drop_bar,1)} bar). ` +
      `Saves ${fmt(rec.savings_kwh,3)} kWh per 30-min run ≈ <strong>₹${inr(rec.monthly_inr)}/month</strong>, ` +
      `${inr(rec.co2_kg_month)} kg CO₂e/month. Minimum pressure kept at ${fmt(rec.minimum_pressure_bar,2)} bar(g); output and compliance guardrails pass. ` +
      `Peak power ${fmt(rec.peak_power_kw,2)} kW.`);
  } catch(error) {resultBox("setpoint-result",error.message);}
}

async function runSequence() {
  resultBox("sequence-result","Simulating both lead/lag orders on the same demand…");
  try {
    const r = await api("/api/optimise/sequence",{tariff:simulation?simulation.tariff_inr_kwh:Number($("tariff").value)});
    let html = `<strong>${r.recommended}</strong> saves ${fmt(r.saving_kwh_per_30min,3)} kWh per 30 min ≈ ₹${inr(r.monthly_inr)}/month (${inr(r.co2_kg_month)} kg CO₂e).<br>`;
    Object.entries(r.strategies).forEach(([name, s]) => {
      const unloadedTotal = Object.values(s.unloaded_seconds).reduce((a,b)=>a+b,0);
      html += `${name}: ${fmt(s.energy_kwh,3)} kWh, unloaded ${unloadedTotal} s, min pressure ${fmt(s.minimum_pressure_bar,2)} bar(g).<br>`;
    });
    html += `<small>${r.note}</small>`;
    resultBox("sequence-result",html);
  } catch(error) {resultBox("sequence-result",error.message);}
}

async function runSchedule() {
  if(!simulation||!simulation.summary.sec_kwh_per_unit){resultBox("schedule-result","Load a scenario with production first.");return;}
  resultBox("schedule-result","Computing illustrative schedule savings…");
  try {
    const r = await api("/api/schedule",{
      sec_kwh_per_unit: simulation.summary.sec_kwh_per_unit,
      units_per_day: Number($("sched-units").value), shiftable_pct: Number($("sched-shift").value),
      peak_rate: Number($("sched-peak").value), off_peak_rate: Number($("sched-off").value),
      solar_rate: Number($("sched-solar").value), solar_share_pct: Number($("sched-solarshare").value)
    });
    resultBox("schedule-result",
      `Daily air energy ${fmt(r.daily_energy_kwh,1)} kWh; shifting ${fmt(r.shifted_kwh_per_day,1)} kWh out of peak saves ` +
      `<strong>₹${inr(r.monthly_saving_inr)}/month</strong>. Solar displacement avoids about ${inr(r.co2_kg_month_solar_displacement)} kg CO₂e/month. ` +
      `<small>${r.note}</small>`);
  } catch(error) {resultBox("schedule-result",error.message);}
}

async function loadCatalog() {
  if(!simulation)return;
  $("catalog-rows").replaceChildren();
  const loading=document.createElement("tr");const cell=document.createElement("td");
  cell.colSpan=8;cell.textContent="Building ranked catalogue…";loading.append(cell);$("catalog-rows").append(loading);
  try {
    const r = await api("/api/catalog",{scenario:simulation.scenario,tariff:simulation.tariff_inr_kwh});
    $("catalog-rows").replaceChildren();
    if (!r.actions.length) {
      const row=document.createElement("tr");const c=document.createElement("td");
      c.colSpan=8;c.textContent="No paid interventions identified for this scenario — setpoint and sequencing checks found no guarded saving.";
      row.append(c);$("catalog-rows").append(row);
      return;
    }
    r.actions.forEach((a,i) => {
      const row=document.createElement("tr");
      const cells=[String(i+1),a.title,a.action,inr(a.monthly_kwh),`₹${inr(a.monthly_inr)}`,inr(a.co2_kg_month),
        a.assumed_cost_inr?`₹${inr(a.assumed_cost_inr)}`:"₹0 (settings)",a.assumed_cost_inr?`${fmt(a.payback_days,0)} days`:"Immediate"];
      cells.forEach(text=>{const c=document.createElement("td");c.textContent=text;row.append(c);});
      row.title=a.basis;
      $("catalog-rows").append(row);
    });
    $("catalog-note").textContent=`${r.assumptions.note} Grid factor ${r.assumptions.grid_co2_kg_per_kwh} kg CO₂e/kWh.`;
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

async function api2(url, body, method = "POST") {
  const response = await fetch(url, method === "POST" ? {
    method, headers: {"Content-Type": "application/json"}, body: JSON.stringify(body || {})
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
  resultBox("health-result", "Checking the signal stream…");
  try {
    const r = await api2("/api/stage4/sensorhealth", {scenario: $("health-scenario").value, corruptions});
    const badge = {ok: "🟢 OK", degraded: "🟡 DEGRADED", failed: "🔴 FAILED"}[r.overall] || r.overall;
    let html = `<strong>${badge}</strong> · ${r.samples_checked} samples · sensors: ` +
      Object.entries(r.sensors).map(([k, s]) => `${k.split("_")[0]} ${s.status}`).join(" · ") + "<br>";
    html += r.issues.length ? r.issues.map(i => `• ${i}`).join("<br>") : "• No quality problems found.";
    if (r.corruptions_applied.length) html += `<br><small>Injected faults: ${r.corruptions_applied.join(", ")}</small>`;
    resultBox("health-result", html);
  } catch (error) { resultBox("health-result", error.message); }
}

async function runDiagnose(createTicket = false) {
  const scenario = $("diag-scenario").value;
  resultBox("diag-result", createTicket ? "Creating ticket from diagnosis…" : "Diagnosing…");
  try {
    const r = await api2("/api/stage4/diagnose", {scenario});
    const d = r.diagnosis;
    const badge = {known: "🎯 KNOWN FAULT", multiple: "🧩 MULTIPLE FAULTS", unknown: "❓ UNKNOWN ANOMALY", normal: "🟢 NORMAL"}[d.state] || d.state;
    let html = `<strong>${badge}</strong> · confidence ${fmt(d.confidence, 2)}<br>${d.explanation}<br>` +
      `<small>Rules fired: ${d.rule_keys.join(", ") || "none"} · AI-flagged windows: ${d.ai_flagged_windows} · anomaly ratio: ${fmt(d.anomaly_ratio, 2)}</small>`;
    if (createTicket) {
      if (scenario === "normal") throw new Error("Run the diagnosis on a fault scenario first.");
      const t = await api2("/api/stage4/ticket/from-diagnosis", {scenario});
      html += `<br><strong>Ticket ${t.id} created</strong> (${t.fault_type}) — see the maintenance table below.`;
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
      c.colSpan = 6; c.textContent = "No tickets yet. Diagnose a fault scenario and create one.";
      row.append(c); tbody.append(row); return;
    }
    tickets.slice().reverse().forEach(t => {
      const row = document.createElement("tr");
      const verification = t.verification
        ? (t.verification.status === "measured_simulated"
          ? `Saved ${t.verification.saved_kwh_30min} kWh/30 min ≈ ₹${inr(t.verification.saved_inr_per_month)}/mo` +
            (t.verification.deviation_pct_vs_predicted != null ? ` (${t.verification.deviation_pct_vs_predicted}% vs predicted)` : "")
          : t.verification.message || t.verification.status)
        : "—";
      const cells = [t.id, t.fault_type, t.title, t.state, verification];
      cells.forEach(text => { const c = document.createElement("td"); c.textContent = text; row.append(c); });
      const actionsCell = document.createElement("td");
      (NEXT_ACTIONS[t.state] || []).forEach(([action, label]) => {
        const b = document.createElement("button"); b.type = "button"; b.textContent = label;
        b.addEventListener("click", () => ticketAct(t.id, action));
        actionsCell.append(b, " ");
      });
      row.append(actionsCell);
      row.title = `Logs: ${t.logs.map(l => `${l.at} ${l.operator} ${l.action}${l.note ? " — " + l.note : ""}`).join(" | ")}`;
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
        ? `Verified: saved ${v.saved_kwh_30min} kWh per 30 min ≈ ₹${inr(v.saved_inr_per_month)}/month.`
        : v.message;
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
  if (!actions.length) { resultBox("comb-result", "Pick at least one action."); return; }
  resultBox("comb-result", "Running the combined re-simulation…");
  try {
    const r = await api2("/api/optimise/combined", {scenario: $("comb-scenario").value, actions});
    let html = `<strong>Total ≈ ₹${inr(r.total_inr_per_month)}/month</strong> · ${inr(r.total_co2_kg_per_month)} kg CO₂e/month · compliance ${fmt(r.pressure_compliance_pct, 1)}%<br>`;
    r.parts.forEach(p => { html += `• ${p.action}: ${p.inr_per_month != null ? "₹" + inr(p.inr_per_month) + "/month" : "n/a"}<br>`; });
    html += `<small>${r.double_counting_note}</small>`;
    resultBox("comb-result", html);
  } catch (error) { resultBox("comb-result", error.message); }
}

async function runStress() {
  resultBox("stress-result", "Running 5 stress scenarios (physics + corruption + detection)…");
  try {
    const r = await api2("/api/stage4/stress", {});
    let html = `<table><thead><tr><th>Test</th><th>Health</th><th>Alerts</th><th>Diagnosis</th></tr></thead><tbody>`;
    r.results.forEach(x => {
      const badge = {ok: "🟢", degraded: "🟡", failed: "🔴"}[x.health_overall] || "";
      html += `<tr><td>${x.test}<br><small>${x.description}</small></td><td>${badge} ${x.health_overall}</td><td>${x.alerts_raised.join(", ") || "—"}</td><td>${x.diagnosis_state}${x.diagnosis_faults.length ? " (" + x.diagnosis_faults.join("+") + ")" : ""}</td></tr>`;
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
