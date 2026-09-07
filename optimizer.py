"""Stage 3 optimisation studies for FactoryAir Twin.

Everything here is simulated or explicitly labelled illustrative arithmetic.
Nothing controls physical equipment. The setpoint study reuses the main
simulator with pressure-band overrides; the sequencing study is a separate,
documented two-compressor model; the tariff scheduler is pure arithmetic on
the simulated specific energy.
"""

from math import sin

from simulator import GRID_CO2_KG_PER_KWH, PLANT, demand_at, run_simulation

# Monthly extrapolation assumption: the 30-minute simulated block repeats
# continuously (24 h x 30 days = 1440 blocks). This is stated in the UI.
BLOCKS_PER_MONTH = 24 * 60 / 30 * 30  # 1440
REPAIR_COSTS_INR = {
    "leak": 8000, "unloaded": 0, "filter": 4500, "worn": 25000,
    "setpoint": 0, "sequencing": 0, "schedule": 0,
}


def setpoint_search(scenario, tariff=7.0, max_drop=0.6, step=0.2):
    """Find the largest pressure-band reduction that keeps every guardrail.

    Lower header pressure reduces compressor power and raises delivery per
    loaded second. Each candidate is a full re-run of the same scenario with
    shifted cut-in/cut-out; guardrails protect production.
    """
    baseline = run_simulation(scenario, tariff=tariff)
    base = baseline["summary"]
    candidates = []
    chosen = None
    drops = [round(d, 2) for d in [max_drop, max_drop - step, step] if d > 0]
    for drop in sorted(set(drops), reverse=True):
        trial = run_simulation(scenario, tariff=tariff,
                               cut_in_bar=PLANT.cut_in_bar - drop, cut_out_bar=PLANT.cut_out_bar - drop)
        after = trial["summary"]
        saving = base["energy_kwh"] - after["energy_kwh"]
        passes = (after["accepted_units"] >= base["accepted_units"]
                  and after["pressure_failed_cycles"] <= base["pressure_failed_cycles"]
                  and after["minimum_pressure_bar"] >= PLANT.minimum_bar
                  and after["pressure_compliance_pct"] >= 99.0
                  and saving >= 0)
        record = {
            "drop_bar": drop,
            "cut_in_bar": round(PLANT.cut_in_bar - drop, 2),
            "cut_out_bar": round(PLANT.cut_out_bar - drop, 2),
            "energy_kwh": after["energy_kwh"],
            "savings_kwh": round(saving, 6),
            "savings_inr": round(saving * tariff, 2),
            "monthly_kwh": round(saving * BLOCKS_PER_MONTH, 1),
            "monthly_inr": round(saving * BLOCKS_PER_MONTH * tariff),
            "co2_kg_month": round(saving * BLOCKS_PER_MONTH * GRID_CO2_KG_PER_KWH, 1),
            "minimum_pressure_bar": after["minimum_pressure_bar"],
            "accepted_units": after["accepted_units"],
            "peak_power_kw": after["peak_power_kw"],
            "guardrails_pass": passes,
        }
        candidates.append(record)
        if passes and chosen is None:
            chosen = record
    return {
        "scenario": scenario,
        "baseline_band": {"cut_in_bar": PLANT.cut_in_bar, "cut_out_bar": PLANT.cut_out_bar},
        "baseline_energy_kwh": base["energy_kwh"],
        "candidates": candidates,
        "recommended": chosen,
        "rule": ("Largest band reduction whose full re-run keeps accepted output, pressure "
                 "compliance ≥ 99% and minimum pressure at or above the demo requirement."),
        "limitation": ("A 30-minute matched re-run. Real networks need a gradual setpoint trial "
                       "with branch pressure checks before accepting a lower band."),
    }


# ---------------------------------------------------------------------------
# Two-compressor sequencing study (separate illustrative model).
# ---------------------------------------------------------------------------

SEQUENCE_COMPRESSORS = {
    "A": {"label": "Compressor A (7.5 kW, 0.90 m³/min)", "rated": 0.90, "loaded_kw": 7.5, "unloaded_kw": 2.1},
    "B": {"label": "Compressor B (4.3 kW, 0.50 m³/min)", "rated": 0.50, "loaded_kw": 4.3, "unloaded_kw": 1.3},
}
SEQUENCE_RECEIVER_M3 = 0.8
SEQUENCE_LEAK = 0.02
SEQ_CUT_IN, SEQ_CUT_OUT = 6.4, 7.2
LAG_START_BAR, LAG_STOP_BAR = 6.30, 7.05
LAG_START_HOLD_S, LAG_STOP_HOLD_S, LAG_STOP_DELAY_S = 15, 30, 15


