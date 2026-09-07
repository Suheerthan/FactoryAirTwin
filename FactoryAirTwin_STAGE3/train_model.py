"""Train locally and evaluate whole, separate simulated runs. No downloads."""

import csv
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.metrics import confusion_matrix, precision_recall_fscore_support

from ai_detector import (FEATURE_NAMES, METADATA_PATH, MODEL_PATH, REPORT_PATH, ROOT, SCHEMA_VERSION,
                         STRIDE, WINDOW, extract_windows, score_windows)
from simulator import run_simulation


TRAIN_SEEDS = list(range(1100, 1140))
CALIBRATION_SEEDS = list(range(2100, 2112))
TEST_RUNS = [(scenario, seed) for scenario, first in [("normal", 9100), ("leak", 9200), ("unloaded", 9300)] for seed in range(first, first + 12)]
THRESHOLD_QUANTILE = 0.98  # Fixed in advance; uses normal calibration data only.


def generate_run(scenario, seed):
    return run_simulation(scenario, variation_seed=seed, fault_start=600)


def label_windows(windows, interval):
    """Labels come from injected fault timing, NOT from rules/model features.

    Exclude a window crossing the injection boundary. Overlapping windows
    stay within one run and one split; they are not independent samples.
    """
    if interval is None:
        return [0] * len(windows)
    labels = []
    for window in windows:
        if window["end_second"] <= interval["start_second"]:
            labels.append(0)
        elif window["start_second"] >= interval["start_second"]:
            labels.append(1)
        else:
            labels.append(None)
    return labels


def metric_summary(actual, predicted):
    precision, recall, f1, _ = precision_recall_fscore_support(actual, predicted, average="binary", zero_division=0)
    tn, fp, fn, tp = confusion_matrix(actual, predicted, labels=[0, 1]).ravel()
    return {"precision": float(precision), "recall": float(recall), "f1": float(f1),
            "true_positive": int(tp), "false_positive": int(fp), "false_negative": int(fn), "true_negative": int(tn),
            "normal_windows": int(tn + fp), "fault_windows": int(tp + fn),
            "false_positive_rate": float(fp / (tn + fp)) if tn + fp else None}


