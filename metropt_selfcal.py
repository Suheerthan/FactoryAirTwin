r"""Self-calibration on REAL compressor data: UCI MetroPT-3.

Runs the twin's load/unload leak test (twin_id.idle_leak_test) on the nights
when the metro train is parked and its compressor only replaces leakage,
using the real TP3 pressure and Motor_current signals at ~0.1 Hz.

What it can and cannot measure honestly:

* The compressor's delivery (FAD) is not published, so leakage is reported
  as a FRACTION OF COMPRESSOR CAPACITY - the time-ratio and slope-ratio
  estimators need no nameplate at all. No m³/min, no ₹.
* Two independent estimators on real data: if the physics holds, they agree.
* The company documented four air-leak periods. Four depot nights fall
  inside them (18 Apr, 30 May, 6 Jun, 7 Jun 2020). The test asks whether the
  nightly leak fraction is higher on those nights than on normal nights.

Run (after downloading the UCI ZIP and extracting the CSV):
    .\.venv\Scripts\python.exe metropt_selfcal.py --csv "data_external/metropt3/MetroPT3(AirCompressor).csv"

Output: reports/metropt3_selfcal.json (git-ignored data, committed report).
"""

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from csv_import import otsu_threshold
from twin_id import idle_leak_test

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "reports" / "metropt3_selfcal.json"
NIGHT_START_H, NIGHT_END_H = 1, 5          # 01:00-05:00 local: metro service stopped
LEAK_EVENTS = [("2020-04-18 00:00", "2020-04-18 23:59"), ("2020-05-29 23:30", "2020-05-30 06:00"),
               ("2020-06-05 10:00", "2020-06-07 14:30"), ("2020-07-15 14:30", "2020-07-15 19:00")]


def _ts(text):
    text = text.strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(text)


EVENTS = [(_ts(a), _ts(b)) for a, b in LEAK_EVENTS]


def night_key(stamp):
    """Night label = the calendar date of the 01:00-05:00 window."""
    return stamp.date().isoformat() if NIGHT_START_H <= stamp.hour < NIGHT_END_H else None


def in_documented_leak(night):
    start = datetime.fromisoformat(night) + timedelta(hours=NIGHT_START_H)
    end = datetime.fromisoformat(night) + timedelta(hours=NIGHT_END_H)
    return any(start < e1 and end > e0 for e0, e1 in EVENTS)


def load_nights(path, max_rows=0):
    nights = defaultdict(lambda: {"t": [], "p": [], "a": []})
    rows = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        if not {"timestamp", "TP3", "Motor_current"} <= set(fields):
            raise SystemExit("Expected the 2023 UCI MetroPT-3 CSV with timestamp, TP3 and Motor_current columns.")
        for record in reader:
            rows += 1
            if max_rows and rows > max_rows:
                break
            try:
                stamp = _ts(record["timestamp"])
                key = night_key(stamp)
                if key is None:
                    continue
                nights[key]["t"].append(stamp.timestamp())
                nights[key]["p"].append(float(record["TP3"]))
                nights[key]["a"].append(float(record["Motor_current"]))
            except (KeyError, ValueError):
                continue
    return nights, rows


def longest_regular_segment(times, max_gap):
    best, start = (0, 0), 0
    for i in range(1, len(times) + 1):
        if i == len(times) or times[i] - times[i - 1] > max_gap:
            if i - start > best[1] - best[0]:
                best = (start, i)
            start = i
    return best


def analyse(nights, threshold=None):
    all_amps = [a for n in nights.values() for a in n["a"]]
    threshold = threshold or otsu_threshold(all_amps)
    results = []
    for night in sorted(nights):
        n = nights[night]
        if len(n["t"]) < 60:
            continue
        diffs = sorted(b - a for a, b in zip(n["t"], n["t"][1:]))
        dt = diffs[len(diffs) // 2]
        a, b = longest_regular_segment(n["t"], max_gap=3 * dt)
        if (b - a) * dt < 2 * 3600:
            continue
        test = idle_leak_test(n["p"][a:b], n["a"][a:b], fad_nominal=1.0, dt_s=dt, loaded_threshold=threshold)
        row = {"night": night, "documented_leak": in_documented_leak(night), "samples": b - a,
               "sample_interval_s": dt, "ok": bool(test.get("ok"))}
        if test.get("ok") and test.get("method", "").startswith("load/unload"):
            row.update({"leak_fraction_time": test["leak_fraction_time"], "leak_fraction_slope": test["leak_fraction_slope"],
                        "leak_fraction": test["leak_fraction"], "disagreement_pct": test["estimator_disagreement_pct"],
                        "cycles": test["complete_cycles"], "confidence": test["confidence"]})
        else:
            row["ok"] = False
            row["reason"] = test.get("reason") or test.get("method")
        results.append(row)
    cycled = [r for r in results if r["ok"]]
    leak_nights = [r for r in cycled if r["documented_leak"]]
    normal = [r for r in cycled if not r["documented_leak"]]
    ranked = sorted(cycled, key=lambda r: r["leak_fraction"], reverse=True)
    for i, r in enumerate(ranked, start=1):
        r["rank_high_to_low"] = i
    med_normal = median(r["leak_fraction"] for r in normal) if normal else None
    summary = {
        "nights_with_data": len(results), "nights_identified": len(cycled),
        "loaded_current_threshold_a": round(threshold, 2) if threshold else None,
        "median_disagreement_pct": round(median(r["disagreement_pct"] for r in cycled), 1) if cycled else None,
        "nights_high_confidence": sum(r["confidence"] == "high" for r in cycled),
        "median_leak_fraction_normal_nights": None if med_normal is None else round(med_normal, 4),
        "documented_leak_nights": [{"night": r["night"], "leak_fraction": r["leak_fraction"],
                                    "ratio_to_normal_median": round(r["leak_fraction"] / med_normal, 2) if med_normal else None,
                                    "rank_high_to_low": r["rank_high_to_low"], "of": len(cycled)} for r in leak_nights],
    }
    if leak_nights and normal:
        better = sum(1 for ln in leak_nights for nn in normal if ln["leak_fraction"] > nn["leak_fraction"])
        summary["auc_leak_nights_vs_normal"] = round(better / (len(leak_nights) * len(normal)), 3)
    return results, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--rows", type=int, default=0)
    args = parser.parse_args()
    nights, rows = load_nights(args.csv, args.rows)
    results, summary = analyse(nights)
    report = {
        "project": "FactoryAir Twin", "purpose": "Self-calibration (load/unload leak test) on real MetroPT-3 data",
        "source_file": Path(args.csv).name, "rows_read": rows,
        "signals": {"pressure": "TP3 (bar)", "load_state": "Motor_current (A), split by Otsu threshold"},
        "night_window": f"{NIGHT_START_H:02d}:00-{NIGHT_END_H:02d}:00 local",
        "documented_leak_events": [f"{a} -> {b}" for a, b in LEAK_EVENTS],
        "summary": summary, "nights": results,
        "limits": ["Railway braking compressor parked in a depot, not an SME factory.",
                   "FAD unknown: leakage reported as a fraction of compressor capacity only.",
                   "Night consumption may include brake/door systems, not only leaks.",
                   "Only four documented-leak nights: a small sample; read as evidence, not proof."],
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Saved {REPORT}")


if __name__ == "__main__":
    main()
