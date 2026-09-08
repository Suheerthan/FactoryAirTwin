r"""Optional MetroPT-3 transfer demonstration (run locally).

Dataset: UCI MetroPT-3 (https://archive.ics.uci.edu/dataset/791/metropt+3+dataset)
1,516,948 readings at 0.1 Hz from a metro train's air-production compressor,
February-August 2020. Columns: TP2/TP3/Reservoirs (bar), Oil_temperature (C),
Motor_current (A), COMP/DV_eletric/Towers/MPG/LPS/Pressure_switch/Oil_level/
Caudal_impulses (digital signals).

The dataset is UNLABELLED, but the company documented four AIR-LEAK events:

    #1  2020-04-18 00:00  ->  2020-04-18 23:59   (High stress)
    #2  2020-05-29 23:30  ->  2020-05-30 06:00   (High stress)
    #3  2020-06-05 10:00  ->  2020-06-07 14:30   (High stress)
    #4  2020-07-15 14:30  ->  2020-07-15 19:00   (High stress)

FactoryAir Twin does NOT ship the data. Download the ZIP yourself, extract
MetroPT3(AirCompressor).csv next to this script, then run:

    .\.venv\Scripts\python.exe metropt3.py --csv "MetroPT3(AirCompressor).csv"

(Add --rows 200000 to test quickly on a subset; 0 = all rows.)

This is a TRANSFER DEMONSTRATION, not performance evidence:

* MetroPT-3 is a railway braking compressor, not an SME factory machine.
* Motor current is an assumed-linear kW proxy (0 A off / 4 A unloaded /
  7 A loaded / 9 A start per the dataset paper), NOT calibrated power.
* The receiver volume and performance map are FactoryAir Twin assumptions.
* Scores describe fit to FactoryAir Twin's synthetic normality; they are not
  fault probabilities. The honest question answered here: are anomalous
  windows concentrated inside the documented leak intervals?

Outputs: reports/metropt3_transfer.json and a console summary.
"""

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from ai_detector import WINDOW, STRIDE, load_local_bundle
from simulator import PLANT

ROOT = Path(__file__).resolve().parent
ASSUMED_LINE_KW_PER_A = 0.4   # assumed 400 V three-phase with pf ~0.85: A -> kW proxy.
ASSUMED_RECEIVER_M3 = 5.0     # metro auxiliary reservoir order of magnitude.

# Company-documented air-leak events (UCI dataset page, "Failure Information").
LEAK_EVENTS = [
    ("2020-04-18 00:00", "2020-04-18 23:59"),
    ("2020-05-29 23:30", "2020-05-30 06:00"),
    ("2020-06-05 10:00", "2020-06-07 14:30"),
    ("2020-07-15 14:30", "2020-07-15 19:00"),
]


def _epoch(text):
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    raise ValueError(f"Unparseable timestamp: {text!r}")


LEAK_INTERVALS = [(_epoch(a), _epoch(b)) for a, b in LEAK_EVENTS]


def _in_documented_leak(start_epoch, end_epoch):
    return int(any(start_epoch <= e1 and end_epoch >= e0 for e0, e1 in LEAK_INTERVALS))


