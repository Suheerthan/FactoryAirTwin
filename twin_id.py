"""Self-calibrating twin: identify plant parameters from pressure + power only.

The engineering idea (ISO 11011-style load/unload leak test, generalised):
while production is idle, the only air leaving the network is leakage, so
the compressor's load/unload cycling is driven by leaks alone.

* Time-ratio method   leak / FAD = T_loaded / (T_loaded + T_not_loaded)
* Slope-ratio method  while loaded:     dp/dt = Pref/V * (FAD - leak) / 60
                      while not loaded: dp/dt = -Pref/V * leak / 60
                      => leak / FAD = -s_off / (s_load - s_off)
                      => V = Pref * FAD / (60 * (s_load - s_off))

Two independent estimators of the same leak fraction give a built-in
consistency check. Only the ratio FAD/V is observable from these two signals,
so the compressor's nameplate delivery (FAD) is the single required input;
the effective storage volume (receiver + pipework) is then *estimated*, not
assumed. Specific power and the loaded power-vs-pressure curve come straight
from the power signal.

Nothing in this module reads simulator ground truth. It works on any 1 Hz
stream: the multi-day twin, the 30-minute scenarios, or live edge data.
"""

from math import isfinite

from simulator import PLANT, compressor_map

LOADED_KW_THRESHOLD = (PLANT.unloaded_kw + PLANT.loaded_kw) / 2.0  # same split as edge_bridge
OFF_KW_THRESHOLD = 0.5
MIN_CYCLES = 3
MIN_IDLE_SECONDS = 60
EDGE_TRIM_SECONDS = 2  # skip valve/sensor transients at every state change


def classify_power(power_kw, loaded_threshold=LOADED_KW_THRESHOLD):
    """1 = loaded, 0 = not delivering air (unloaded or off)."""
    return [1 if kw > loaded_threshold else 0 for kw in power_kw]


def _runs(flags):
    """Contiguous runs as (value, start_index, end_index_exclusive)."""
    runs, start = [], 0
    for i in range(1, len(flags) + 1):
        if i == len(flags) or flags[i] != flags[start]:
            runs.append((flags[start], start, i))
            start = i
    return runs


def _slope(values, start, end, min_samples=5):
    """Least-squares slope (per sample) of values[start:end]; None if too short."""
    n = end - start
    if n < min_samples:
        return None
    mean_x = (n - 1) / 2.0
    mean_y = sum(values[start:end]) / n
    num = den = 0.0
    for k in range(n):
        dx = k - mean_x
        num += dx * (values[start + k] - mean_y)
        den += dx * dx
    return num / den if den else None


def fad_at(pressure_bar, fad_nominal=PLANT.rated_flow_m3_min):
    """Nameplate delivery corrected to pressure with the same map shape the twin uses."""
    return fad_nominal * compressor_map(pressure_bar, "Loaded")[0] / PLANT.rated_flow_m3_min


def idle_leak_test(pressure, power, fad_nominal=PLANT.rated_flow_m3_min,
                   volume_hint_m3=None, reference_bar=PLANT.reference_bar,
                   dt_s=1.0, loaded_threshold=LOADED_KW_THRESHOLD):
    """Run the load/unload leak test on an idle (no production) window.

    Returns a dict with both leak-fraction estimates, leak flow, effective
    storage volume, specific power and a confidence grade. When the
    compressor never reloads inside the window (very small leak), falls back
    to pure decay with ``volume_hint_m3`` and says so.
    """
    if len(pressure) != len(power) or len(pressure) * dt_s < MIN_IDLE_SECONDS:
        return {"ok": False, "reason": f"Need at least {MIN_IDLE_SECONDS} s of paired idle pressure and power samples."}
    # Coarse logs (e.g. 0.1 Hz) keep fewer samples per run, so trim one sample
    # and accept 3-sample runs; slopes are converted to bar/s with dt_s.
    trim = EDGE_TRIM_SECONDS if dt_s <= 1 else 1
    min_run = 5 if dt_s <= 1 else 3
    flags = classify_power(power, loaded_threshold)
    runs = _runs(flags)
    loaded_starts = [r for r in runs if r[0] == 1]
    mean_p = sum(pressure) / len(pressure)
    fad_p = fad_at(mean_p, fad_nominal)

    slopes_load, weights_load, slopes_off, weights_off = [], [], [], []
    loaded_kw = []
    for value, start, end in runs:
        a, b = start + trim, end - trim
        s = _slope(pressure, a, b, min_run)
        if s is None:
            continue
        s /= dt_s
        if value == 1:
            slopes_load.append(s); weights_load.append(b - a)
            loaded_kw.extend(power[a:b])
        else:
            slopes_off.append(s); weights_off.append(b - a)
    s_load = (sum(s * w for s, w in zip(slopes_load, weights_load)) / sum(weights_load)) if weights_load else None
    s_off = (sum(s * w for s, w in zip(slopes_off, weights_off)) / sum(weights_off)) if weights_off else None

    result = {"ok": True, "samples": len(pressure), "sample_interval_s": dt_s, "mean_pressure_bar": round(mean_p, 3),
              "fad_nameplate_m3_min": fad_nominal, "fad_at_pressure_m3_min": round(fad_p, 4),
              "slope_loaded_mbar_s": None if s_load is None else round(s_load * 1000, 3),
              "slope_idle_mbar_s": None if s_off is None else round(s_off * 1000, 3)}

    # Complete cycles: loaded-start to next loaded-start.
    cycles = []
    for first, second in zip(loaded_starts, loaded_starts[1:]):
        cycles.append((first[2] - first[1], second[1] - first[1]))
    result["complete_cycles"] = len(cycles)

    if len(cycles) >= MIN_CYCLES and s_load is not None and s_off is not None and s_load > s_off:
        t_load = sum(c[0] for c in cycles)
        t_total = sum(c[1] for c in cycles)
        frac_time = t_load / t_total
        frac_slope = -s_off / (s_load - s_off)
        frac = (frac_time + frac_slope) / 2.0
        volume = reference_bar * fad_p / (60.0 * (s_load - s_off))
        agreement = abs(frac_time - frac_slope) / max(frac, 1e-6)
        grade = "high" if agreement < 0.10 and len(cycles) >= 8 else ("medium" if agreement < 0.25 else "low")
        sp = (sum(loaded_kw) / len(loaded_kw)) / fad_p if loaded_kw else None
        result.update({
            "method": "load/unload cycling (time-ratio + slope-ratio)",
            "leak_fraction_time": round(frac_time, 4),
            "leak_fraction_slope": round(frac_slope, 4),
            "leak_fraction": round(frac, 4),
            "leak_pct_of_capacity": round(frac * 100, 1),
            "leak_m3_min": round(frac * fad_p, 4),
            "effective_volume_m3": round(volume, 3),
            "volume_source": "estimated from slopes",
            "specific_power_kw_per_m3_min": None if sp is None else round(sp, 3),
            "estimator_disagreement_pct": round(agreement * 100, 1),
            "confidence": grade,
        })
        return result

    # Fallback: compressor never cycled -> decay only, volume must be assumed.
    if s_off is None or not isfinite(s_off):
        return {**result, "ok": False, "reason": "No usable idle decay segment in this window."}
    volume = volume_hint_m3 if volume_hint_m3 else PLANT.receiver_m3
    leak = max(0.0, -s_off * volume * 60.0 / reference_bar)
    result.update({
        "method": "passive decay only (compressor did not reload)",
        "leak_fraction": round(leak / fad_p, 4), "leak_pct_of_capacity": round(100 * leak / fad_p, 1),
        "leak_m3_min": round(leak, 4), "effective_volume_m3": round(volume, 3),
        "volume_source": "assumed (site configuration) - not identifiable without reload cycles",
        "specific_power_kw_per_m3_min": None, "estimator_disagreement_pct": None, "confidence": "low",
    })
    return result


