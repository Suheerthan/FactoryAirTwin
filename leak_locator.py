"""Leak Hunter: locate leakage by zone with a guided valve-isolation test.

The twin can say *how much* air leaks; an operator still needs to know
*where*. Most small plants already have a ball valve at the start of every
branch. With the compressor stopped at cut-out pressure and production idle,
the header pressure decays only through whatever is open:

    leak_open = -dp/dt * V_open * 60 / Pref         (m³/min at reference)

Opening branches one at a time and differencing successive decay tests
allocates the total leak to zones - no new sensors, about 15 minutes during
a lunch break. Each test is simulated second by second with sensor noise;
the estimate uses only the measured pressure and the known pipe volumes.

The hidden leak per zone is drawn from a seed, so "New plant" really changes
the answer; ``reveal=True`` exposes the hidden values for grading only.
"""

from random import Random

from simulator import GRID_CO2_KG_PER_KWH, PLANT

TEST_SECONDS = 120
REPAIR_EFFICACY = 0.92  # a repair rarely removes every last hiss in a zone
START_BAR = PLANT.cut_out_bar
NOISE_BAR = 0.004

ZONES = [
    {"id": "header", "name": "Compressor room & main header", "pipe_m3": 0.0, "x": 70, "y": 150},
    {"id": "bay1", "name": "Bay 1 - CNC machining", "pipe_m3": 0.035, "x": 250, "y": 60},
    {"id": "bay2", "name": "Bay 2 - Assembly line", "pipe_m3": 0.045, "x": 420, "y": 60},
    {"id": "paint", "name": "Paint booth", "pipe_m3": 0.025, "x": 420, "y": 240},
    {"id": "pack", "name": "Packing & dispatch", "pipe_m3": 0.030, "x": 250, "y": 240},
]
BRANCHES = [z["id"] for z in ZONES if z["id"] != "header"]
HEADER_PIPE_M3 = 0.04


def hidden_leaks(seed):
    """Seeded hidden leak per zone (m³/min at 7 bar); one zone usually dominates."""
    rng = Random(seed)
    total = rng.uniform(0.14, 0.26)
    weights = {z["id"]: rng.uniform(0.2, 1.0) for z in ZONES}
    weights["header"] *= 0.35
    dominant = rng.choice(BRANCHES)
    weights[dominant] += rng.uniform(1.5, 3.0)
    s = sum(weights.values())
    return {zid: round(total * w / s, 4) for zid, w in weights.items()}


def plant_layout(seed=11):
    """Public description (no hidden values)."""
    return {"seed": seed, "zones": [{k: z[k] for k in ("id", "name", "pipe_m3", "x", "y")} for z in ZONES],
            "header_storage_m3": round(PLANT.receiver_m3 + HEADER_PIPE_M3, 3),
            "procedure": [
                "Stop production (lunch or shift change) and let the compressor reach cut-out pressure.",
                "Switch the compressor off so it cannot reload during the test.",
                "Close every branch valve: the first test measures the compressor room and main header only.",
                "Open one branch at a time and repeat a 2-minute decay test after each opening.",
                "The twin differences successive tests to allocate leakage to each zone.",
            ]}


def _volume(open_branches):
    return PLANT.receiver_m3 + HEADER_PIPE_M3 + sum(z["pipe_m3"] for z in ZONES if z["id"] in open_branches)


def _plant_leaks(seed, repaired=()):
    leaks = hidden_leaks(seed)
    for zone in repaired:
        if zone in leaks:
            leaks[zone] = round(leaks[zone] * (1 - REPAIR_EFFICACY), 4)
    return leaks


def decay_test(seed, open_branches, test_index=0, repaired=()):
    """Simulate one 2-minute decay test and estimate outflow from pressure only."""
    open_branches = [b for b in open_branches if b in BRANCHES]
    leaks = _plant_leaks(seed, repaired)
    rng = Random(seed * 1000 + test_index * 17 + len(open_branches) + 7919 * len(repaired))
    volume = _volume(open_branches)
    leak7 = leaks["header"] + sum(leaks[b] for b in open_branches)
    p = START_BAR
    trace = []
    for second in range(TEST_SECONDS + 1):
        trace.append(round(p + rng.gauss(0, NOISE_BAR), 4))
        p -= PLANT.reference_bar / volume * leak7 * p / 7.0 / 60.0
    n = len(trace)
    mx = (n - 1) / 2.0
    my = sum(trace) / n
    sxx = sum((i - mx) ** 2 for i in range(n))
    slope = sum((i - mx) * (v - my) for i, v in enumerate(trace)) / sxx
    resid = sum((v - (my + slope * (i - mx))) ** 2 for i, v in enumerate(trace)) / (n - 2)
    slope_se = (resid / sxx) ** 0.5
    to_flow = volume * 60.0 / PLANT.reference_bar * 7.0 / my  # normalise to 7 bar
    return {
        "open_branches": open_branches, "volume_m3": round(volume, 4),
        "trace_bar": trace, "drop_bar": round(trace[0] - trace[-1], 3),
        "slope_mbar_s": round(slope * 1000, 3),
        "outflow_m3_min_at_7bar": round(max(0.0, -slope * to_flow), 4),
        "uncertainty_m3_min": round(2 * slope_se * to_flow, 4),
    }


