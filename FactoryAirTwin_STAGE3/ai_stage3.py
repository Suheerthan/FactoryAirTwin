"""Stage 3 detection: extended window features, supervised fault-type
classifier and hybrid event detection.

Stage 2's Isolation Forest stays in place as the unsupervised baseline.
Stage 3 adds a RandomForest trained on LABELLED simulated runs (separate
seed blocks, documented in train_stage3.py) so the dashboard can name the
probable fault type with a probability. Feature extraction still accepts
sensor readings only; labels are used exclusively during training.

Every feature is trailing (causal). The 60-second decision window is
enriched with 300-second trailing estimates because slow faults such as
filter blockage need longer context than a single minute.
"""

import json
import pickle
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import sklearn

from ai_detector import ROOT, WINDOW, STRIDE, extract_windows
from simulator import PLANT, compressor_map

STAGE3_MODEL_PATH = ROOT / "models" / "stage3_classifier.joblib"
STAGE3_REPORT_PATH = ROOT / "reports" / "stage3_validation.json"
STAGE3_SCHEMA_VERSION = 3
CLASSES = ["normal", "leak", "unloaded", "filter", "worn"]
STAGE3_FEATURE_NAMES = [
    "air_balance_residual_m3_min", "unloaded_fraction", "longest_unloaded_seconds",
    "idle_motor_kw", "idle_decay_m3_min", "power_ratio_vs_map",
    "loaded_slope_window_bar_s", "loaded_slope_trailing_bar_s",
    "power_ratio_trailing", "idle_decay_trailing_m3_min",
    "air_balance_residual_trailing_m3_min", "unloaded_fraction_trailing",
    "longest_unloaded_trailing_seconds", "min_pressure_bar",
]
CLASS_TITLES = {
    "normal": "Normal operation",
    "leak": "Air leakage",
    "unloaded": "Extended unloaded running",
    "filter": "Filter blockage",
    "worn": "Compressor deterioration",
}
TRAILING_SECONDS = 300


def _power_ratio(rows):
    ratios = [r["power_kw"] / max(compressor_map(r["pressure_bar"], "Loaded")[1], 0.1)
              for r in rows if r["state"] == "Loaded"]
    return float(np.mean(ratios)) if ratios else 1.0


def _loaded_slope(rows):
    slopes = [rows[i]["pressure_bar"] - rows[i - 1]["pressure_bar"] for i in range(1, len(rows))
              if rows[i]["state"] == "Loaded" and rows[i]["production_active"]]
    return float(np.mean(slopes)) if slopes else 0.0


def _longest_unloaded(rows):
    streak = longest = 0
    for row in rows:
        streak = streak + 1 if row["state"] == "Unloaded" else 0
        longest = max(longest, streak)
    return longest


def _idle_decay(rows):
    segments, segment_start = [], None
    if not rows:
        return 0.0
    for row in rows + [dict(rows[-1], production_active=not rows[-1]["production_active"])]:
        if not row["production_active"] and segment_start is None:
            segment_start = row
        elif row["production_active"] and segment_start is not None:
            dt = row["second"] - segment_start["second"]
            if dt > 0:
                segments.append(-PLANT.receiver_m3 / PLANT.reference_bar
                                * (row["pressure_bar"] - segment_start["pressure_bar"]) * 60 / dt)
            segment_start = None
    return float(np.mean(segments)) if segments else 0.0


def _air_balance_residual(rows):
    """Estimated total outflow minus the nominal production demand proxy."""
    if len(rows) < 2:
        return 0.0
    elapsed = rows[-1]["second"] - rows[0]["second"]
    if elapsed <= 0:
        return 0.0
    inlet = np.mean([compressor_map(r["pressure_bar"], r["state"])[0] for r in rows[1:]])
    outflow = inlet - PLANT.receiver_m3 / PLANT.reference_bar * (rows[-1]["pressure_bar"] - rows[0]["pressure_bar"]) * 60 / elapsed
    production_fraction = np.mean([bool(r["production_active"]) for r in rows[1:]])
    return float(outflow - 0.45 * production_fraction)


