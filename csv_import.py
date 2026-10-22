"""Bring-your-own-data: identify a real plant from a logger CSV.

Accepts what small factories can actually export from a pressure logger and
an energy meter:

* a pressure column (bar gauge)            pressure_bar / pressure / header_pressure / tp2
* power in kW **or** motor current in A    power_kw / power / kw   |  current_a / current / motor_current / amps
* optional timestamp                       timestamp / time / datetime / t  (ISO text or unix seconds)
* optional production flag                 production / production_active / prod  (1/0, true/false)

Idle windows for the leak test are chosen, in order of preference, from the
production flag, from the night (23:00-05:00) when timestamps exist, or the
whole file when neither exists (the user recorded an idle period). The
loaded/unloaded split is learned from the file's own power histogram
(Otsu's threshold), so it works for any compressor size.
"""

import csv
import io
from datetime import datetime, timezone
from statistics import median

from simulator import GRID_CO2_KG_PER_KWH
from twin_id import idle_leak_test, loaded_power_curve

ALIASES = {
    "time": ("timestamp", "time", "datetime", "date_time", "t"),
    "pressure": ("pressure_bar", "pressure", "header_pressure", "tp2", "p_bar"),
    "power": ("power_kw", "power", "kw", "active_power_kw"),
    "current": ("current_a", "current", "motor_current", "amps", "i_a"),
    "production": ("production", "production_active", "prod", "machine_on"),
}
MAX_ROWS = 400_000
DEFAULT_KW_PER_A = 3 ** 0.5 * 415 * 0.86 / 1000  # balanced 3-phase, 415 V, pf 0.86


def _pick(fields, key):
    lowered = {f.strip().lower(): f for f in fields}
    for alias in ALIASES[key]:
        if alias in lowered:
            return lowered[alias]
    return None


def _parse_time(text):
    text = text.strip()
    try:
        return float(text)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp {text!r}. Use ISO 'YYYY-MM-DD HH:MM:SS' or unix seconds.")


def _truthy(text):
    return str(text).strip().lower() in ("1", "true", "yes", "on", "y")


def otsu_threshold(values, bins=60):
    """Split nonzero motor power into unloaded vs loaded without knowing the machine."""
    top = max(values) if values else 0.0
    xs = [v for v in values if v > 0.05 * top]
    if len(xs) < 20:
        return None
    lo, hi = min(xs), max(xs)
    if hi - lo < 1e-6:
        return None
    width = (hi - lo) / bins
    hist = [0] * bins
    for v in xs:
        hist[min(bins - 1, int((v - lo) / width))] += 1
    total = len(xs)
    sum_all = sum((i + 0.5) * h for i, h in enumerate(hist))
    best, best_i, best_end, w0, sum0 = -1.0, 0, 0, 0, 0.0
    for i, h in enumerate(hist):
        w0 += h
        sum0 += (i + 0.5) * h
        if w0 == 0 or w0 == total:
            continue
        m0, m1 = sum0 / w0, (sum_all - sum0) / (total - w0)
        between = w0 * (total - w0) * (m0 - m1) ** 2
        if between > best * (1 + 1e-12):
            best, best_i, best_end = between, i, i
        elif abs(between - best) <= 1e-12 * best:
            best_end = i  # empty bins between clusters tie: keep the whole gap
    # Middle of the gap between the unloaded and loaded clusters.
    return lo + ((best_i + best_end) / 2 + 1) * width


def parse_csv(text, kw_per_amp=DEFAULT_KW_PER_A):
    reader = csv.DictReader(io.StringIO(text))
    fields = reader.fieldnames or []
    col_p, col_kw, col_a = _pick(fields, "pressure"), _pick(fields, "power"), _pick(fields, "current")
    col_t, col_prod = _pick(fields, "time"), _pick(fields, "production")
    if not col_p or not (col_kw or col_a):
        raise ValueError("The CSV needs a pressure column (e.g. pressure_bar) and power_kw or motor current "
                         f"(e.g. current_a). Found columns: {', '.join(fields) or 'none'}.")
    times, pressure, power, production, skipped = [], [], [], [], 0
    for i, row in enumerate(reader):
        if i >= MAX_ROWS:
            break
        try:
            p = float(row[col_p])
            kw = float(row[col_kw]) if col_kw else float(row[col_a]) * kw_per_amp
            t = _parse_time(row[col_t]) if col_t else float(i)
        except (TypeError, ValueError, KeyError):
            skipped += 1
            continue
        if not (0 <= p <= 16 and 0 <= kw <= 2000):
            skipped += 1
            continue
        times.append(t); pressure.append(p); power.append(kw)
        production.append(_truthy(row[col_prod]) if col_prod else None)
    if len(pressure) < 60:
        raise ValueError("Fewer than 60 usable rows after cleaning.")
    return {"times": times, "pressure": pressure, "power": power,
            "production": production if col_prod else None, "has_time": bool(col_t),
            "columns": {"time": col_t, "pressure": col_p, "power": col_kw, "current": col_a, "production": col_prod},
            "power_from_current": not col_kw, "skipped_rows": skipped}