def stream_windows(path, max_rows):
    """Stream the CSV and yield 4-feature windows without holding all rows.

    Supports BOTH formats: the current UCI release (TP2/Motor_current/...)
    and the older MetroPt3_*.txt release (Pressure/Temperature/Motor current/
    Leak). The air-balance feature uses only pressure DIFFERENCE, so absolute
    gauge/absolute offsets cancel out.
    """
    vectors, leak_flags, epochs = [], [], []
    count = 0
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames or []
        new_format = "TP2" in fields
        old_format = "Pressure" in fields and "Leak" in fields
        if not new_format and not old_format:
            raise ValueError("Unrecognised CSV. Expected MetroPT-3 columns "
                             "(TP2, Motor_current, ...) or the older format "
                             "(Pressure, Temperature, Motor current, Leak).")
        buffer_t, buffer_p = [], []
        leak_in_buffer = []
        for record in reader:
            count += 1
            if max_rows and count > max_rows:
                break
            try:
                if new_format:
                    t = _epoch(record["timestamp"])
                    pressure = float(record["TP2"])
                    leak_in = _in_documented_leak(t, t)
                else:
                    t = float(count)  # old format has no timestamps: sample index
                    pressure = float(record["Pressure"]) / 100.0  # kPa -> bar
                    leak_in = int(float(record["Leak"]) > 0)
            except (KeyError, TypeError, ValueError):
                continue
            buffer_t.append(t)
            buffer_p.append(pressure)
            leak_in_buffer.append(leak_in)
            if len(buffer_t) > WINDOW:
                buffer_t.pop(0)
                buffer_p.pop(0)
                leak_in_buffer.pop(0)
            if len(buffer_t) == WINDOW and (count - WINDOW) % STRIDE == 0:
                elapsed = buffer_t[-1] - buffer_t[0]
                if elapsed <= 0:
                    continue
                dp = buffer_p[-1] - buffer_p[0]
                outflow = -ASSUMED_RECEIVER_M3 / PLANT.reference_bar * dp * 60.0 / elapsed
                vectors.append([float(outflow), 0.0, 0.0, 0.0])
                leak_flags.append(int(any(leak_in_buffer)))
                epochs.append((buffer_t[0], buffer_t[-1]))
    return np.asarray(vectors, dtype=float), leak_flags, epochs, new_format, count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to the downloaded MetroPT-3 CSV file.")
    parser.add_argument("--rows", type=int, default=0, help="Maximum readings to load (0 = all rows).")
    args = parser.parse_args()

    bundle, message = load_local_bundle()
    if bundle is None:
        sys.exit(f"Stage 2 model unavailable: {message}")
    matrix, leak_flags, epochs, new_format, rows_read = stream_windows(args.csv, args.rows)
    if len(matrix) < 1:
        sys.exit("Not enough rows for one window.")
    scores = -bundle["model"].score_samples(matrix)
    flagged = scores > bundle["threshold"]

    leak_flags = np.asarray(leak_flags)
    windows_in_leak = int(leak_flags.sum())
    windows_outside = len(leak_flags) - windows_in_leak
    flagged_in_leak = int((flagged & leak_flags.astype(bool)).sum())
    flagged_outside = int((flagged & ~leak_flags.astype(bool)).sum())
    rate_in = flagged_in_leak / windows_in_leak if windows_in_leak else None
    rate_out = flagged_outside / windows_outside if windows_outside else None
    base_rate = windows_in_leak / len(leak_flags) if len(leak_flags) else 0.0
    enrichment = []
    for pct in (1, 5, 10):
        k = max(1, int(len(scores) * pct / 100))
        top = np.argsort(scores)[-k:]
        share = float(leak_flags[top].mean())
        enrichment.append({"top_pct": pct, "windows": k,
                           "leak_window_share": round(share, 4),
                           "enrichment_vs_base_rate": round(share / base_rate, 2) if base_rate else None})

    report = {
        "project": "FactoryAir Twin", "purpose": "MetroPT-3 transfer demonstration only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(Path(args.csv).name), "format": "MetroPT-3 2023 release" if new_format else "older MetroPt3 release",
        "rows_loaded": rows_read, "windows_scored": len(matrix),
        "window_samples": WINDOW, "stride_samples": STRIDE,
        "assumptions": {
            "kw_per_amp": ASSUMED_LINE_KW_PER_A,
            "receiver_m3": ASSUMED_RECEIVER_M3,
            "documented_leak_events": [f"{a} -> {b}" for a, b in LEAK_EVENTS],
            "feature": "Receiver air-balance outflow (mass balance over each window); "
                       "absolute pressure offsets cancel, only dp/dt matters.",
        },
        "score_summary": {
            "threshold": float(bundle["threshold"]),
            "mean_score": float(np.mean(scores)), "max_score": float(np.max(scores)),
            "flagged_windows": int(flagged.sum()),
            "windows_inside_documented_leaks": windows_in_leak,
            "flagged_inside_documented_leaks": flagged_in_leak,
            "flagged_outside_documented_leaks": flagged_outside,
            "flag_rate_inside_leak_periods": round(rate_in, 4) if rate_in is not None else None,
            "flag_rate_outside_leak_periods": round(rate_out, 4) if rate_out is not None else None,
            "mean_score_inside_leak_periods": float(np.mean(scores[leak_flags.astype(bool)])) if windows_in_leak else None,
            "mean_score_outside_leak_periods": float(np.mean(scores[~leak_flags.astype(bool)])) if windows_outside else None,
            "base_leak_window_rate": round(base_rate, 4),
            "max_score_inside_leak_periods": float(np.max(scores[leak_flags.astype(bool)])) if windows_in_leak else None,
            "max_score_outside_leak_periods": float(np.max(scores[~leak_flags.astype(bool)])) if windows_outside else None,
            "top_window_enrichment": enrichment,
            "reading": "Enrichment > 1 means the most anomalous windows concentrate inside the "
                       "company-documented leak periods more than chance would predict.",
        },
        "limits": [
            "Railway braking compressor; not an SME factory compressor.",
            "Motor current is an assumed-linear kW proxy, not calibrated power.",
            "Receiver volume and map are FactoryAir Twin assumptions.",
            "Scores describe fit to FactoryAir Twin's synthetic normality, not fault ground truth.",
            "Dataset is unlabelled; only company-reported leak intervals are available.",
        ],
    }
    out = ROOT / "reports" / "metropt3_transfer.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["score_summary"], indent=2))
    print(f"\nTransfer report saved to {out}.")
    print("This is a transfer demonstration, not a validation of FactoryAir Twin on factory data.")


if __name__ == "__main__":
    main()
