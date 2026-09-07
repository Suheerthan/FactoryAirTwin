"""Window features and local Isolation Forest inference.

Feature extraction accepts sensor readings only. Scenario names, fault labels,
repair settings, model scores and evaluation metadata are never model inputs.
"""

import json
import pickle
from functools import lru_cache
from pathlib import Path

import joblib
import numpy as np
import sklearn

from simulator import PLANT, compressor_map


ROOT = Path(__file__).resolve().parent
MODEL_PATH = ROOT / "models" / "factoryair_isolation_forest.joblib"
METADATA_PATH = ROOT / "models" / "model_metadata.json"
REPORT_PATH = ROOT / "reports" / "validation_report.json"
SCHEMA_VERSION = 2
WINDOW = 60
STRIDE = 30
FEATURE_NAMES = ["air_balance_residual_m3_min", "unloaded_fraction", "longest_unloaded_seconds", "idle_motor_kw"]
FEATURE_LABELS = ["Demand-adjusted air outflow", "Fraction of time unloaded", "Longest unloaded spell", "Motor power during production pauses"]


def extract_windows(readings):
    """60-second trailing windows every 30 seconds, with no future readings."""
    vectors, windows = [], []
    for end in range(WINDOW, len(readings) + 1, STRIDE):
        rows = readings[end - WINDOW:end]
        elapsed = rows[-1]["second"] - rows[0]["second"]
        if elapsed <= 0:
            raise ValueError("Readings must have increasing timestamps.")
        # Average the receiver balance over the window; do not average the
        # clipped, noisy one-second display estimate from the simulator.
        inlet = np.mean([compressor_map(r["pressure_bar"], r["state"])[0] for r in rows[1:]])
        outflow = inlet - PLANT.receiver_m3 / PLANT.reference_bar * (rows[-1]["pressure_bar"] - rows[0]["pressure_bar"]) * 60 / elapsed
        production_fraction = np.mean([bool(r["production_active"]) for r in rows[1:]])
        unloaded_fraction = np.mean([r["state"] == "Unloaded" for r in rows])
        streak = longest = 0
        for row in rows:
            streak = streak + 1 if row["state"] == "Unloaded" else 0
            longest = max(longest, streak)
        idle_power = np.mean([r["power_kw"] if not r["production_active"] else 0 for r in rows])
        values = [float(outflow - 0.45 * production_fraction), float(unloaded_fraction), float(longest), float(idle_power)]
        if not np.all(np.isfinite(values)):
            raise ValueError("Features must be finite; check input measurements.")
        vectors.append(values)
        windows.append({"start_second": rows[0]["second"] - 1, "end_second": rows[-1]["second"], "features": dict(zip(FEATURE_NAMES, values))})
    return np.asarray(vectors, dtype=float).reshape((-1, len(FEATURE_NAMES))), windows


def score_windows(bundle, matrix, windows):
    if len(matrix) == 0:
        return []
    scores = -bundle["model"].score_samples(matrix)
    result = []
    for values, window, score in zip(matrix, windows, scores):
        observations = []
        for i, (name, value) in enumerate(zip(FEATURE_NAMES, values)):
            low, high = bundle["reference_ranges"][name]
            if value < low or value > high:
                observations.append(f"{FEATURE_LABELS[i]}: {value:.3f}; normal training reference {low:.3f}–{high:.3f}.")
        result.append({**window, "score": float(score), "threshold": bundle["threshold"],
                       "is_anomaly": bool(score > bundle["threshold"]), "observations": observations[:2]})
    return result


@lru_cache(maxsize=1)
def load_local_bundle():
    if not MODEL_PATH.exists() or not METADATA_PATH.exists():
        return None, "Model not trained. Run train_model.py, then restart app.py."
    try:
        metadata = json.loads(METADATA_PATH.read_text(encoding="utf-8"))
        if metadata.get("schema_version") != SCHEMA_VERSION or metadata.get("sklearn_version") != sklearn.__version__:
            return None, "Model settings or scikit-learn version changed. Run train_model.py again, then restart app.py."
        # Only the model generated locally by the supplied training script is
        # loaded. The app has no model-upload or arbitrary-file-loading route.
        bundle = joblib.load(MODEL_PATH)
        if bundle.get("feature_names") != FEATURE_NAMES:
            return None, "Model features changed. Train the model again."
        return bundle, None
    except (OSError, ValueError, KeyError, EOFError, ImportError, AttributeError, TypeError, pickle.UnpicklingError) as exc:
        return None, f"Local model could not be loaded ({type(exc).__name__}). Run train_model.py again."


def infer_readings(readings):
    bundle, message = load_local_bundle()
    if bundle is None:
        return {"enabled": False, "message": message, "windows": []}
    matrix, windows = extract_windows(readings)
    return {"enabled": True, "method": "Isolation Forest", "data_source": "Synthetic compressor simulations",
            "threshold": bundle["threshold"], "window_seconds": WINDOW, "stride_seconds": STRIDE,
            "score_note": "Anomaly score, not a probability or a confirmed fault cause.",
            "windows": score_windows(bundle, matrix, windows)}


def model_status():
    bundle, message = load_local_bundle()
    if bundle is None:
        return {"enabled": False, "message": message}
    try:
        report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        if report.get("created_at") != bundle["trained_at"] or report.get("schema_version") != SCHEMA_VERSION:
            report = None
    except (OSError, ValueError, AttributeError):
        report = None
    return {"enabled": True, "method": "Isolation Forest", "threshold": bundle["threshold"],
            "trained_at": bundle["trained_at"], "sklearn_version": sklearn.__version__, "report": report}