def _idle_windows(data, dt):
    n = len(data["pressure"])
    min_len = max(int(1200 / dt), 60)  # 20 minutes
    windows = []
    if data["production"] is not None:
        start = None
        for i, active in enumerate(data["production"] + [True]):
            if not active and start is None:
                start = i
            elif active and start is not None:
                if i - start >= min_len:
                    windows.append((start, i, "production flag off"))
                start = None
        return windows
    if data["has_time"]:
        start = None
        for i, t in enumerate(data["times"] + [None]):
            night = t is not None and (datetime.fromtimestamp(t, timezone.utc).hour >= 23
                                       or datetime.fromtimestamp(t, timezone.utc).hour < 5)
            if night and start is None:
                start = i
            elif not night and start is not None:
                if i - start >= min_len:
                    windows.append((start, i, "night 23:00-05:00"))
                start = None
        return windows
    return [(0, n, "whole file (assumed idle)")]


def identify_csv(text, fad_nominal=0.9, nameplate_kw=7.5, tariff=7.0, kw_per_amp=DEFAULT_KW_PER_A):
    for name, value, lo, hi in (("fad_nominal", fad_nominal, 0.05, 200), ("nameplate_kw", nameplate_kw, 0.5, 2000),
                                ("tariff", tariff, 0, 100), ("kw_per_amp", kw_per_amp, 0.01, 50)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}.")
    data = parse_csv(text, kw_per_amp)
    times = data["times"]
    dt = median(b - a for a, b in zip(times, times[1:])) if data["has_time"] and len(times) > 2 else 1.0
    if dt <= 0:
        raise ValueError("Timestamps must increase.")
    threshold = otsu_threshold(data["power"])
    if threshold is None:
        raise ValueError("Could not find separate loaded and unloaded power levels in this file.")
    tests = []
    for a, b, source in _idle_windows(data, dt):
        result = idle_leak_test(data["pressure"][a:b], data["power"][a:b], fad_nominal,
                                dt_s=dt, loaded_threshold=threshold)
        label = (datetime.fromtimestamp(times[a], timezone.utc).strftime("%d %b %H:%M")
                 if data["has_time"] else f"rows {a}-{b}")
        tests.append({"window": label, "source": source, "rows": b - a, **result})
    good = [t for t in tests if t.get("ok")]
    cycled = [t for t in good if t.get("volume_source") == "estimated from slopes"]
    loaded_kw = [kw for kw in data["power"] if kw > threshold]
    sp = (median(loaded_kw) / fad_nominal) if loaded_kw else None
    leak = median(t["leak_m3_min"] for t in good) if good else None
    leak_kwh_day = leak * sp * 24 if leak is not None and sp else None
    curve = loaded_power_curve(data["pressure"], data["power"], nameplate_kw=nameplate_kw, loaded_threshold=threshold)
    total_kwh = sum(data["power"]) * dt / 3600.0
    unloaded_kwh = sum(kw for kw in data["power"] if 0.05 * nameplate_kw < kw <= threshold) * dt / 3600.0
    hours = len(data["power"]) * dt / 3600.0
    return {
        "rows_used": len(data["pressure"]), "rows_skipped": data["skipped_rows"], "columns": data["columns"],
        "power_from_current": data["power_from_current"], "sample_interval_s": round(dt, 3),
        "hours_covered": round(hours, 1), "loaded_threshold_kw": round(threshold, 2),
        "idle_windows": len(tests), "windows_identified": len(good), "windows_with_cycling": len(cycled),
        "tests": tests,
        "summary": {
            "leak_m3_min": None if leak is None else round(leak, 4),
            "leak_pct_of_capacity": None if leak is None else round(100 * leak / fad_nominal, 1),
            "effective_volume_m3": round(median(t["effective_volume_m3"] for t in cycled), 3) if cycled else None,
            "specific_power_kw_per_m3_min": None if sp is None else round(sp, 2),
            "leak_inr_per_month": None if leak_kwh_day is None else round(leak_kwh_day * 30 * tariff),
            "leak_co2_kg_per_month": None if leak_kwh_day is None else round(leak_kwh_day * 30 * GRID_CO2_KG_PER_KWH),
            "unloaded_share_pct": round(100 * unloaded_kwh / total_kwh, 1) if total_kwh else None,
            "kwh_per_day": round(total_kwh / hours * 24, 1) if hours else None,
            "power_ratio_vs_nameplate": None if not curve else curve["power_ratio_vs_nameplate"],
        },
        "inputs": {"fad_nominal_m3_min": fad_nominal, "nameplate_kw": nameplate_kw, "tariff_inr_kwh": tariff,
                   "kw_per_amp": round(kw_per_amp, 4)},
        "note": ("Leak flow uses the nameplate FAD you entered; a machine delivering below nameplate reads "
                 "slightly high. Idle windows shorter than 20 minutes are ignored."),
    }


def sample_csv(hours=36, every_s=5):
    """A downloadable example: 36 h from the two-week twin, 5-second rows, with timestamps."""
    from longrun import SiteTruth, simulate_plant
    sim = simulate_plant(days=2, truth=SiteTruth(new_leak_at_day=0.8, repair_at_day=None), seed=21)
    base = datetime(2026, 10, 5, tzinfo=timezone.utc).timestamp()
    out = io.StringIO()
    out.write("timestamp,pressure_bar,power_kw\n")
    for s in range(0, hours * 3600, every_s):
        stamp = datetime.fromtimestamp(base + s, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        out.write(f"{stamp},{sim['pressure'][s]:.3f},{sim['power'][s]:.2f}\n")
    return out.getvalue()
