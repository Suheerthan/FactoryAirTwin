"""FactoryAir Twin: a small, deterministic teaching simulator.

All equipment values below are illustrative assumptions. No factory data is
included. Flows are m3/min at the reference pressure and
temperature. Pressure is gauge bar. See README.md for model limitations.

Stage 3 adds two predictive-maintenance scenarios (progressive filter
blockage, worn compressor), optional pressure-band overrides for setpoint
studies, and money/carbon impact estimates on leak alerts. The original
three scenarios keep exactly their previous settings and results.
"""

from dataclasses import asdict, dataclass
from math import sin
from random import Random


@dataclass(frozen=True)
class Plant:
    receiver_m3: float = 0.5
    reference_bar: float = 1.01325
    rated_flow_m3_min: float = 0.90
    loaded_kw: float = 7.5
    unloaded_kw: float = 2.1
    cut_in_bar: float = 6.4
    cut_out_bar: float = 7.2
    minimum_bar: float = 6.0
    maximum_bar: float = 7.4
    duration_seconds: int = 1800
    cycle_seconds: int = 15


PLANT = Plant()
SCENARIOS = {
    "normal": {"label": "Normal operation", "leak_m3_min": 0.015, "stop_delay": 20},
    "leak": {"label": "Suspected air leak", "leak_m3_min": 0.22, "stop_delay": 20},
    "unloaded": {"label": "Extended unloaded running", "leak_m3_min": 0.015, "stop_delay": 240},
    "filter": {"label": "Progressive filter blockage", "leak_m3_min": 0.015, "stop_delay": 20},
    "worn": {"label": "Worn compressor", "leak_m3_min": 0.015, "stop_delay": 20},
}

# Illustrative conversion constants for money/carbon impact estimates.
GRID_CO2_KG_PER_KWH = 0.79  # Indian grid average, editable in the dashboard.
SP_REF = PLANT.loaded_kw / PLANT.rated_flow_m3_min  # nominal kW per m3/min.


def demand_at(second):
    """Identical demand schedule in both comparison runs: 3.5 min on / 1.5 off."""
    active = second % 300 < 210
    return (0.45 * (1 + 0.10 * sin(second / 37)) if active else 0.0), active


def compressor_map(pressure, state):
    """Illustrative locally known performance curve, NOT a manufacturer curve."""
    if state == "Loaded":
        return (
            PLANT.rated_flow_m3_min * (1 - 0.02 * (pressure - 7)),
            PLANT.loaded_kw * (1 + 0.035 * (pressure - 7)),
        )
    return (0.0, PLANT.unloaded_kw if state == "Unloaded" else 0.0)


def _maintenance_rules(rows, alerts, emitted):
    """Filter blockage / wear rules from electrical and pressure signals only.

    Wear signature: the motor draws more power than the nominal map expects
    for the same pressure (power ratio > 1.05 sustained).
    Filter signature: while loaded in production, header pressure climbs more
    slowly than the nominal map predicts (restricted inlet flow), and the
    climb rate keeps falling as the blockage grows. A leak also slows the
    climb, so the filter rule only applies when no leak alert exists and the
    degradation is progressive.
    """
    loaded = [(r["second"], r["power_kw"] / compressor_map(r["pressure_bar"], "Loaded")[1])
              for r in rows if r["state"] == "Loaded"]
    if loaded and sum(ratio for _, ratio in loaded) / len(loaded) > 1.05 and "worn" not in emitted:
        first = next((second for second, ratio in loaded if ratio > 1.05), rows[-1]["second"])
        alerts.append({
            "key": "worn", "severity": "warning", "second": first,
            "title": "Compressor deterioration suspected",
            "evidence": (f"Motor power is about {sum(r for _, r in loaded) / len(loaded):.2f}× the nominal "
                         "map expectation across loaded operation. The machine uses more electricity "
                         "for the same pressure and delivery."),
            "action": "Plan an inspection/overhaul window. Compare energy before and after service.",
        })
        emitted.add("worn")
    if "leak" in emitted or "filter" in emitted:
        return
    rises = [(rows[i]["second"], rows[i]["pressure_bar"] - rows[i - 1]["pressure_bar"])
             for i in range(1, len(rows))
             if rows[i]["state"] == "Loaded" and rows[i]["production_active"]]
    if len(rises) < 240:
        return
    half = len(rises) // 2
    first_mean = sum(dp for _, dp in rises[:half]) / half
    second_mean = sum(dp for _, dp in rises[half:]) / (len(rises) - half)
    if second_mean < 0.0075 and first_mean - second_mean > 0.0008:
        first_cross = next((second for second, dp in rises if dp < 0.004), rises[half][0])
        alerts.append({
            "key": "filter", "severity": "warning", "second": first_cross,
            "title": "Filter blockage suspected",
            "evidence": (f"While loaded in production, header pressure now climbs {second_mean * 1000:.1f} "
                         f"mbar/s versus {first_mean * 1000:.1f} mbar/s earlier. The compressor loads longer "
                         "for the same delivery, a typical intake-restriction pattern."),
            "action": "Check the intake filter differential pressure. Replace the element and re-test.",
        })
        emitted.add("filter")