def survey(seed=11, tariff=7.0, specific_power=PLANT.loaded_kw / PLANT.rated_flow_m3_min,
           order=None, reveal=False, repaired=()):
    """Run the full guided sequence and allocate leakage to zones."""
    order = [b for b in (order or BRANCHES) if b in BRANCHES]
    steps, opened = [], []
    steps.append(decay_test(seed, [], 0, repaired))
    for i, branch in enumerate(order, start=1):
        opened.append(branch)
        steps.append(decay_test(seed, list(opened), i, repaired))
    alloc = [{"id": "header", "est_m3_min": steps[0]["outflow_m3_min_at_7bar"],
              "uncertainty_m3_min": steps[0]["uncertainty_m3_min"]}]
    for i, branch in enumerate(order, start=1):
        diff = steps[i]["outflow_m3_min_at_7bar"] - steps[i - 1]["outflow_m3_min_at_7bar"]
        unc = (steps[i]["uncertainty_m3_min"] ** 2 + steps[i - 1]["uncertainty_m3_min"] ** 2) ** 0.5
        alloc.append({"id": branch, "est_m3_min": round(max(0.0, diff), 4), "uncertainty_m3_min": round(unc, 4)})
    total = sum(a["est_m3_min"] for a in alloc) or 1e-9
    names = {z["id"]: z["name"] for z in ZONES}
    leaks = _plant_leaks(seed, repaired)
    for a in alloc:
        kwh_day = a["est_m3_min"] * specific_power * 24
        a.update({"name": names[a["id"]], "share_pct": round(100 * a["est_m3_min"] / total, 1),
                  "kwh_per_day": round(kwh_day, 1), "inr_per_month": round(kwh_day * 30 * tariff),
                  "co2_kg_month": round(kwh_day * 30 * GRID_CO2_KG_PER_KWH)})
        if reveal:
            a["validation_true_m3_min"] = leaks[a["id"]]
    ranked = sorted(alloc, key=lambda a: a["est_m3_min"], reverse=True)
    worst = ranked[0]
    return {
        "seed": seed, "steps": steps, "zones": alloc, "ranked": [a["id"] for a in ranked],
        "total_m3_min": round(total, 4), "total_inr_per_month": sum(a["inr_per_month"] for a in alloc),
        "recommendation": (f"Start the ultrasonic / soap-water walk in {worst['name']}: it carries "
                           f"{worst['share_pct']:.0f}% of the measured leakage "
                           f"(≈ ₹{worst['inr_per_month']:,}/month)."),
        "duration_minutes": round(len(steps) * (TEST_SECONDS + 60) / 60),
        "revealed": reveal, "data_mode": "simulation",
        "method": "Sequential branch opening; each 2-minute decay regressed for slope; leak = -slope × V × 60 / Pref.",
    }


def verify_zone_repair(seed, zone, tariff=7.0, specific_power=PLANT.loaded_kw / PLANT.rated_flow_m3_min):
    """Repeat the valve survey after the zone is repaired and measure the change.

    The verified saving is the drop in *total measured* leakage between the
    two surveys, so noise in the zone split cannot inflate it.
    """
    if zone not in [z["id"] for z in ZONES]:
        raise ValueError("Unknown zone.")
    before = survey(seed, tariff, specific_power)
    after = survey(seed, tariff, specific_power, repaired=(zone,))
    saved = max(0.0, before["total_m3_min"] - after["total_m3_min"])
    kwh_day = saved * specific_power * 24
    zone_before = next(z for z in before["zones"] if z["id"] == zone)
    zone_after = next(z for z in after["zones"] if z["id"] == zone)
    return {"zone": zone, "before_total_m3_min": before["total_m3_min"], "after_total_m3_min": after["total_m3_min"],
            "zone_before_m3_min": zone_before["est_m3_min"], "zone_after_m3_min": zone_after["est_m3_min"],
            "saved_m3_min": round(saved, 4), "saved_kwh_per_day": round(kwh_day, 2),
            "saved_inr_per_month": round(kwh_day * 30 * tariff),
            "saved_co2_kg_per_month": round(kwh_day * 30 * GRID_CO2_KG_PER_KWH, 1)}
