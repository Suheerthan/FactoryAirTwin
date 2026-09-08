"""FactoryAir Twin Stage 4: production-readiness layer (SIMULATED DATA).

Adds eight features on top of the Stage 1-3 prototype:

1. Plant setup & calibration panel  -> get_config / update_config
2. Sensor health monitoring          -> sensor_health / corrupt_readings
3. Unknown & multiple-fault handling -> decide_fault_state / diagnose_scenario
4. Persistent maintenance workflow   -> create_ticket / ticket_action / list_tickets
5. Measured post-repair verification -> verify_ticket
6. Combined-action savings           -> combined_savings (no double-counting)
7. Downloadable PDF reports          -> build_pdf_report
8. Stress-test mode                  -> run_stress / STRESS_PROFILES

Everything here runs on simulated data. No factory measurements exist.
Money and carbon figures are illustrative extrapolations of a 30-minute run.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from random import Random

from simulator import GRID_CO2_KG_PER_KWH, PLANT, SCENARIOS, run_simulation
from optimizer import compare_sequences, schedule_savings, setpoint_search

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CONFIG_PATH = DATA_DIR / "plant_config.json"
TICKETS_PATH = DATA_DIR / "maintenance.json"
RUNS_PER_MONTH = 1440  # one 30-minute simulation run == 30 minutes of plant time

FAULT_KEYS = ("leak", "unloaded", "filter", "worn")

DEFAULT_CONFIG = {
    "site_name": "Demo SME compressor room",
    "compressor_model": "Illustrative 7.5 kW fixed-speed unit",
    "rated_power_kw": 7.5,
    "rated_flow_m3_min": 0.90,
    "receiver_volume_m3": 0.50,
    "cut_in_bar": 6.4,
    "cut_out_bar": 7.2,
    "min_pressure_bar": 6.0,
    "max_pressure_bar": 7.4,
    "tariff_inr_kwh": 7.0,
    "grid_co2_kg_per_kwh": GRID_CO2_KG_PER_KWH,
}

_CONFIG_LIMITS = {
    "rated_power_kw": (0.5, 500.0),
    "rated_flow_m3_min": (0.05, 60.0),
    "receiver_volume_m3": (0.05, 50.0),
    "cut_in_bar": (5.5, 7.5),
    "cut_out_bar": (5.5, 7.5),
    "min_pressure_bar": (4.0, 7.5),
    "max_pressure_bar": (5.0, 12.0),
    "tariff_inr_kwh": (0.0, 100.0),
    "grid_co2_kg_per_kwh": (0.0, 2.0),
}


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------- 1. CONFIG
def get_config(path=None):
    path = Path(path) if path else CONFIG_PATH
    config = dict(DEFAULT_CONFIG)
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                for key in DEFAULT_CONFIG:
                    if key in stored and isinstance(stored[key], type(DEFAULT_CONFIG[key])):
                        config[key] = stored[key]
        except (OSError, ValueError):
            pass
    return config


def update_config(changes, path=None):
    """Validate and persist calibration values. Returns (config, error)."""
    path = Path(path) if path else CONFIG_PATH
    if not isinstance(changes, dict) or not changes:
        return None, "Send a JSON object with at least one calibration value."
    config = get_config(path)
    for key, value in changes.items():
        if key in ("site_name", "compressor_model"):
            if not isinstance(value, str) or not 1 <= len(value.strip()) <= 80:
                return None, f"{key} must be text of 1-80 characters."
            config[key] = value.strip()
            continue
        if key not in _CONFIG_LIMITS:
            return None, f"Unknown calibration field: {key}."
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None, f"{key} must be a number."
        low, high = _CONFIG_LIMITS[key]
        if not low <= float(value) <= high:
            return None, f"{key} must be between {low} and {high}."
        config[key] = float(value)
    if config["cut_in_bar"] >= config["cut_out_bar"]:
        return None, "cut_in_bar must be below cut_out_bar."
    if config["min_pressure_bar"] >= config["cut_in_bar"]:
        return None, "min_pressure_bar must be below cut_in_bar."
    if config["max_pressure_bar"] < config["cut_out_bar"]:
        return None, "max_pressure_bar must be at or above cut_out_bar."
    path.parent.mkdir(parents=True, exist_ok=True)
    config["updated_at"] = _now()
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config, None


# ------------------------------------------------------ 2. SENSOR HEALTH
def sensor_health(readings, config=None):
    """Check the three signals for missing, stale, stuck and out-of-range data.

    Returns a per-sensor report plus an overall status:
    ok -> nothing found; degraded -> quality issues; failed -> unusable data.
    """
    config = config or get_config()
    issues = []
    sensors = {}

    seconds = [r.get("second") for r in readings if r.get("second") is not None]
    gaps = []
    for prev, nxt in zip(seconds, seconds[1:]):
        if nxt - prev > 1:
            gaps.append({"after_second": prev, "missing_seconds": nxt - prev - 1})
    if gaps:
        issues.append(f"Timestamp gaps: {len(gaps)} gap(s), {sum(g['missing_seconds'] for g in gaps)} missing sample(s).")

    def check(name, field, low, high, stuck_tolerance, active_when=None):
        """active_when(row): a frozen signal is only suspicious while the
        machine state expects movement (zero power with the unit Off, and
        slow pressure decay below sensor resolution, are normal)."""
        missing = stuck_run = 0
        out_of_range = 0
        last_value = None
        run = 0
        for row in readings:
            value = row.get(field)
            if value is None or (isinstance(value, float) and not math.isfinite(value)):
                missing += 1
                run = 0
                last_value = None
                continue
            if not low <= value <= high:
                out_of_range += 1
            if active_when is not None and not active_when(row):
                run = 0
                last_value = None
                continue
            if last_value is not None and abs(value - last_value) <= stuck_tolerance:
                run += 1
            else:
                run = 1
            stuck_run = max(stuck_run, run)
            last_value = value
        if stuck_run >= 60:
            issues.append(f"{name}: value stuck for {stuck_run} consecutive samples.")
        if missing:
            issues.append(f"{name}: {missing} missing sample(s).")
        if out_of_range:
            issues.append(f"{name}: {out_of_range} sample(s) outside the plausible range {low:g} to {high:g}.")
        status = "ok"
        if out_of_range or missing >= 5:
            status = "failed"
        elif missing or stuck_run >= 60:
            status = "degraded"
        sensors[name] = {"status": status, "missing": missing, "out_of_range": out_of_range,
                         "longest_stuck_run": stuck_run}
        return status

    max_power = max(config.get("rated_power_kw", PLANT.loaded_kw) * 1.5, 1.0)
    statuses = [
        # Pressure may legitimately drift slower than sensor resolution while Off.
        check("pressure_bar", "pressure_bar", 0.0, 12.0, 0.001,
              active_when=lambda row: row.get("state") != "Off"),
        # Off (0 kW) and Unloaded (constant idle kW) are legitimate constant
        # power states; only Loaded operation should show varying power.
        check("power_kw", "power_kw", -0.01, max_power, 0.0005,
              active_when=lambda row: row.get("state") == "Loaded"),
        check("temperature_c", "temperature_c", -10.0, 95.0, 0.01),
    ]
    if gaps:
        statuses.append("degraded")
    overall = "ok" if all(s == "ok" for s in statuses) else ("failed" if any(s == "failed" for s in statuses) else "degraded")
    return {"overall": overall, "sensors": sensors, "gaps": gaps[:10], "issues": issues,
            "samples_checked": len(readings),
            "note": "Health checks run on the raw signal stream before any AI scoring."}


def corrupt_readings(readings, corruptions=("dropout",), seed=4400):
    """Apply synthetic sensor faults to a clean run (stress-test generator)."""
    rng = Random(seed)
    rows = [dict(r) for r in readings]
    corruptions = {corruptions} if isinstance(corruptions, str) else set(corruptions)
    if "dropout" in corruptions:
        victims = rng.sample(range(60, len(rows) - 1), max(1, len(rows) // 25))
        rows = [r for i, r in enumerate(rows) if i not in set(victims)]
    if "stuck_pressure" in corruptions:
        start = len(rows) // 3
        frozen = rows[start]["pressure_bar"]
        for i in range(start, min(start + 120, len(rows))):
            rows[i]["pressure_bar"] = frozen
    if "power_noise" in corruptions:
        for row in rows:
            row["power_kw"] = max(0.0, row["power_kw"] + rng.gauss(0.0, 0.35))
        for _ in range(4):
            rows[rng.randrange(len(rows))]["power_kw"] += rng.uniform(3.0, 6.0)
    if "range_violation" in corruptions:
        rows[len(rows) // 2]["pressure_bar"] = -0.5
        rows[len(rows) // 2 + 30]["power_kw"] = 250.0
    return rows


# ------------------------------------- 3. UNKNOWN & MULTIPLE-FAULT HANDLING
def decide_fault_state(rule_keys, ai_classes=None, anomaly_ratio=0.0):
    """Pure decision logic: known / multiple / unknown / normal.

    rule_keys   : fault keys raised by engineering rules (leak/unloaded/filter/worn)
    ai_classes  : fault types the Stage 3 classifier flagged (may repeat per window)
    anomaly_ratio: share of Stage 2 windows flagged anomalous (0..1)
    """
    ai_classes = ai_classes or []
    counts = {}
    for key in list(rule_keys) + list(ai_classes):
        if key in FAULT_KEYS:
            counts[key] = counts.get(key, 0) + 1
    candidates = sorted(counts)
    if len(candidates) >= 2:
        return {"state": "multiple", "faults": candidates,
                "confidence": round(min(0.95, 0.5 + 0.1 * sum(counts[c] for c in candidates)), 3),
                "explanation": ("Several fault signatures overlap. Repair the dominant one first "
                                "(see ranked catalogue), then re-run diagnosis before touching the rest.")}
    if len(candidates) == 1:
        return {"state": "known", "faults": candidates,
                "confidence": round(min(0.99, 0.6 + 0.05 * counts[candidates[0]]), 3),
                "explanation": f"A single fault signature dominates: {candidates[0]}."}
    if anomaly_ratio >= 0.10:
        return {"state": "unknown", "faults": [],
                "confidence": round(min(0.9, 0.3 + anomaly_ratio), 3),
                "explanation": ("The anomaly detector sees an unusual pattern that matches no known "
                                "fault. Investigate manually before creating a maintenance ticket.")}
    return {"state": "normal", "faults": [], "confidence": 0.0,
            "explanation": "No fault signature found."}


def diagnose_scenario(scenario, tariff=None, *, run=None):
    """Run a scenario and combine rules + Stage 2 + Stage 3 into a fault state."""
    from ai_detector import infer_readings
    from ai_stage3 import infer_stage3

    run = run or run_simulation(scenario, tariff=tariff or get_config()["tariff_inr_kwh"])
    rule_keys = {a["key"] for a in run.get("alerts", [])} & set(FAULT_KEYS)
    anomaly_ratio, ai_classes = 0.0, []
    stage2 = infer_readings(run["readings"])
    if stage2.get("enabled") and stage2.get("windows"):
        flags = [w for w in stage2["windows"] if w.get("is_anomaly")]
        anomaly_ratio = len(flags) / len(stage2["windows"])
    stage3 = infer_stage3(run["readings"])
    if stage3.get("enabled"):
        ai_classes = [w["predicted_class"] for w in stage3["windows"] if w.get("is_fault")]
    state = decide_fault_state(rule_keys, ai_classes, anomaly_ratio)
    state.update({"scenario": scenario, "rule_keys": sorted(rule_keys),
                  "ai_flagged_windows": len(ai_classes), "anomaly_ratio": round(anomaly_ratio, 3),
                  "ai_models": {"stage2": stage2.get("enabled", False), "stage3": stage3.get("enabled", False)}})
    return state, run


# ------------------------------------------------- 4. MAINTENANCE WORKFLOW
ACTIONS = {
    "acknowledge": {"from": ("open",), "to": "acknowledged"},
    "start_repair": {"from": ("acknowledged",), "to": "in_repair"},
    "start_verify": {"from": ("in_repair",), "to": "verifying"},
    "resolve": {"from": ("verifying",), "to": "resolved"},
    "reopen": {"from": ("resolved", "cancelled"), "to": "open"},
    "cancel": {"from": ("open", "acknowledged"), "to": "cancelled"},
}


def _load_store(path=None):
    path = Path(path) if path else TICKETS_PATH
    if path.exists():
        try:
            store = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(store, dict) and isinstance(store.get("tickets"), list):
                return store, path
        except (OSError, ValueError):
            pass
    return {"tickets": [], "sequence": 0}, path


def _save_store(store, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def list_tickets(path=None):
    store, _ = _load_store(path)
    return store["tickets"]


def get_ticket(ticket_id, path=None):
    return next((t for t in list_tickets(path) if t["id"] == ticket_id), None)


def create_ticket(fault_type, title, evidence="", predicted=None, operator="operator", path=None):
    if fault_type not in FAULT_KEYS + ("unknown",):
        raise ValueError(f"fault_type must be one of {FAULT_KEYS + ('unknown',)}.")
    if not title or not isinstance(title, str) or len(title) > 120:
        raise ValueError("Ticket title must be 1-120 characters.")
    store, path = _load_store(path)
    store["sequence"] += 1
    ticket = {
        "id": f"M-{store['sequence']:04d}",
        "fault_type": fault_type,
        "title": title.strip(),
        "evidence": str(evidence)[:400],
        "predicted_savings": predicted or {},
        "state": "open",
        "created_at": _now(),
        "verification": None,
        "logs": [{"at": _now(), "operator": str(operator)[:40] or "operator",
                  "action": "created", "note": "Ticket created."}],
    }
    store["tickets"].append(ticket)
    _save_store(store, path)
    return ticket


def ticket_action(ticket_id, action, operator="operator", note="", path=None):
    if action not in ACTIONS:
        raise ValueError(f"Unknown action. Use one of: {', '.join(ACTIONS)}.")
    store, path = _load_store(path)
    ticket = next((t for t in store["tickets"] if t["id"] == ticket_id), None)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} does not exist.")
    rule = ACTIONS[action]
    if ticket["state"] not in rule["from"]:
        raise ValueError(f"Cannot '{action}' a ticket in state '{ticket['state']}' "
                         f"(allowed from: {', '.join(rule['from'])}).")
    ticket["state"] = rule["to"]
    ticket["logs"].append({"at": _now(), "operator": str(operator)[:40] or "operator",
                           "action": action, "note": str(note)[:400]})
    _save_store(store, path)
    return ticket


# ------------------------------------------ 5. POST-REPAIR VERIFICATION
REPAIRABLE = {"leak": "Leak repaired (simulated)", "unloaded": "Stop delay shortened (simulated)"}


def verify_ticket(ticket_id, operator="operator", path=None):
    """Compare measured (simulated) energy before and after the modelled repair."""
    store, path = _load_store(path)
    ticket = next((t for t in store["tickets"] if t["id"] == ticket_id), None)
    if ticket is None:
        raise ValueError(f"Ticket {ticket_id} does not exist.")
    if ticket["state"] not in ("in_repair", "verifying"):
        raise ValueError("Move the ticket to in_repair before verifying.")
    fault = ticket["fault_type"]
    if fault not in REPAIRABLE or fault not in SCENARIOS:
        ticket["verification"] = {
            "verified_at": _now(), "status": "not_modelled",
            "message": (f"The simulator has no repair model for '{fault}'. Verification needs "
                        "field measurements before/after the real repair."),
        }
        ticket["logs"].append({"at": _now(), "operator": str(operator)[:40], "action": "verify",
                               "note": "Verification attempted; repair not modelled."})
        _save_store(store, path)
        return ticket["verification"]
    tariff = get_config()["tariff_inr_kwh"]
    before = run_simulation(fault, intervention=False, tariff=tariff)
    after = run_simulation(fault, intervention=True, tariff=tariff)
    saved_kwh = before["summary"]["energy_kwh"] - after["summary"]["energy_kwh"]
    saved_inr_month = saved_kwh * RUNS_PER_MONTH * tariff
    predicted = ticket.get("predicted_savings") or {}
    predicted_inr = predicted.get("inr_per_month")
    deviation = None
    if isinstance(predicted_inr, (int, float)) and predicted_inr > 0:
        deviation = round(100 * (saved_inr_month - predicted_inr) / predicted_inr, 1)
    ticket["state"] = "verifying"
    ticket["verification"] = {
        "verified_at": _now(), "status": "measured_simulated",
        "scenario": fault, "repair": REPAIRABLE[fault],
        "before_kwh_30min": before["summary"]["energy_kwh"],
        "after_kwh_30min": after["summary"]["energy_kwh"],
        "saved_kwh_30min": round(saved_kwh, 4),
        "saved_inr_per_month": round(saved_inr_month),
        "saved_co2_kg_per_month": round(saved_kwh * RUNS_PER_MONTH * GRID_CO2_KG_PER_KWH, 1),
        "predicted_inr_per_month": predicted_inr,
        "deviation_pct_vs_predicted": deviation,
        "note": "Measured from matched 30-minute simulated runs; extrapolated to a 24x7 month.",
    }
    ticket["logs"].append({"at": _now(), "operator": str(operator)[:40], "action": "verify",
                           "note": f"Before {before['summary']['energy_kwh']:.3f} kWh vs after "
                                   f"{after['summary']['energy_kwh']:.3f} kWh per 30 min."})
    _save_store(store, path)
    return ticket["verification"]


# --------------------------------------------- 6. COMBINED SAVINGS (NO DOUBLE COUNT)
def combined_savings(scenario="leak", actions=("repair_leak", "setpoint"), tariff=None):
    """One combined re-simulation for physics-overlapping actions, plus clearly
    separated independent adders, so savings are never counted twice."""
    if scenario not in SCENARIOS:
        raise ValueError("Choose normal, leak, unloaded, filter or worn.")
    actions = list(dict.fromkeys(actions))
    allowed = {"repair_leak", "setpoint", "sequencing", "scheduling"}
    bad = set(actions) - allowed
    if bad:
        raise ValueError(f"Unknown actions: {', '.join(sorted(bad))}. Use {', '.join(sorted(allowed))}.")
    tariff = get_config()["tariff_inr_kwh"] if tariff is None else float(tariff)

    baseline = run_simulation(scenario, tariff=tariff)
    intervention = "repair_leak" in actions and scenario == "leak"
    cut_in = cut_out = None
    setpoint_note = "Not applicable: setpoint search runs on the current scenario."
    if "setpoint" in actions:
        study = setpoint_search(scenario, tariff)
        if study.get("best"):
            cut_in, cut_out = study["best"]["cut_in_bar"], study["best"]["cut_out_bar"]
            setpoint_note = f"Setpoint lowered to {cut_in:.1f}/{cut_out:.1f} bar(g)."
        else:
            setpoint_note = "Setpoint search found no safe lower band; keeping current setpoint."
    combined = run_simulation(scenario, intervention=intervention, tariff=tariff,
                              cut_in_bar=cut_in, cut_out_bar=cut_out)

    base_kwh = baseline["summary"]["energy_kwh"]
    combined_kwh = combined["summary"]["energy_kwh"]
    measured_saved_kwh = base_kwh - combined_kwh
    measured_inr_month = measured_saved_kwh * RUNS_PER_MONTH * tariff

    parts = [{"action": "repair_leak + setpoint (one combined re-simulation)",
              "kwh_saved_30min": round(measured_saved_kwh, 4),
              "inr_per_month": round(measured_inr_month)}] if actions else []
    independent_inr = 0.0
    if "sequencing" in actions:
        seq = compare_sequences(tariff)
        best = seq.get("best_monthly_saving_inr", 0.0)
        independent_inr += best
        parts.append({"action": "sequencing (independent two-compressor study)",
                      "kwh_saved_30min": None, "inr_per_month": round(best)})
    if "scheduling" in actions and baseline["summary"].get("sec_kwh_per_unit"):
        sched = schedule_savings(baseline["summary"]["sec_kwh_per_unit"])
        independent_inr += sched.get("monthly_saving_inr", 0.0)
        parts.append({"action": "tariff scheduling (independent arithmetic)",
                      "kwh_saved_30min": None, "inr_per_month": round(sched.get("monthly_saving_inr", 0.0))})

    standalone_sum = 0.0
    if "repair_leak" in actions and scenario == "leak":
        standalone_sum += (base_kwh - run_simulation(scenario, intervention=True, tariff=tariff)["summary"]["energy_kwh"]) * RUNS_PER_MONTH * tariff
    if "setpoint" in actions and cut_in is not None:
        standalone_sum += (base_kwh - run_simulation(scenario, tariff=tariff, cut_in_bar=cut_in, cut_out_bar=cut_out)["summary"]["energy_kwh"]) * RUNS_PER_MONTH * tariff

    return {
        "scenario": scenario, "tariff_inr_kwh": tariff,
        "baseline_kwh_30min": base_kwh, "combined_kwh_30min": combined_kwh,
        "pressure_compliance_pct": combined["summary"]["pressure_compliance_pct"],
        "accepted_units": combined["summary"]["accepted_units"],
        "parts": parts,
        "total_inr_per_month": round(measured_inr_month + independent_inr),
        "total_co2_kg_per_month": round((measured_saved_kwh * RUNS_PER_MONTH) * GRID_CO2_KG_PER_KWH, 1),
        "double_counting_note": (
            "Repair + setpoint savings are measured in ONE combined re-simulation, so their overlap "
            "is not counted twice. Sequencing and scheduling touch different mechanisms and are added "
            "separately from their own studies."),
        "assumptions": [setpoint_note,
                        "Monthly figures extrapolate one 30-minute simulated pattern 24x7.",
                        "Guardrails (output held, pressure compliance >= 99%, minimum pressure) enforced by the simulator."],
        "standalone_sum_inr_per_month": round(standalone_sum + independent_inr),
    }


# --------------------------------------------------------- 8. STRESS TESTS
STRESS_PROFILES = {
    "sensor_dropout": {"description": "5% of samples drop out (gaps)", "corruptions": ("dropout",),
                       "scenario": "leak", "kwargs": {}},
    "stuck_pressure": {"description": "Pressure transmitter stuck for 120 s", "corruptions": ("stuck_pressure",),
                       "scenario": "normal", "kwargs": {}},
    "power_noise": {"description": "Heavy electrical noise + 4 spikes", "corruptions": ("power_noise",),
                    "scenario": "normal", "kwargs": {}},
    "demand_shift": {"description": "Demand jumps 25% (busier shift)", "corruptions": (),
                     "scenario": "normal", "kwargs": {"demand_multiplier": 1.25}},
    "simultaneous_faults": {"description": "Leak AND filter blockage together", "corruptions": (),
                            "scenario": "leak", "kwargs": {"secondary_fault": "filter"}},
}


def run_stress(tests=None, seed=4400):
    from simulator import detect_alerts

    chosen = list(tests) if tests else list(STRESS_PROFILES)
    bad = set(chosen) - set(STRESS_PROFILES)
    if bad:
        raise ValueError(f"Unknown stress tests: {', '.join(sorted(bad))}.")
    results = []
    for name in chosen:
        profile = STRESS_PROFILES[name]
        run = run_simulation(profile["scenario"], variation_seed=None, **profile["kwargs"])
        readings = corrupt_readings(run["readings"], profile["corruptions"], seed=seed)
        health = sensor_health(readings)
        alerts = detect_alerts(readings)
        rule_keys = {a["key"] for a in alerts} & set(FAULT_KEYS)
        state = decide_fault_state(rule_keys)
        results.append({
            "test": name, "description": profile["description"],
            "health_overall": health["overall"], "health_issues": health["issues"][:4],
            "alerts_raised": sorted(rule_keys), "diagnosis_state": state["state"],
            "diagnosis_faults": state["faults"],
            "energy_kwh": run["summary"]["energy_kwh"],
        })
    return {"seed": seed, "data_mode": "simulated stress injection", "results": results,
            "note": "Corruption is applied to simulated readings after the physics run."}


# ------------------------------------------------------------- 7. PDF REPORT
def _ascii(text):
    replacements = {"₹": "Rs.", "³": "3", "·": "-", "≥": ">=", "≤": "<=", "×": "x",
                    "≈": "~", "₂": "2", "→": "->", "°": " deg ", "✅": "[ok]", "❌": "[x]"}
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("ascii", "replace").decode("ascii")


def build_pdf_report(path=None):
    """Generate a plain-text PDF summary of the whole prototype (simulated data)."""
    from fpdf import FPDF

    path = Path(path) if path else DATA_DIR / "FactoryAirTwin_report.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    config = get_config()
    normal = run_simulation("normal", tariff=config["tariff_inr_kwh"])
    leak = run_simulation("leak", tariff=config["tariff_inr_kwh"])
    leak_fixed = run_simulation("leak", intervention=True, tariff=config["tariff_inr_kwh"])
    combined = combined_savings("leak", ("repair_leak", "setpoint"), config["tariff_inr_kwh"])
    stress = run_stress()

    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 18)
    pdf.cell(0, 10, "FactoryAir Twin - Prototype Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.set_text_color(90)
    pdf.cell(0, 6, "Team Volts and Bolts - Schneider Electric Yuva Yodha 2026 - Challenge 04", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, "ALL DATA SIMULATED. Generated " + _now(), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0)

    def section(title):
        pdf.ln(4)
        pdf.set_font("Helvetica", "B", 13)
        pdf.set_fill_color(223, 243, 233)
        pdf.cell(0, 8, " " + title, new_x="LMARGIN", new_y="NEXT", fill=True)
        pdf.set_font("Helvetica", "", 10)

    def rows(pairs):
        for key, value in pairs:
            pdf.cell(70, 6, _ascii(str(key)))
            pdf.cell(0, 6, _ascii(str(value)), new_x="LMARGIN", new_y="NEXT")

    section("1. Plant setup & calibration")
    rows([(k, v) for k, v in config.items()])

    section("2. Sensor health (clean 30-min run)")
    health = sensor_health(normal["readings"], config)
    rows([("Overall status", health["overall"]), ("Samples checked", health["samples_checked"]),
          ("Issues", "; ".join(health["issues"]) or "none")])

    section("3. Fault diagnosis (rules + AI)")
    for scenario in ("normal", "leak", "worn"):
        state, _ = diagnose_scenario(scenario)
        rows([(f"Scenario '{scenario}'", f"state={state['state']} faults={state['faults'] or '-'} "
                                          f"confidence={state['confidence']}")])

    section("4. Energy comparison (30-minute runs)")
    rows([("Normal run", f"{normal['summary']['energy_kwh']:.3f} kWh"),
          ("Leak run", f"{leak['summary']['energy_kwh']:.3f} kWh"),
          ("Leak repaired", f"{leak_fixed['summary']['energy_kwh']:.3f} kWh"),
          ("Saving from repair", f"{leak['summary']['energy_kwh'] - leak_fixed['summary']['energy_kwh']:.3f} kWh "
                                 f"(~Rs.{(leak['summary']['energy_kwh'] - leak_fixed['summary']['energy_kwh']) * RUNS_PER_MONTH * config['tariff_inr_kwh']:,.0f}/month)")])

    section("5. Combined-action savings (no double counting)")
    rows([("Actions", ", ".join(p["action"] for p in combined["parts"])),
          ("Total", f"Rs.{combined['total_inr_per_month']:,}/month"),
          ("CO2e avoided", f"{combined['total_co2_kg_per_month']} kg/month"),
          ("Method", _ascii(combined["double_counting_note"])[:160])])

    section("6. Maintenance workflow")
    tickets = list_tickets()
    if not tickets:
        pdf.cell(0, 6, "No tickets yet. Create one from a diagnosis.", new_x="LMARGIN", new_y="NEXT")
    for ticket in tickets[-8:]:
        rows([(ticket["id"], f"{ticket['fault_type']} - {ticket['state']} - {ticket['title'][:50]}")])
        if ticket.get("verification"):
            verification = ticket["verification"]
            pdf.cell(10)
            pdf.cell(0, 6, _ascii(f"   verified: saved {verification.get('saved_kwh_30min', 'n/a')} kWh/30min "
                                  f"({verification.get('status')})"), new_x="LMARGIN", new_y="NEXT")

    section("7. Stress-test results")
    for result in stress["results"]:
        rows([(result["test"], f"health={result['health_overall']} alerts={result['alerts_raised'] or '-'} "
                               f"diagnosis={result['diagnosis_state']}")])

    section("8. Assumptions & limitations")
    for line in [
        "- All readings come from the built-in simulator; no factory data is used.",
        "- Money and CO2e figures extrapolate one 30-minute pattern to a 24x7 month.",
        "- AI models are trained on labelled simulated runs only; field validation pending.",
        "- Repairs for 'filter' and 'worn' are not yet modelled; verification flags them as not_modelled.",
        "- Operator approval is required before any recommendation is acted on.",
    ]:
        pdf.cell(0, 6, _ascii(line), new_x="LMARGIN", new_y="NEXT")

    pdf.output(str(path))
    return path