def _passive_outflow(rows):
    """Duration-weighted receiver mass-balance outflow over non-loaded seconds.

    Uses pressure endpoints of contiguous passive segments instead of the
    noisy one-second display estimate, so sensor noise largely cancels.
    Returns m3/min at reference conditions, or None when too few passive
    seconds are available.
    """
    passive = [r for r in rows if r["state"] != "Loaded"]
    if len(passive) < 20:
        return None
    total_m3, total_seconds = 0.0, 0
    for prev, nxt in zip(passive, passive[1:]):
        if nxt["second"] - prev["second"] == 1:
            total_m3 += -(nxt["pressure_bar"] - prev["pressure_bar"]) * PLANT.receiver_m3 / PLANT.reference_bar
            total_seconds += 1
    if total_seconds < 15:
        return None
    return total_m3 / total_seconds * 60


def detect_alerts(rows, tariff=7.0):
    """Engineering rules using available readings; no hidden fault label input."""
    alerts = []
    emitted = set()
    unloaded_seconds = 0
    for index, row in enumerate(rows):
        unloaded_seconds = unloaded_seconds + 1 if row["state"] == "Unloaded" else 0
        if index >= 44:
            window = rows[index - 44:index + 1]
            if not any(r["production_active"] for r in window):
                # Mass-balance outflow while the compressor is not pumping
                # (Off/Unloaded); loaded recovery seconds store air and would
                # look like phantom outflow.
                outflow = _passive_outflow(window)
                if outflow is not None and outflow > 0.08 and "leak" not in emitted:
                    alerts.append({
                        "key": "leak", "severity": "warning", "second": row["second"],
                        "title": "Suspected leakage or unrecorded air use",
                        "evidence": f"Production idle for 45 s; estimated outflow {outflow:.3f} m³/min at reference conditions.",
                        "action": "Inspect the air network. Branch sensing or an isolation test is needed to locate the source.",
                    })
                    emitted.add("leak")
        if unloaded_seconds >= 60 and "unloaded" not in emitted:
            alerts.append({
                "key": "unloaded", "severity": "warning", "second": row["second"],
                "title": "Extended unloaded operation",
                "evidence": "Motor has used power without delivering air for 60 consecutive seconds.",
                "action": "Review the stop delay and restart requirements. Test a shorter delay in the simulator.",
            })
            emitted.add("unloaded")
        if row["pressure_bar"] < PLANT.minimum_bar and "pressure" not in emitted:
            alerts.append({
                "key": "pressure", "severity": "critical", "second": row["second"],
                "title": "Pressure below the demo minimum",
                "evidence": f"Header pressure is below the assumed {PLANT.minimum_bar:.1f} bar requirement.",
                "action": "Review the capacity and demand assumptions before accepting any operating change.",
            })
            emitted.add("pressure")
    _maintenance_rules(rows, alerts, emitted)
    # Quantify the leak alert in kWh, ₹ and CO2e using only recorded readings.
    for alert in alerts:
        if alert["key"] != "leak":
            continue
        idle_rows = [r for r in rows if not r["production_active"]]
        idle_outflow = _passive_outflow(idle_rows) or 0.0
        excess = max(0.0, idle_outflow - SCENARIOS["normal"]["leak_m3_min"])
        loaded_sp = [r["power_kw"] / max(compressor_map(r["pressure_bar"], "Loaded")[0], 0.05)
                     for r in rows if r["state"] == "Loaded"]
        sp = sum(loaded_sp) / len(loaded_sp) if loaded_sp else SP_REF
        loss_kw = excess * sp
        kwh_day = loss_kw * 24
        alert["impact"] = {
            "excess_idle_outflow_m3_min": round(excess, 4),
            "estimated_loss_kw": round(loss_kw, 3),
            "kwh_per_day": round(kwh_day, 1),
            "inr_per_month": round(kwh_day * 30 * tariff),
            "co2_kg_per_month": round(kwh_day * 30 * GRID_CO2_KG_PER_KWH, 1),
        }
        alert["evidence"] += (f" Estimated loss {kwh_day:.0f} kWh/day ≈ "
                              f"₹{kwh_day * 30 * tariff:,.0f}/month at ₹{tariff:g}/kWh.")
    return alerts