def write_csv(path, rows, columns):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def train_and_evaluate():
    print("1/4 Generating 40 normal training runs...", flush=True)
    training, train_rows = [], []
    for seed in TRAIN_SEEDS:
        matrix, windows = extract_windows(generate_run("normal", seed)["readings"])
        training.append(matrix)
        train_rows.extend({"run_seed": seed, "end_second": w["end_second"], **w["features"]} for w in windows)
    training = np.vstack(training)
    # The model sees normal features only. Neither labels nor scenario IDs are
    # inputs. Split seeds are recorded in the report; simulator settings are
    # recorded in simulator.py and documented in README.md.
    model = IsolationForest(n_estimators=160, max_samples=256, contamination="auto", random_state=42, n_jobs=1)
    model.fit(training)

    print("2/4 Calibrating the alert threshold on 12 separate normal runs...", flush=True)
    calibration = np.vstack([extract_windows(generate_run("normal", seed)["readings"])[0] for seed in CALIBRATION_SEEDS])
    calibration_scores = -model.score_samples(calibration)
    threshold = float(np.quantile(calibration_scores, THRESHOLD_QUANTILE))
    created = datetime.now(timezone.utc).isoformat()
    bundle = {"model": model, "feature_names": FEATURE_NAMES, "threshold": threshold,
              "reference_ranges": {name: [float(np.quantile(training[:, i], .025)), float(np.quantile(training[:, i], .975))] for i, name in enumerate(FEATURE_NAMES)},
              "trained_at": created, "schema_version": SCHEMA_VERSION, "sklearn_version": sklearn.__version__}

    print("3/4 Evaluating 36 held-out runs; test labels do not tune the model...", flush=True)
    actual, predicted, prediction_rows, per_run = [], [], [], []
    fault_events, detected_events, delays = 0, 0, []
    normal_episode_count, normal_duration_hours = 0, 0.0
    excluded = 0
    for scenario, seed in TEST_RUNS:
        run = generate_run(scenario, seed)
        matrix, windows = extract_windows(run["readings"])
        labels = label_windows(windows, run["fault_interval"])
        predictions = score_windows(bundle, matrix, windows)
        local_actual, local_predicted = [], []
        first_detection = None
        was_flagged = False
        for label, result in zip(labels, predictions):
            flag = int(result["is_anomaly"])
            prediction_rows.append({"run_seed": seed, "scenario": scenario, "end_second": result["end_second"],
                                    "injected_fault_label": "transition_excluded" if label is None else label,
                                    "anomaly_score": result["score"], "threshold": threshold, "predicted_anomaly": flag,
                                    **result["features"]})
            if scenario == "normal":
                if flag and not was_flagged:
                    normal_episode_count += 1
                was_flagged = bool(flag)
            if label is None:
                excluded += 1
                continue
            actual.append(label); predicted.append(flag)
            local_actual.append(label); local_predicted.append(flag)
            if label == 1 and flag and first_detection is None:
                first_detection = result["end_second"] - run["fault_interval"]["start_second"]
        if scenario != "normal":
            fault_events += 1
            if first_detection is not None:
                detected_events += 1
                delays.append(first_detection)
        else:
            normal_duration_hours += run["summary"]["duration_seconds"] / 3600
        per_run.append({"scenario": scenario, "seed": seed, "first_post_onset_detection_delay_seconds": first_detection,
                        **metric_summary(local_actual, local_predicted)})

    report = {
        "project": "FactoryAir Twin", "stage": "Synthetic-data anomaly detector", "method": "Isolation Forest",
        "data_source": "Generated compressor simulations only; no factory or external dataset measurements.",
        "created_at": created, "python_version": platform.python_version(), "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__, "schema_version": SCHEMA_VERSION, "features": FEATURE_NAMES,
        "window_seconds": WINDOW, "stride_seconds": STRIDE,
        "train_runs": len(TRAIN_SEEDS), "calibration_runs": len(CALIBRATION_SEEDS), "test_runs": len(TEST_RUNS),
        "train_windows": len(training), "calibration_windows": len(calibration), "test_windows": len(actual),
        "excluded_transition_windows": excluded, "train_seeds": TRAIN_SEEDS, "calibration_seeds": CALIBRATION_SEEDS,
        "test_seeds": [seed for _, seed in TEST_RUNS], "threshold": threshold, "threshold_quantile": THRESHOLD_QUANTILE,
        "threshold_policy": "98th percentile of anomaly scores from separate NORMAL calibration runs. No test labels used.",
        "fault_injection_second": 600, "label_policy": "Normal until injection; fault after injection; boundary-crossing windows excluded.",
        "metrics": metric_summary(actual, predicted),
        "event_detection": {"detected": detected_events, "total": fault_events,
                            "median_delay_seconds_among_detected": float(np.median(delays)) if delays else None,
                            "definition": "At least one flagged fully post-onset window. Missed events are counted separately."},
        "normal_run_alarm_episodes": normal_episode_count, "normal_run_hours": normal_duration_hours,
        "normal_alarm_episodes_per_hour": normal_episode_count / normal_duration_hours,
        "per_run": per_run,
        "limits": ["All runs use the same simplified simulator family; different seeds are not evidence of real-world generalisation.",
                   "Window samples overlap and are correlated; metrics are window-level unless explicitly labelled event-level.",
                   "Injected fault timing defines labels; a fault may not be observable immediately in every operating state.",
                   "Anomaly scores are not probabilities, fault classifications, exact leak locations or failure forecasts.",
                   "The displayed normal-range observations are measurements, not causal explanations or formal model attributions.",
                   "AI does not control the compressor or change the simulated intervention/savings calculations."]
    }
    print("4/4 Saving the local model, report and reproducible feature data...", flush=True)
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, MODEL_PATH)
    metadata = {key: bundle[key] for key in ("schema_version", "feature_names", "threshold", "trained_at", "sklearn_version")}
    METADATA_PATH.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    write_csv(ROOT / "data" / "training_features.csv", train_rows, ["run_seed", "end_second", *FEATURE_NAMES])
    write_csv(ROOT / "data" / "test_predictions.csv", prediction_rows,
              ["run_seed", "scenario", "end_second", "injected_fault_label", "anomaly_score", "threshold", "predicted_anomaly", *FEATURE_NAMES])
    m = report["metrics"]
    print(f"Synthetic TEST precision {m['precision']:.1%}; recall {m['recall']:.1%}; F1 {m['f1']:.1%}", flush=True)
    print(f"Normal-window false-positive rate: {m['false_positive_rate']:.1%}. Events detected: {detected_events}/{fault_events}.", flush=True)
    print("Training complete. Start app.py and refresh your browser.", flush=True)
    return report


if __name__ == "__main__":
    train_and_evaluate()
