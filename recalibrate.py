"""Stage 5 field-readiness: per-site recalibration of the Stage-2 detector.

The shipped Isolation Forest is trained on synthetic runs and transfers only
directionally to real compressors (see the MetroPT-3 study). This module
refits the forest on a site's OWN normal operating windows and recalibrates
the alert threshold from a held-out slice of that same site data, so the
detector becomes site-specific instead of one-size-fits-all.

Usage:  python recalibrate.py path/to/site_normal.csv
(the CSV must contain t, pressure_bar, power_kw[, temperature_c] at ~1 Hz)
Set FACTORYAIR_SITE_MODEL=1 afterwards to make the app serve the site bundle.
"""

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest

from ai_detector import (FEATURE_NAMES, ROOT, SCHEMA_VERSION, extract_windows)

SITE_MODEL_PATH = ROOT / "models" / "if_model_site.joblib"
SITE_METADATA_PATH = ROOT / "models" / "model_metadata_site.json"
SITE_REPORT_PATH = ROOT / "reports" / "recalibration_report.json"
THRESHOLD_QUANTILE = 0.98  # Same fixed-in-advance policy as the original training.
MIN_WINDOWS = 60


def load_site_readings(csv_path):
    readings = []
    with open(csv_path, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            readings.append({"t": int(row["t"]), "pressure_bar": float(row["pressure_bar"]),
                             "power_kw": float(row["power_kw"]),
                             "temperature_c": float(row["temperature_c"]) if row.get("temperature_c") else None})
    return readings


def recalibrate(readings, source_name="site-upload"):
    """Refit the forest on site normal windows; returns the written report."""
    import edge_bridge  # state derivation keeps field rows compatible

    rows = []
    previous = None
    for raw in readings:
        row, why = edge_bridge._clean_row(raw, previous)
        if row is None:
            raise ValueError(f"site row rejected: {why}")
        rows.append(row)
        previous = row["second"]
    matrix, windows = extract_windows(rows)
    if len(matrix) < MIN_WINDOWS:
        raise ValueError(f"Need at least {MIN_WINDOWS} normal windows; got {len(matrix)}.")
    cut = int(len(matrix) * 0.7)
    fit_part, cal_part = matrix[:cut], matrix[cut:]
    model = IsolationForest(n_estimators=160, max_samples=256, contamination="auto", random_state=42, n_jobs=1)
    model.fit(fit_part)
    cal_scores = -model.score_samples(cal_part)
    threshold = float(np.quantile(cal_scores, THRESHOLD_QUANTILE))
    created = datetime.now(timezone.utc).isoformat()
    bundle = {"model": model, "feature_names": FEATURE_NAMES, "threshold": threshold,
              "reference_ranges": {name: [float(np.quantile(fit_part[:, i], .025)),
                                          float(np.quantile(fit_part[:, i], .975))]
                                   for i, name in enumerate(FEATURE_NAMES)},
              "trained_at": created, "schema_version": SCHEMA_VERSION,
              "sklearn_version": sklearn.__version__, "data_source": f"Site recalibration ({source_name})",
              "fit_windows": int(len(fit_part)), "calibration_windows": int(len(cal_part))}
    SITE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, SITE_MODEL_PATH)
    SITE_METADATA_PATH.write_text(json.dumps(
        {key: bundle[key] for key in ("schema_version", "feature_names", "threshold", "trained_at",
                                      "sklearn_version")}, indent=2), encoding="utf-8")
    report = {"created_at": created, "source": source_name, "windows_total": int(len(matrix)),
              "fit_windows": int(len(fit_part)), "calibration_windows": int(len(cal_part)),
              "threshold_quantile": THRESHOLD_QUANTILE, "new_threshold": threshold,
              "calibration_score_mean": float(np.mean(cal_scores)),
              "policy": "Threshold fixed from a held-out slice of the SAME site's normal data. "
                        "No fault labels used.",
              "activate": "Set FACTORYAIR_SITE_MODEL=1 and restart app.py."}
    SITE_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    SITE_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(1)
    print(json.dumps(recalibrate(load_site_readings(sys.argv[1]), source_name=Path(sys.argv[1]).name), indent=2))