def extract_stage3_windows(readings):
    """Extended features on the same 60 s / 30 s window grid. Causal only."""
    base_matrix, windows = extract_windows(readings)
    vectors = []
    for index, (end, window) in enumerate(zip(range(WINDOW, len(readings) + 1, STRIDE), windows)):
        rows = readings[end - WINDOW:end]
        trail = readings[max(0, end - TRAILING_SECONDS):end]
        # Idle decay: receiver pressure fall during production pauses,
        # converted to an equivalent outflow. A leak makes this positive.
        extended = [
            _idle_decay(rows),
            _power_ratio(rows),
            _loaded_slope(rows),
            _loaded_slope(trail),
            _power_ratio(trail),
            _idle_decay(trail),
            _air_balance_residual(trail),
            float(np.mean([r["state"] == "Unloaded" for r in trail])),
            float(_longest_unloaded(trail)),
            float(min(r["pressure_bar"] for r in rows)),
        ]
        if not np.all(np.isfinite(extended)):
            raise ValueError("Stage 3 features must be finite; check input measurements.")
        vectors.append(list(base_matrix[index]) + extended)
        window["features"] = {**window["features"],
                              **dict(zip(STAGE3_FEATURE_NAMES[4:], extended))}
    return np.asarray(vectors, dtype=float).reshape((-1, len(STAGE3_FEATURE_NAMES))), windows


@lru_cache(maxsize=1)
def load_stage3_bundle():
    if not STAGE3_MODEL_PATH.exists():
        return None, "Stage 3 classifier not trained. Run train_stage3.py, then restart app.py."
    try:
        bundle = joblib.load(STAGE3_MODEL_PATH)
        if bundle.get("schema_version") != STAGE3_SCHEMA_VERSION or bundle.get("sklearn_version") != sklearn.__version__:
            return None, "Stage 3 model settings or scikit-learn version changed. Run train_stage3.py again."
        if bundle.get("feature_names") != STAGE3_FEATURE_NAMES:
            return None, "Stage 3 features changed. Run train_stage3.py again."
        return bundle, None
    except (OSError, ValueError, KeyError, EOFError, ImportError, AttributeError, TypeError, pickle.UnpicklingError) as exc:
        return None, f"Stage 3 model could not be loaded ({type(exc).__name__}). Run train_stage3.py again."


def _decide(bundle, probabilities):
    """Per-class probability gates calibrated on separate normal runs."""
    classes = list(bundle["model"].classes_)
    thresholds = bundle["thresholds"]
    decisions = []
    for row in probabilities:
        probs = dict(zip(classes, row))
        fault_classes = [c for c in classes if c != "normal"]
        best = max(fault_classes, key=lambda c: probs[c])
        if probs[best] >= thresholds.get(best, 0.5):
            decisions.append({"class": best, "probability": float(probs[best]), "is_fault": True})
        else:
            decisions.append({"class": "normal", "probability": float(probs.get("normal", 0.0)), "is_fault": False})
        decisions[-1]["p_normal"] = float(probs.get("normal", 0.0))
        decisions[-1]["probabilities"] = {k: round(float(v), 4) for k, v in probs.items()}
    return decisions


def _predict(bundle, matrix):
    if len(matrix) == 0:
        return [], []
    probabilities = bundle["model"].predict_proba(matrix)
    decisions = _decide(bundle, probabilities)
    return decisions, [d["is_fault"] for d in decisions]


def infer_stage3(readings):
    bundle, message = load_stage3_bundle()
    if bundle is None:
        return {"enabled": False, "message": message, "windows": []}
    matrix, windows = extract_stage3_windows(readings)
    decisions, flags = _predict(bundle, matrix)
    out = []
    for window, decision in zip(windows, decisions):
        out.append({"start_second": window["start_second"], "end_second": window["end_second"],
                    "predicted_class": decision["class"], "probability": decision["probability"],
                    "p_normal": decision["p_normal"], "is_fault": decision["is_fault"],
                    "probabilities": decision["probabilities"]})
    return {"enabled": True, "method": "RandomForest fault-type classifier (supervised, synthetic labels)",
            "classes": CLASSES, "class_titles": CLASS_TITLES, "thresholds": bundle["thresholds"],
            "window_seconds": WINDOW, "stride_seconds": STRIDE,
            "note": "Trained on labelled simulated runs only; probabilities are not field-validated.",
            "windows": out}


def stage3_status():
    bundle, message = load_stage3_bundle()
    if bundle is None:
        return {"enabled": False, "message": message}
    try:
        report = json.loads(STAGE3_REPORT_PATH.read_text(encoding="utf-8"))
        if report.get("created_at") != bundle["trained_at"] or report.get("schema_version") != STAGE3_SCHEMA_VERSION:
            report = None
    except (OSError, ValueError, AttributeError):
        report = None
    return {"enabled": True, "method": bundle["method"], "thresholds": bundle["thresholds"],
            "trained_at": bundle["trained_at"], "sklearn_version": sklearn.__version__, "report": report}