def run_simulation(scenario="normal", intervention=False, tariff=7.0, *, variation_seed=None, fault_start=0,
                   cut_in_bar=None, cut_out_bar=None, secondary_fault=None, demand_multiplier=None):
    if scenario not in SCENARIOS:
        raise ValueError("Choose normal, leak, unloaded, filter or worn.")
    if not isinstance(fault_start, int) or not 0 <= fault_start <= PLANT.duration_seconds:
        raise ValueError("fault_start must be a second within the simulation.")
    if secondary_fault not in (None, "filter", "worn"):
        raise ValueError("secondary_fault must be None, 'filter' or 'worn'.")
    if demand_multiplier is not None and (isinstance(demand_multiplier, bool) or not 0.1 <= demand_multiplier <= 3.0):
        raise ValueError("demand_multiplier must be between 0.1 and 3.0.")
    for name, override in (("cut_in_bar", cut_in_bar), ("cut_out_bar", cut_out_bar)):
        if override is not None and (isinstance(override, bool) or not isinstance(override, (int, float))
                                     or not 5.5 <= override <= 7.5):
            raise ValueError(f"{name} must be a pressure between 5.5 and 7.5 bar(g).")
    if cut_in_bar is not None and cut_out_bar is not None and not cut_in_bar < cut_out_bar:
        raise ValueError("cut_in_bar must be below cut_out_bar.")
    configuration = SCENARIOS[scenario]
    leak_rate = configuration["leak_m3_min"]
    stop_delay = configuration["stop_delay"]
    if intervention:
        if scenario == "leak":
            leak_rate = SCENARIOS["normal"]["leak_m3_min"]
        elif scenario == "unloaded":
            stop_delay = SCENARIOS["normal"]["stop_delay"]

    # The original dashboard scenarios retain their exact settings. Training
    # variants change a whole run's physical parameters and sensor readings.
    rng = Random(variation_seed) if variation_seed is not None else None
    healthy_leak = rng.uniform(0.008, 0.025) if rng else 0.015
    healthy_delay = rng.randint(15, 30) if rng else 20
    if rng:
        leak_rate = rng.uniform(0.12, 0.30) if scenario == "leak" and not intervention else healthy_leak
        stop_delay = rng.randint(90, 240) if scenario == "unloaded" and not intervention else healthy_delay
    volume = rng.uniform(0.47, 0.53) if rng else PLANT.receiver_m3
    flow_scale = rng.uniform(0.96, 1.04) if rng else 1.0
    power_scale = rng.uniform(0.96, 1.04) if rng else 1.0
    demand_scale = rng.uniform(0.80, 1.15) if rng else 1.0
    block_seconds = rng.choice((240, 300, 360)) if rng else 300
    active_seconds = int(block_seconds * rng.uniform(0.55, 0.80)) if rng else 210
    cut_in = rng.uniform(6.35, 6.50) if rng else PLANT.cut_in_bar
    cut_out = rng.uniform(7.10, 7.25) if rng else PLANT.cut_out_bar
    if cut_in_bar is not None:
        cut_in = float(cut_in_bar)
    if cut_out_bar is not None:
        cut_out = float(cut_out_bar)
    pressure_bias = rng.uniform(-0.02, 0.02) if rng else 0.0

    p = rng.uniform(6.8, 7.2) if rng else 7.1
    initial_pressure = p
    previous_measurement = p + pressure_bias
    temperature = 35.0
    state = "Off"
    idle_seconds = 0
    energy = 0.0
    accepted = 0
    failed_cycles = 0
    partial_cycle = 0
    cycle_ok = True
    air_in = air_out = 0.0
    rows = []
    for t in range(PLANT.duration_seconds):
        if rng:
            active = t % block_seconds < active_seconds
            demand = 0.45 * demand_scale * (1 + 0.10 * sin(t / 37)) if active else 0.0
        else:
            demand, active = demand_at(t)
        if demand_multiplier is not None:
            demand *= demand_multiplier
        is_fault_active = scenario != "normal" and not intervention and t >= fault_start
        current_leak = leak_rate if is_fault_active else healthy_leak
        current_delay = stop_delay if is_fault_active else healthy_delay
        # Stage 3 fault physics: filter clog progressively restricts inlet
        # flow; wear degrades flow and raises power at all times. Stage 4
        # stress mode can layer a secondary fault on top of the primary one.
        clog_factor, wear_flow, wear_power = 1.0, 1.0, 1.0
        active_faults = set()
        if is_fault_active and scenario in ("filter", "worn"):
            active_faults.add(scenario)
        if secondary_fault and t >= fault_start:
            active_faults.add(secondary_fault)
        if "filter" in active_faults:
            clog_factor = max(0.75, 1.0 - 0.25 * min(1.0, (t - fault_start) / 300))
        if "worn" in active_faults:
            wear_flow, wear_power = 0.88, 1.06
        if p <= cut_in:
            state, idle_seconds = "Loaded", 0
        elif state == "Loaded" and p >= cut_out:
            state, idle_seconds = "Unloaded", 0
        if state == "Unloaded":
            if idle_seconds >= current_delay:
                state = "Off"
            else:
                idle_seconds += 1

        inlet, power = compressor_map(p, state)
        nominal_inlet = inlet
        inlet *= flow_scale * clog_factor * wear_flow
        power *= power_scale * wear_power
        # Isothermal receiver balance, dt = 1 second. All flows share reference conditions.
        leak = current_leak * max(p, 0) / 7.0
        outlet = demand + leak
        previous_p = p
        p += PLANT.reference_bar / volume * (inlet - outlet) / 60
        measured_p = p + pressure_bias + (rng.gauss(0, 0.004) if rng else 0.0)
        measured_power = max(0, power + (rng.gauss(0, 0.025) if rng else 0.0))
        estimated_outlet = nominal_inlet - PLANT.receiver_m3 / PLANT.reference_bar * (measured_p - previous_measurement) * 60
        previous_measurement = measured_p
        air_in += inlet / 60
        air_out += outlet / 60
        energy += power / 3600
        target_temperature = 65 if state == "Loaded" else (45 if state == "Unloaded" else 28)
        temperature += (target_temperature - temperature) / 300

        if active:
            partial_cycle += 1
            cycle_ok = cycle_ok and min(previous_p, p) >= PLANT.minimum_bar
            if partial_cycle == PLANT.cycle_seconds:
                accepted += int(cycle_ok)
                failed_cycles += int(not cycle_ok)
                partial_cycle, cycle_ok = 0, True
        else:
            partial_cycle, cycle_ok = 0, True

        rows.append({
            "second": t + 1, "pressure_bar": round(measured_p, 6), "power_kw": round(measured_power, 6),
            "temperature_c": round(temperature, 3), "state": state,
            "production_active": active, "accepted_units": accepted,
            "pressure_failed_cycles": failed_cycles, "energy_kwh": round(energy, 8),
            "estimated_outflow_m3_min": round(max(estimated_outlet, 0), 6),
        })

    compliance = 100 * sum(PLANT.minimum_bar <= r["pressure_bar"] <= PLANT.maximum_bar for r in rows) / len(rows)
    summary = {
        "energy_kwh": round(energy, 6), "cost_inr": round(energy * tariff, 2),
        "accepted_units": accepted, "sec_kwh_per_unit": round(energy / accepted, 6) if accepted else None,
        "pressure_compliance_pct": round(compliance, 2), "pressure_failed_cycles": failed_cycles,
        "unloaded_seconds": sum(r["state"] == "Unloaded" for r in rows),
        "peak_power_kw": max(r["power_kw"] for r in rows), "minimum_pressure_bar": min(r["pressure_bar"] for r in rows),
        "duration_seconds": PLANT.duration_seconds,
        "final_pressure_bar": round(p, 6),
    }
    # Exposes a physical consistency check, not a fitted savings percentage.
    residual = air_in - air_out - volume / PLANT.reference_bar * (p - initial_pressure)
    return {
        "scenario": scenario, "label": configuration["label"], "intervention": intervention,
        "tariff_inr_kwh": tariff, "summary": summary, "readings": rows, "alerts": detect_alerts(rows, tariff),
        "assumptions": asdict(PLANT), "balance_residual_reference_m3": residual,
        "data_mode": "simulation", "detection_method": "engineering rules",
        "variation_seed": variation_seed,
        "fault_interval": {"start_second": fault_start, "end_second": PLANT.duration_seconds, "type": scenario}
        if scenario != "normal" and not intervention and fault_start < PLANT.duration_seconds else None,
    }


