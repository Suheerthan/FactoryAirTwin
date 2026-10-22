"""Two-week plant twin with a nightly automatic leak test.

The 30-minute scenarios show one shift. Owners think in weeks and bills, so
this module runs the SAME receiver physics and compressor map second by
second over 14 days of a realistic small-factory calendar:

* Mon-Fri two shifts 06:00-22:00, Saturday one shift 06:00-14:00, Sunday off
* tea breaks and lunch with production paused
* leakage that grows slowly every day, a new leak that appears mid-week
  (a failed hose coupling) and, optionally, a repair a few days later

Every night (23:00-05:00) the twin runs ``twin_id.idle_leak_test`` on the
pressure + power stream only - exactly what a site gateway would do - and
publishes a daily leak index. Hidden "true" plant values are kept for
validation and labelled as such; the analytics never read them.

All constants are illustrative assumptions, and the whole run is labelled
SIMULATED in the API and the UI.
"""

from array import array
from dataclasses import asdict, dataclass
from functools import lru_cache
from math import sin
from random import Random

from simulator import GRID_CO2_KG_PER_KWH, PLANT, compressor_map
from twin_id import LOADED_KW_THRESHOLD, OFF_KW_THRESHOLD, idle_leak_test, loaded_power_curve

DAY = 86400
HOUR = 3600
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
BREAKS = ((10 * HOUR, 10 * HOUR + 900), (13 * HOUR, 13 * HOUR + 2700), (18 * HOUR, 18 * HOUR + 900))
NIGHT_START, NIGHT_END = 23 * HOUR, 29 * HOUR  # 23:00 -> 05:00 next morning
REQUIRED_MIN_BAR = PLANT.minimum_bar


@dataclass(frozen=True)
class SiteTruth:
    """Hidden plant reality. Differs from the nameplate on purpose: the
    effective storage includes pipework and the machine is slightly tired."""
    receiver_m3: float = 0.62            # 0.50 m³ tank + ~0.12 m³ pipework
    fad_scale: float = 0.97              # delivers 3 % below nameplate
    power_scale: float = 1.02
    base_leak_m3_min: float = 0.065
    leak_growth_per_day: float = 0.012   # fractional growth of the background leak
    new_leak_m3_min: float = 0.13        # failed coupling
    new_leak_at_day: float = 3.6         # Thursday 14:24
    repair_at_day: float = 10.45         # following Thursday 10:48 (None = never)
    stop_delay_s: int = 20


def _production_mask(days):
    mask = bytearray(days * DAY)
    for d in range(days):
        weekday = d % 7
        if weekday == 6:
            continue
        start, end = 6 * HOUR, (14 * HOUR if weekday == 5 else 22 * HOUR)
        base = d * DAY
        mask[base + start:base + end] = b"\x01" * (end - start)
        for b0, b1 in BREAKS:
            if b0 < end:
                b1 = min(b1, end)
                mask[base + b0:base + b1] = bytes(b1 - b0)
    return mask


def _true_leak(truth, t):
    day = t / DAY
    leak = truth.base_leak_m3_min * (1 + truth.leak_growth_per_day * day)
    if day >= truth.new_leak_at_day and (truth.repair_at_day is None or day < truth.repair_at_day):
        leak += truth.new_leak_m3_min
    return leak


