r"""Optional MetroPT-3 transfer demonstration (run locally, offline).

The UCI MetroPT-3 dataset (https://archive.ics.uci.edu/dataset/791/metropt%2B3%2Bdataset)
contains 1.5M+ readings of a metro-compressor: pressure (kPa), temperature,
motor current (A) and documented leak classes. FactoryAir Twin does NOT ship
the data. Download it yourself, then run for example:

    .\.venv\Scripts\python.exe metropt3.py --csv MetroPt3_1.txt --rows 50000

This script maps the external columns onto FactoryAir Twin's window features
and scores them with the locally trained Stage 2 Isolation Forest. It is a
TRANSFER DEMONSTRATION, not performance evidence:

* MetroPT-3 is a railway braking compressor, not an SME factory machine.
* Motor current is converted with an assumed voltage/power factor; it is NOT
  a calibrated kW measurement.
* The receiver volume and performance map are FactoryAir Twin's assumptions,
  not the machine's datasheet.
* Feature windows reuse FactoryAir Twin's production flag, which has no
  equivalent in MetroPT-3 and is set inactive throughout.

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
ASSUMED_LINE_KW_PER_A = 0.4  # assumed 400 V three-phase with pf ≈ 0.85: A -> kW proxy.
ASSUMED_RECEIVER_M3 = 5.0    # metro auxiliary reservoir order of magnitude.


def load_rows(path, max_rows):
    rows = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"Pressure", "Temperature", "Motor current", "Leak"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV must contain columns {sorted(required)}; missing {sorted(missing)}.")
        for i, record in enumerate(reader):
            if i >= max_rows:
                break
            try:
                rows.append({
                    "second": i + 1,
                    "pressure_bar": float(record["Pressure"]) / 100.0 - 1.01325,  # kPa abs -> bar gauge
                    "temperature_c": float(record["Temperature"]),
                    "power_kw": float(record["Motor current"]) * ASSUMED_LINE_KW_PER_A,
                    "leak_class": int(float(record["Leak"])),
                })
            except ValueError:
                continue
    return rows


def extract_transfer_windows(rows):
    """Air-balance feature using the ASSUMED receiver; no production signal."""
    vectors, meta = [], []
    for end in range(WINDOW, len(rows) + 1, STRIDE):
        window = rows[end - WINDOW:end]
        elapsed = window[-1]["second"] - window[0]["second"]
        if elapsed <= 0:
            continue
        dp = window[-1]["pressure_bar"] - window[0]["pressure_bar"]
        outflow = -ASSUMED_RECEIVER_M3 / PLANT.reference_bar * dp * 60 / elapsed
        vectors.append([float(outflow), 0.0, 0.0, 0.0])
        meta.append({"end_second": window[-1]["second"],
                     "leak_present": int(any(r["leak_class"] > 0 for r in window))})
    return np.asarray(vectors, dtype=float), meta


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="Path to the downloaded MetroPT-3 CSV file.")
    parser.add_argument("--rows", type=int, default=50000, help="Maximum readings to load.")
    args = parser.parse_args()

    bundle, message = load_local_bundle()
    if bundle is None:
        sys.exit(f"Stage 2 model unavailable: {message}")
    rows = load_rows(args.csv, args.rows)
    if len(rows) < WINDOW + STRIDE:
        sys.exit("Not enough rows for one window.")
    matrix, meta = extract_transfer_windows(rows)
    scores = -bundle["model"].score_samples(matrix)
    flagged = scores > bundle["threshold"]
    leak_flags = [m["leak_present"] for m in meta]
    report = {
        "project": "FactoryAir Twin", "purpose": "MetroPT-3 transfer demonstration only",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_file": str(Path(args.csv).name), "rows_loaded": len(rows), "windows": len(meta),
        "assumptions": {
            "kw_per_amp": ASSUMED_LINE_KW_PER_A,
            "receiver_m3": ASSUMED_RECEIVER_M3,
            "production_flag": "inactive (no production signal in MetroPT-3)",
        },
        "score_summary": {"mean": float(np.mean(scores)), "max": float(np.max(scores)),
                          "threshold": bundle["threshold"],
                          "flagged_windows": int(flagged.sum()),
                          "flagged_windows_with_documented_leak": int(sum(f & l for f, l in zip(flagged, leak_flags)))},
        "limits": [
            "Railway braking compressor; not an SME factory compressor.",
            "Motor current is an assumed-linear kW proxy, not calibrated power.",
            "Receiver volume and map are FactoryAir Twin assumptions.",
            "Scores describe fit to FactoryAir Twin's synthetic normality, not fault ground truth.",
        ],
    }
    out = ROOT / "reports" / "metropt3_transfer.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report["score_summary"], indent=2))
    print(f"Transfer report saved to {out}. This is not a validation of FactoryAir Twin on factory data.")


if __name__ == "__main__":
    main()