def compare_runs(scenario, tariff=7.0):
    baseline = run_simulation(scenario, False, tariff)
    after = run_simulation(scenario, True, tariff)
    b, a = baseline["summary"], after["summary"]
    saving = b["energy_kwh"] - a["energy_kwh"]
    guardrails = (a["accepted_units"] >= b["accepted_units"] and a["pressure_failed_cycles"] <= b["pressure_failed_cycles"]
                  and a["minimum_pressure_bar"] >= PLANT.minimum_bar)
    return {
        "baseline": b, "after": a,
        "savings_kwh": round(saving, 6),
        "energy_reduction_pct": round(100 * saving / b["energy_kwh"], 2) if b["energy_kwh"] else None,
        "savings_inr": round(saving * tariff, 2),
        "sec_reduction_pct": round(100 * (b["sec_kwh_per_unit"] - a["sec_kwh_per_unit"]) / b["sec_kwh_per_unit"], 2)
        if b["sec_kwh_per_unit"] and a["sec_kwh_per_unit"] is not None else None,
        "simulation_guardrails_pass": guardrails,
        "action": {"leak": "Assume inspection and repair reduce leakage to the normal scenario level.",
                   "unloaded": "Reduce the assumed unloaded stop delay from 240 seconds to 20 seconds.",
                   "filter": "Replace the blocked intake filter and restore full inlet flow.",
                   "worn": "Overhaul the compressor and restore the original performance map.",
                   "normal": "No intervention: repeat the same normal operation."}[scenario],
        "scope": "Two matched 30-minute simulated runs; identical equipment, starting pressure and production demand.",
        "limitation": "Accepted units use a pressure-only proxy. Final receiver pressures may differ; stored-air changes are not normalized. Product quality, wear, start limits and real factory savings require validation.",
    }


if __name__ == "__main__":
    import json
    for name in SCENARIOS:
        result = run_simulation(name)
        print(name, json.dumps(result["summary"], indent=2), [a["key"] for a in result["alerts"]])