def _sequence_run_clean(order, duration=PLANT.duration_seconds):
    lead, lag = order
    state = {lead: "Off", lag: "Off"}
    idle = {lead: 0, lag: 0}
    p, unloaded = 7.0, {lead: 0, lag: 0}
    min_p, low_s, high_s = p, 0, 0
    total_energy, peak_power = 0.0, 0.0
    for t in range(duration):
        base_demand, active = demand_at(t)
        demand = 0.85 * (1 + 0.15 * sin(t / 37)) if active else 0.0
        if p <= SEQ_CUT_IN and state[lead] != "Loaded":
            state[lead], idle[lead] = "Loaded", 0
        elif state[lead] == "Loaded" and p >= SEQ_CUT_OUT:
            state[lead], idle[lead] = "Unloaded", 0
        low_s = low_s + 1 if p < LAG_START_BAR else 0
        high_s = high_s + 1 if p > LAG_STOP_BAR else 0
        if state[lag] == "Off" and low_s >= LAG_START_HOLD_S:
            state[lag], idle[lag] = "Loaded", 0
        if state[lag] == "Loaded" and high_s >= LAG_STOP_HOLD_S:
            state[lag], idle[lag] = "Unloaded", 0
        for name in (lead, lag):
            if state[name] == "Unloaded":
                idle[name] += 1
                delay = LAG_STOP_DELAY_S if name == lag else 20
                if idle[name] >= delay:
                    state[name], idle[name] = "Off", 0
        inlet = power = 0.0
        for name in (lead, lag):
            spec = SEQUENCE_COMPRESSORS[name]
            if state[name] == "Loaded":
                inlet += spec["rated"] * (1 - 0.02 * (p - 7))
                power += spec["loaded_kw"] * (1 + 0.035 * (p - 7))
            elif state[name] == "Unloaded":
                power += spec["unloaded_kw"]
                unloaded[name] += 1
        leak = SEQUENCE_LEAK * max(p, 0) / 7.0
        p += PLANT.reference_bar / SEQUENCE_RECEIVER_M3 * (inlet - demand - leak) / 60
        min_p = min(min_p, p)
        peak_power = max(peak_power, power)
        total_energy += power / 3600
    return {
        "order": list(order),
        "energy_kwh": round(total_energy, 6),
        "monthly_kwh": round(total_energy * BLOCKS_PER_MONTH, 1),
        "unloaded_seconds": unloaded,
        "minimum_pressure_bar": round(min_p, 4),
        "peak_power_kw": round(peak_power, 3),
    }


def compare_sequences(tariff=7.0):
    """Simulate both lead/lag orders on the same demand schedule."""
    strategies = {
        "A leads (efficient base-load)": _sequence_run_clean(("A", "B")),
        "B leads (small base-load)": _sequence_run_clean(("B", "A")),
    }
    best_name = min(strategies, key=lambda k: strategies[k]["energy_kwh"])
    worst_name = max(strategies, key=lambda k: strategies[k]["energy_kwh"])
    delta = strategies[worst_name]["energy_kwh"] - strategies[best_name]["energy_kwh"]
    return {
        "strategies": strategies,
        "recommended": best_name,
        "saving_kwh_per_30min": round(delta, 6),
        "monthly_kwh": round(delta * BLOCKS_PER_MONTH, 1),
        "monthly_inr": round(delta * BLOCKS_PER_MONTH * tariff),
        "co2_kg_month": round(delta * BLOCKS_PER_MONTH * GRID_CO2_KG_PER_KWH, 1),
        "compressors": {k: v["label"] for k, v in SEQUENCE_COMPRESSORS.items()},
        "specific_power": {k: round(v["loaded_kw"] / v["rated"], 2) for k, v in SEQUENCE_COMPRESSORS.items()},
        "note": ("Separate illustrative two-compressor model (0.8 m³ receiver, higher demand). "
                 "The unit with the lower specific power should carry base load; the other trims peaks."),
        "limitation": "Simplified lag start/stop logic; real sequencers use measured demand and restart limits.",
    }