def loaded_power_curve(pressure, power, nameplate_kw=PLANT.loaded_kw, loaded_threshold=LOADED_KW_THRESHOLD):
    """Fit loaded kW = a + b * (p - 7) and compare with the nameplate map.

    A machine that needs noticeably more power than its own map for the same
    pressure is deteriorating (worn elements, failing bearings, high
    separator dp). Returns None when there are too few loaded samples.
    """
    flags = classify_power(power, loaded_threshold)
    xs, ys = [], []
    for (value, start, end) in _runs(flags):
        if value != 1:
            continue
        for k in range(start + EDGE_TRIM_SECONDS, end - EDGE_TRIM_SECONDS):
            xs.append(pressure[k] - 7.0); ys.append(power[k])
    n = len(xs)
    if n < 30:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 1e-9 else 0.0
    a = my - b * mx
    expected = nameplate_kw  # map value at 7 bar
    ratio = a / expected
    return {"kw_at_7bar": round(a, 3), "kw_per_bar": round(b, 3), "nameplate_kw_at_7bar": expected,
            "power_ratio_vs_nameplate": round(ratio, 3), "loaded_samples": n,
            "verdict": ("deterioration suspected" if ratio > 1.05 else "within nameplate band")}


def unloaded_overhead(power):
    """Share of electricity spent while the compressor delivers no air."""
    total = sum(power)
    if total <= 0:
        return None
    unloaded = sum(kw for kw in power if OFF_KW_THRESHOLD < kw <= LOADED_KW_THRESHOLD)
    return {"unloaded_energy_share_pct": round(100 * unloaded / total, 1),
            "unloaded_kwh": round(unloaded / 3600.0, 3)}


def identify_stream(pressure, power, production=None, fad_nominal=PLANT.rated_flow_m3_min,
                    volume_hint_m3=None):
    """Identify what this stream can support. Production flags, when present,
    restrict the leak test to idle seconds; otherwise the longest window with
    no loaded run longer than the receiver recharge time is used."""
    idle_p, idle_kw = [], []
    if production is not None:
        best, current = [], []
        for p, kw, active in zip(pressure, power, production):
            if active:
                if len(current) > len(best):
                    best = current
                current = []
            else:
                current.append((p, kw))
        if len(current) > len(best):
            best = current
        idle_p = [x[0] for x in best]
        idle_kw = [x[1] for x in best]
    leak = idle_leak_test(idle_p, idle_kw, fad_nominal, volume_hint_m3) if idle_p else {
        "ok": False, "reason": "No production-idle window in this stream."}
    return {
        "leak_test": leak,
        "power_curve": loaded_power_curve(pressure, power),
        "unloaded": unloaded_overhead(power),
        "identifiable": {
            "leak_flow": bool(leak.get("ok")),
            "storage_volume": leak.get("volume_source") == "estimated from slopes",
            "specific_power": bool(leak.get("specific_power_kw_per_m3_min")),
            "power_curve": True,
        },
        "inputs_used": ["header pressure (1 Hz)", "motor power (1 Hz)", f"nameplate FAD {fad_nominal} m³/min"]
                       + (["production on/off flag"] if production is not None else []),
    }