def simulate_plant(days=14, truth=SiteTruth(), seed=7):
    """Second-by-second simulation. Returns compact arrays (not dicts):
    measured pressure, measured power, production flag and hourly true leak."""
    rng = Random(seed)
    n = days * DAY
    pressure = array("f", bytes(4 * n))
    power = array("f", bytes(4 * n))
    production = _production_mask(days)
    true_leak_hourly = []
    demand_scale = [rng.uniform(0.85, 1.10) for _ in range(days)]
    gauss = rng.gauss
    ref = PLANT.reference_bar
    vol = truth.receiver_m3
    k = ref / vol / 60.0
    rated = PLANT.rated_flow_m3_min * truth.fad_scale
    loaded_kw = PLANT.loaded_kw * truth.power_scale
    unloaded_kw = PLANT.unloaded_kw * truth.power_scale
    cut_in, cut_out = PLANT.cut_in_bar, PLANT.cut_out_bar
    delay = truth.stop_delay_s
    p = 7.0
    state = 0  # 0 off, 1 loaded, 2 unloaded
    idle = 0
    leak = _true_leak(truth, 0)
    leak_acc = 0.0
    for t in range(n):
        if t % 60 == 0:
            leak = _true_leak(truth, t)
        if t % HOUR == 0 and t:
            true_leak_hourly.append(leak_acc / HOUR)
            leak_acc = 0.0
        leak_acc += leak
        if production[t]:
            demand = 0.45 * demand_scale[t // DAY] * (1 + 0.10 * sin(t / 37.0)) if t % 300 < 210 else 0.05
        else:
            demand = 0.0
        if p <= cut_in:
            state, idle = 1, 0
        elif state == 1 and p >= cut_out:
            state, idle = 2, 0
        if state == 2:
            if idle >= delay:
                state = 0
            else:
                idle += 1
        if state == 1:
            inlet = rated * (1 - 0.02 * (p - 7))
            kw = loaded_kw * (1 + 0.035 * (p - 7))
        elif state == 2:
            inlet, kw = 0.0, unloaded_kw
        else:
            inlet, kw = 0.0, 0.0
        p += k * (inlet - demand - leak * p / 7.0)
        pressure[t] = p + gauss(0, 0.004)
        power[t] = kw + gauss(0, 0.025) if kw else 0.0
    true_leak_hourly.append(leak_acc / HOUR)
    return {"pressure": pressure, "power": power, "production": production,
            "true_leak_hourly": true_leak_hourly, "days": days, "truth": truth}


def night_label(d):
    """'Thu 4 → Fri 5' - weekday plus day number, so two weeks never collide."""
    return f"{DAY_NAMES[d % 7]} {d + 1} → {DAY_NAMES[(d + 1) % 7]} {d + 2}"


def _night_slice(d):
    return d * DAY + NIGHT_START, d * DAY + NIGHT_END


@lru_cache(maxsize=4)
def run_longrun(days=14, repair=True, tariff=7.0, seed=7):
    truth = SiteTruth() if repair else SiteTruth(repair_at_day=None)
    sim = simulate_plant(days, truth, seed)
    pressure, power, production = sim["pressure"], sim["power"], sim["production"]

    # ---------- hourly series for charts ----------
    hourly = []
    for h in range(days * 24):
        a, b = h * HOUR, (h + 1) * HOUR
        pw = power[a:b]
        kwh = sum(pw) / 3600.0
        loaded = sum(1 for kw in pw if kw > LOADED_KW_THRESHOLD)
        unloaded_kwh = sum(kw for kw in pw if OFF_KW_THRESHOLD < kw <= LOADED_KW_THRESHOLD) / 3600.0
        hourly.append({
            "hour": h, "day": h // 24, "hod": h % 24,
            "kwh": round(kwh, 3), "unloaded_kwh": round(unloaded_kwh, 3),
            "mean_pressure_bar": round(sum(pressure[a:b]) / HOUR, 3),
            "min_pressure_bar": round(min(pressure[a:b]), 3),
            "loaded_fraction": round(loaded / HOUR, 3),
            "production_fraction": round(sum(production[a:b]) / HOUR, 3),
            "true_leak_m3_min": round(sim["true_leak_hourly"][h], 4),
        })

    # ---------- nightly leak tests (pressure + power only) ----------
    nights = []
    for d in range(days - 1):
        a, b = _night_slice(d)
        test = idle_leak_test(list(pressure[a:b]), list(power[a:b]))
        true_leak_7bar = sum(sim["true_leak_hourly"][a // HOUR:b // HOUR]) / ((b - a) / HOUR)
        # Leakage flows through fixed orifices, so it scales with pressure:
        # grade the estimate against the true leak at the test's mean pressure.
        true_leak = true_leak_7bar * test.get("mean_pressure_bar", 7.0) / 7.0
        nights.append({"night": d, "label": night_label(d), **test,
                       "validation_true_leak_m3_min": round(true_leak, 4)})

    # ---------- identified plant (median of confident nights) ----------
    good = [n for n in nights if n.get("ok") and n.get("volume_source") == "estimated from slopes"]
    vols = sorted(n["effective_volume_m3"] for n in good)
    sps = sorted(n["specific_power_kw_per_m3_min"] for n in good if n.get("specific_power_kw_per_m3_min"))
    median = lambda xs: xs[len(xs) // 2] if xs else None
    curve = loaded_power_curve(list(pressure[:3 * DAY]), list(power[:3 * DAY]))
    identified = {
        "effective_volume_m3": median(vols),
        "nameplate_receiver_m3": PLANT.receiver_m3,
        "specific_power_kw_per_m3_min": median(sps),
        "nameplate_specific_power": round(PLANT.loaded_kw / PLANT.rated_flow_m3_min, 3),
        "power_curve": curve,
        "nights_used": len(good),
    }

    # ---------- daily owner table ----------
    sp_used = identified["specific_power_kw_per_m3_min"] or PLANT.loaded_kw / PLANT.rated_flow_m3_min
    daily = []
    for d in range(days):
        rows = hourly[d * 24:(d + 1) * 24]
        kwh = sum(r["kwh"] for r in rows)
        night = next((n for n in nights if n["night"] == d), None)
        leak = night.get("leak_m3_min") if night and night.get("ok") else None
        leak_kwh_day = leak * sp_used * 24 if leak is not None else None
        daily.append({
            "day": d, "weekday": DAY_NAMES[d % 7], "kwh": round(kwh, 1), "cost_inr": round(kwh * tariff),
            "production_hours": round(sum(r["production_fraction"] for r in rows), 1),
            "unloaded_kwh": round(sum(r["unloaded_kwh"] for r in rows), 1),
            "leak_m3_min_tonight": leak,
            "leak_pct_tonight": night.get("leak_pct_of_capacity") if night and night.get("ok") else None,
            "leak_kwh_per_day": None if leak_kwh_day is None else round(leak_kwh_day, 1),
            "leak_inr_per_day": None if leak_kwh_day is None else round(leak_kwh_day * tariff),
            "test_confidence": night.get("confidence") if night else None,
        })

    # ---------- events: step changes in the nightly leak index ----------
    events = []
    series = [(n["night"], n["leak_m3_min"]) for n in nights if n.get("ok")]
    for i in range(3, len(series)):
        night, value = series[i]
        reference = sorted(v for _, v in series[i - 3:i])[1]
        delta = value - reference
        if abs(delta) >= max(0.03, 0.25 * reference):
            kind = "new_leak" if delta > 0 else "leak_reduced"
            inr_month = abs(delta) * sp_used * 24 * 30 * tariff
            events.append({
                "night": night, "kind": kind, "label": nights[night]["label"],
                "delta_m3_min": round(delta, 4), "inr_per_month": round(inr_month),
                "title": ("New leak detected overnight" if kind == "new_leak"
                          else "Leak reduction confirmed overnight"),
                "detail": (f"Nightly test leak rose from {reference:.3f} to {value:.3f} m³/min "
                           f"(≈ ₹{inr_month:,.0f}/month extra). Something changed since the last shift."
                           if kind == "new_leak" else
                           f"Nightly test leak fell from {reference:.3f} to {value:.3f} m³/min - "
                           f"the repair is worth ≈ ₹{inr_month:,.0f}/month, measured, not predicted."),
            })
    # One step change can trip the 3-night reference on consecutive nights;
    # keep only the first flag of each step.
    deduped = []
    for e in events:
        if not deduped or e["night"] - deduped[-1]["night"] > 2 or e["kind"] != deduped[-1]["kind"]:
            deduped.append(e)

    # ---------- validation against hidden truth (labelled) ----------
    errors = [abs(n["leak_m3_min"] - n["validation_true_leak_m3_min"]) / n["validation_true_leak_m3_min"]
              for n in nights if n.get("ok") and n["validation_true_leak_m3_min"] > 0]
    validation = {
        "leak_mape_pct": round(100 * sum(errors) / len(errors), 1) if errors else None,
        "true_effective_volume_m3": truth.receiver_m3,
        "volume_error_pct": (round(100 * abs(identified["effective_volume_m3"] - truth.receiver_m3)
                                   / truth.receiver_m3, 1) if identified["effective_volume_m3"] else None),
        "true_fad_scale": truth.fad_scale,
        "note": ("Hidden simulator values, shown only to grade the identification. The nightly test "
                 "assumes nameplate delivery, so a machine delivering 3 % below nameplate biases leak "
                 "and volume estimates by a similar amount - visible and expected."),
    }

    total_kwh = sum(d["kwh"] for d in daily)
    week_scale = 30.0 / days
    latest = next((d for d in reversed(daily) if d["leak_kwh_per_day"] is not None), None)
    peak_leak = max((d for d in daily if d["leak_kwh_per_day"] is not None), key=lambda d: d["leak_kwh_per_day"])
    unloaded_total = sum(d["unloaded_kwh"] for d in daily)
    mean_header = sum(r["mean_pressure_bar"] * r["production_fraction"] for r in hourly) / max(
        1e-6, sum(r["production_fraction"] for r in hourly))
    minimum_seen = min(r["min_pressure_bar"] for r in hourly if r["production_fraction"] > 0)
    summary = {
        "days": days, "total_kwh": round(total_kwh, 1), "total_cost_inr": round(total_kwh * tariff),
        "monthly_kwh": round(total_kwh * week_scale), "monthly_cost_inr": round(total_kwh * week_scale * tariff),
        "monthly_co2_kg": round(total_kwh * week_scale * GRID_CO2_KG_PER_KWH),
        "latest_leak_pct": latest["leak_pct_tonight"] if latest else None,
        "latest_leak_inr_month": round(latest["leak_kwh_per_day"] * 30 * tariff) if latest else None,
        "peak_leak_inr_month": round(peak_leak["leak_kwh_per_day"] * 30 * tariff),
        "unloaded_share_pct": round(100 * unloaded_total / total_kwh, 1) if total_kwh else None,
        "unloaded_inr_month": round(unloaded_total * week_scale * tariff),
        "mean_header_bar_in_production": round(mean_header, 2),
        "lowest_pressure_in_production_bar": round(minimum_seen, 2),
        "tariff_inr_kwh": tariff, "grid_factor": GRID_CO2_KG_PER_KWH,
    }
    return {
        "data_mode": "simulation", "label": "SIMULATED 14-day plant twin",
        "calendar": "Mon-Fri 06:00-22:00 (breaks 10:00, 13:00 lunch, 18:00) · Sat 06:00-14:00 · Sun off",
        "repair_included": repair, "summary": summary, "identified": identified, "validation": validation,
        "daily": daily, "nights": nights, "events": deduped, "hourly": hourly,
        "truth_parameters": asdict(truth),
        "assumptions": {"nameplate_fad_m3_min": PLANT.rated_flow_m3_min, "loaded_kw": PLANT.loaded_kw,
                        "unloaded_kw": PLANT.unloaded_kw, "cut_in_bar": PLANT.cut_in_bar,
                        "cut_out_bar": PLANT.cut_out_bar, "night_test_window": "23:00-05:00"},
    }


def simulate_idle_window(truth, seconds=6 * HOUR, seed=1):
    """A single production-free window for identification validation."""
    rng = Random(seed)
    gauss = rng.gauss
    k = PLANT.reference_bar / truth.receiver_m3 / 60.0
    rated = PLANT.rated_flow_m3_min * truth.fad_scale
    p, state, idle = rng.uniform(6.6, 7.1), 0, 0
    leak = truth.base_leak_m3_min
    pressure, power = [], []
    for _ in range(seconds):
        if p <= PLANT.cut_in_bar:
            state, idle = 1, 0
        elif state == 1 and p >= PLANT.cut_out_bar:
            state, idle = 2, 0
        if state == 2:
            if idle >= truth.stop_delay_s:
                state = 0
            else:
                idle += 1
        inlet = rated * (1 - 0.02 * (p - 7)) if state == 1 else 0.0
        kw = (PLANT.loaded_kw * truth.power_scale * (1 + 0.035 * (p - 7)) if state == 1
              else PLANT.unloaded_kw * truth.power_scale if state == 2 else 0.0)
        p += k * (inlet - leak * p / 7.0)
        pressure.append(p + gauss(0, 0.004))
        power.append(kw + gauss(0, 0.025) if kw else 0.0)
    return pressure, power


@lru_cache(maxsize=2)
def validate_identification(plants=30, seed=2026):
    """Grade the identification on many random plants it has never seen.

    Each plant draws a hidden storage volume, leak, delivery and power scale.
    The test only receives pressure + power + nameplate FAD.
    """
    rng = Random(seed)
    rows = []
    for i in range(plants):
        truth = SiteTruth(receiver_m3=round(rng.uniform(0.35, 1.5), 3),
                          fad_scale=round(rng.uniform(0.94, 1.03), 3),
                          power_scale=round(rng.uniform(0.97, 1.06), 3),
                          base_leak_m3_min=round(rng.uniform(0.03, 0.32), 4),
                          stop_delay_s=rng.choice((15, 20, 30, 60)))
        pressure, power = simulate_idle_window(truth, seed=seed + i)
        test = idle_leak_test(pressure, power)
        if not test.get("ok"):
            rows.append({"plant": i + 1, "ok": False, "reason": test.get("reason")})
            continue
        true_leak = truth.base_leak_m3_min * test["mean_pressure_bar"] / 7.0
        true_sp = (PLANT.loaded_kw * truth.power_scale) / (PLANT.rated_flow_m3_min * truth.fad_scale)
        est_sp = test.get("specific_power_kw_per_m3_min")
        rows.append({
            "plant": i + 1, "ok": True, "method": test["method"], "confidence": test["confidence"],
            "true_leak_m3_min": round(true_leak, 4), "est_leak_m3_min": test["leak_m3_min"],
            "leak_error_pct": round(100 * (test["leak_m3_min"] - true_leak) / true_leak, 1),
            "true_volume_m3": truth.receiver_m3, "est_volume_m3": test["effective_volume_m3"],
            "volume_error_pct": round(100 * (test["effective_volume_m3"] - truth.receiver_m3) / truth.receiver_m3, 1),
            "true_sp": round(true_sp, 3), "est_sp": est_sp,
            "sp_error_pct": None if not est_sp else round(100 * (est_sp - true_sp) / true_sp, 1),
            "true_fad_scale": truth.fad_scale,
        })
    ok = [r for r in rows if r["ok"]]
    cycled = [r for r in ok if r["method"].startswith("load/unload")]
    mae = lambda key, rs: round(sum(abs(r[key]) for r in rs) / len(rs), 1) if rs else None
    return {
        "plants": plants, "identified": len(ok), "cycling_plants": len(cycled),
        "leak_mean_abs_error_pct": mae("leak_error_pct", ok),
        "volume_mean_abs_error_pct": mae("volume_error_pct", cycled),
        "sp_mean_abs_error_pct": mae("sp_error_pct", [r for r in cycled if r["sp_error_pct"] is not None]),
        "within_10pct_leak": sum(1 for r in ok if abs(r["leak_error_pct"]) <= 10),
        "rows": rows,
        "protocol": ("Random hidden plants (storage 0.35-1.5 m³, leak 0.03-0.32 m³/min, delivery 94-103 % "
                     "of nameplate, power 97-106 %, stop delay 15-60 s). The test sees only 6 h of idle "
                     "pressure + power and the nameplate FAD. Error is dominated by the unknown delivery "
                     "shortfall - the reason the field version calibrates FAD once with a pump-up test."),
        "data_mode": "simulation",
    }


if __name__ == "__main__":
    import time
    t0 = time.time()
    result = run_longrun()
    print(f"14-day twin in {time.time() - t0:.1f}s")
    print(result["summary"])
    print(result["identified"])
    print(result["validation"])
    for e in result["events"]:
        print(e["label"], e["title"], e["inr_per_month"])
    for n in result["nights"]:
        print(n["label"], n.get("leak_m3_min"), n["validation_true_leak_m3_min"], n.get("effective_volume_m3"),
              n.get("confidence"), n.get("complete_cycles"))
    t0 = time.time()
    v = validate_identification()
    print(f"validation in {time.time() - t0:.1f}s", {k: v[k] for k in v if k != "rows"})