def schedule_savings(sec_kwh_per_unit, units_per_day=300, shiftable_pct=30.0, peak_rate=9.0,
                     off_peak_rate=5.5, solar_rate=6.0, solar_share_pct=50.0, days_per_month=26):
    """Illustrative arithmetic for tariff-aware production scheduling.

    Moves a configurable share of daily compressed-air energy out of the peak
    tariff window into off-peak / solar-rich hours. Not a physics simulation.
    """
    for name, value in (("sec_kwh_per_unit", sec_kwh_per_unit), ("units_per_day", units_per_day),
                        ("shiftable_pct", shiftable_pct), ("peak_rate", peak_rate),
                        ("off_peak_rate", off_peak_rate), ("solar_rate", solar_rate),
                        ("solar_share_pct", solar_share_pct), ("days_per_month", days_per_month)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"{name} must be a non-negative number.")
    if not 0 <= shiftable_pct <= 100 or not 0 <= solar_share_pct <= 100:
        raise ValueError("Percentages must be between 0 and 100.")
    daily_kwh = sec_kwh_per_unit * units_per_day
    shifted_kwh = daily_kwh * shiftable_pct / 100
    solar_kwh = shifted_kwh * solar_share_pct / 100
    offpeak_kwh = shifted_kwh - solar_kwh
    blended_new_rate = (solar_kwh * solar_rate + offpeak_kwh * off_peak_rate) / shifted_kwh if shifted_kwh else peak_rate
    daily_saving_inr = shifted_kwh * (peak_rate - blended_new_rate)
    co2_saved_kg_month = solar_kwh * days_per_month * GRID_CO2_KG_PER_KWH  # solar displaces grid power
    return {
        "inputs": {"sec_kwh_per_unit": sec_kwh_per_unit, "units_per_day": units_per_day,
                   "shiftable_pct": shiftable_pct, "peak_rate": peak_rate, "off_peak_rate": off_peak_rate,
                   "solar_rate": solar_rate, "solar_share_pct": solar_share_pct, "days_per_month": days_per_month},
        "daily_energy_kwh": round(daily_kwh, 2),
        "shifted_kwh_per_day": round(shifted_kwh, 2),
        "daily_saving_inr": round(daily_saving_inr, 2),
        "monthly_saving_inr": round(daily_saving_inr * days_per_month),
        "co2_kg_month_solar_displacement": round(co2_saved_kg_month, 1),
        "note": ("Illustrative arithmetic on the simulated specific energy. It assumes the shifted "
                 "production currently runs in the peak tariff window and that storage/buffer capacity "
                 "allows the move without affecting throughput."),
    }


def intervention_catalog(scenario, tariff=7.0):
    """Ranked, priced action list for the current scenario.

    Savings are extrapolated from matched 30-minute re-runs assuming the
    pattern persists continuously (1440 blocks per month). Costs are
    illustrative SME defaults and fully editable in real use.
    """
    comparison = run_simulation(scenario, False, tariff)
    fixed = run_simulation(scenario, True, tariff)
    actions = []
    alerts = {a["key"]: a for a in comparison["alerts"]}
    b, a = comparison["summary"], fixed["summary"]
    repair_saving = max(0.0, b["energy_kwh"] - a["energy_kwh"])
    if scenario != "normal" and repair_saving > 0:
        actions.append({
            "id": f"repair-{scenario}", "title": alerts.get(scenario, {}).get("title", f"Fix {scenario}"),
            "action": {"leak": "Locate and repair the leak (ultrasonic survey / isolation test).",
                       "unloaded": "Shorten the unloaded stop delay to about 20 seconds.",
                       "filter": "Replace the intake filter element.",
                       "worn": "Overhaul / restore compressor performance."}[scenario],
            "monthly_kwh": round(repair_saving * BLOCKS_PER_MONTH, 1),
            "monthly_inr": round(repair_saving * BLOCKS_PER_MONTH * tariff),
            "co2_kg_month": round(repair_saving * BLOCKS_PER_MONTH * GRID_CO2_KG_PER_KWH, 1),
            "assumed_cost_inr": REPAIR_COSTS_INR[scenario],
            "basis": "Matched 30-minute simulated re-run, extrapolated to continuous operation.",
        })
    setpoint = setpoint_search(scenario, tariff)
    if setpoint["recommended"] and setpoint["recommended"]["savings_kwh"] > 0:
        rec = setpoint["recommended"]
        actions.append({
            "id": "setpoint", "title": "Lower pressure band",
            "action": (f"Shift the band to {rec['cut_in_bar']:.1f}/{rec['cut_out_bar']:.1f} bar(g) "
                       f"after verifying end-use pressures."),
            "monthly_kwh": rec["monthly_kwh"], "monthly_inr": rec["monthly_inr"],
            "co2_kg_month": rec["co2_kg_month"], "assumed_cost_inr": 0,
            "basis": setpoint["rule"],
        })
    sequence = compare_sequences(tariff)
    if sequence["saving_kwh_per_30min"] > 0:
        actions.append({
            "id": "sequencing", "title": f"Sequencing: {sequence['recommended']}",
            "action": "Set the efficient unit as base-load lead; use the smaller unit for peak trimming.",
            "monthly_kwh": sequence["monthly_kwh"], "monthly_inr": sequence["monthly_inr"],
            "co2_kg_month": sequence["co2_kg_month"], "assumed_cost_inr": 0,
            "basis": sequence["note"],
        })
    for item in actions:
        cost = item["assumed_cost_inr"]
        item["payback_days"] = 0 if cost <= 0 else round(cost / max(item["monthly_inr"], 1e-9) * 30, 1)
    actions.sort(key=lambda item: (item["payback_days"], -item["monthly_inr"]))
    return {
        "scenario": scenario, "tariff_inr_kwh": tariff, "actions": actions,
        "assumptions": {
            "blocks_per_month": BLOCKS_PER_MONTH,
            "grid_co2_kg_per_kwh": GRID_CO2_KG_PER_KWH,
            "note": "Monthly figures assume the observed 30-minute pattern persists 24×7. Costs are illustrative.",
        },
    }
