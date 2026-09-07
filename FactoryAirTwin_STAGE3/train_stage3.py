"""Train and evaluate the Stage 3 supervised fault-type classifier.

Seed blocks are disjoint from every Stage 2 split. The Stage 2 test seeds
(9100–9399) are never touched here; Stage 3 reserves its own final test
block (9500–9999) so the Stage 2 evaluation remains an untouched baseline.
"""

import csv
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import sklearn
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import confusion_matrix, f1_score, precision_recall_fscore_support

from ai_detector import ROOT
from ai_stage3 import (CLASSES, CLASS_TITLES, STAGE3_FEATURE_NAMES, STAGE3_MODEL_PATH,
                       STAGE3_REPORT_PATH, STAGE3_SCHEMA_VERSION, _decide, extract_stage3_windows)
from simulator import run_simulation

SCENARIO_SEED_BASE = {"normal": 3100, "leak": 3200, "unloaded": 3300, "filter": 3400, "worn": 3500}
RUNS_PER_SCENARIO = 20
TRAIN_SEEDS = {sc: list(range(base, base + RUNS_PER_SCENARIO)) for sc, base in SCENARIO_SEED_BASE.items()}
CALIBRATION_SEEDS = list(range(3600, 3620))  # normal runs, used only for the probability gates.
TEST_SEED_BASE = {"normal": 9500, "leak": 9600, "unloaded": 9700, "filter": 9800, "worn": 9900}
TEST_RUNS = [(sc, seed) for sc, base in TEST_SEED_BASE.items() for seed in range(base, base + RUNS_PER_SCENARIO)]
FAULT_INJECTION_SECOND = 600


def generate_run(scenario, seed):
    return run_simulation(scenario, variation_seed=seed, fault_start=FAULT_INJECTION_SECOND)


def label_windows_stage3(windows, interval, scenario):
    """Window class labels from injection timing; boundary windows excluded."""
    labels = []
    for window in windows:
        if interval is None or window["end_second"] <= interval["start_second"]:
            labels.append("normal")
        elif window["start_second"] >= interval["start_second"]:
            labels.append(scenario)
        else:
            labels.append(None)
    return labels


def train_and_evaluate():
    print("1/4 Generating labelled training runs...", flush=True)
    print(f"   ({RUNS_PER_SCENARIO} runs x 5 scenarios)", flush=True)
    matrices, labels, train_rows = [], [], []
    for scenario, seeds in TRAIN_SEEDS.items():
        for seed in seeds:
            run = generate_run(scenario, seed)
            matrix, windows = extract_stage3_windows(run["readings"])
            window_labels = label_windows_stage3(windows, run["fault_interval"], scenario)
            keep = [i for i, label in enumerate(window_labels) if label is not None]
            matrices.append(matrix[keep])
            labels.extend(window_labels[i] for i in keep)
            for i in keep:
                train_rows.append({"run_seed": seed, "scenario": scenario,
                                   "end_second": windows[i]["end_second"], "label": window_labels[i],
                                   **windows[i]["features"]})
    training = np.vstack(matrices)
    model = RandomForestClassifier(n_estimators=400, class_weight="balanced", random_state=42, n_jobs=1)
    model.fit(training, labels)

    print("2/4 Calibrating per-class probability gates on separate normal runs...", flush=True)
    calibration = np.vstack([extract_stage3_windows(generate_run("normal", seed)["readings"])[0]
                             for seed in CALIBRATION_SEEDS])
    calibration_probs = model.predict_proba(calibration)
    classes = list(model.classes_)
    thresholds = {}
    for cls in classes:
        if cls == "normal":
            continue
        column = calibration_probs[:, classes.index(cls)]
        thresholds[cls] = float(max(0.5, np.quantile(column, 0.995)))
    created = datetime.now(timezone.utc).isoformat()
    bundle = {"model": model, "method": "RandomForestClassifier", "feature_names": STAGE3_FEATURE_NAMES,
              "classes": CLASSES, "thresholds": thresholds, "trained_at": created,
              "schema_version": STAGE3_SCHEMA_VERSION, "sklearn_version": sklearn.__version__}

    print("3/4 Evaluating held-out Stage 3 test runs...", flush=True)
    actual, predicted, prediction_rows, per_run = [], [], [], []
    fault_events, detected_events, delays = 0, 0, []
    normal_episodes, normal_hours = 0, 0.0
    excluded = 0
    for scenario, seed in TEST_RUNS:
        run = generate_run(scenario, seed)
        matrix, windows = extract_stage3_windows(run["readings"])
        window_labels = label_windows_stage3(windows, run["fault_interval"], scenario)
        decisions = _decide(bundle, model.predict_proba(matrix))
        rule_seconds = {a["key"]: a["second"] for a in run["alerts"]}
        local_actual, local_predicted = [], []
        first_detection = None
        was_flagged = False
        for window, label, decision in zip(windows, window_labels, decisions):
            flag = decision["is_fault"]
            predicted_class = decision["class"]
            prediction_rows.append({"run_seed": seed, "scenario": scenario, "end_second": window["end_second"],
                                    "injected_label": "transition_excluded" if label is None else label,
                                    "predicted_class": predicted_class, "max_probability": round(decision["probability"], 4),
                                    "flagged": int(flag), **window["features"]})
            if scenario == "normal":
                if flag and not was_flagged:
                    normal_episodes += 1
                was_flagged = flag
            if label is None:
                excluded += 1
                continue
            actual.append(label)
            predicted.append(predicted_class)
            local_actual.append(label)
            local_predicted.append(predicted_class)
            # Hybrid event detection: classifier flag OR matching engineering rule already active.
            hybrid = flag or (scenario in rule_seconds and rule_seconds[scenario] <= window["end_second"])
            if scenario != "normal" and label == scenario and hybrid and first_detection is None:
                first_detection = window["end_second"] - run["fault_interval"]["start_second"]
        if scenario != "normal":
            fault_events += 1
            if first_detection is not None:
                detected_events += 1
                delays.append(first_detection)
        else:
            normal_hours += run["summary"]["duration_seconds"] / 3600
        per_run.append({"scenario": scenario, "seed": seed,
                        "hybrid_first_detection_delay_seconds": first_detection})

    precision, recall, f1, _ = precision_recall_fscore_support(actual, predicted, labels=CLASSES, zero_division=0)
    macro_f1 = float(f1_score(actual, predicted, labels=CLASSES, average="macro", zero_division=0))
    confusion = confusion_matrix(actual, predicted, labels=CLASSES)
    report = {
        "project": "FactoryAir Twin", "stage": "Stage 3 supervised fault-type classifier",
        "method": "RandomForestClassifier on 7 window features",
        "data_source": "Generated compressor simulations only; labelled by fault-injection timing.",
        "created_at": created, "python_version": platform.python_version(), "sklearn_version": sklearn.__version__,
        "numpy_version": np.__version__, "schema_version": STAGE3_SCHEMA_VERSION,
        "features": STAGE3_FEATURE_NAMES, "classes": CLASSES, "class_titles": CLASS_TITLES,
        "train_runs_per_scenario": RUNS_PER_SCENARIO, "test_runs_per_scenario": RUNS_PER_SCENARIO,
        "train_windows": len(training), "calibration_windows": len(calibration), "test_windows": len(actual),
        "excluded_transition_windows": excluded, "thresholds": thresholds,
        "threshold_policy": ("Per-class fault gates set at the 99.5th percentile of that class's probability "
                             "on separate calibration normal runs (floor 0.5). No test labels used."),
        "train_seeds": TRAIN_SEEDS, "calibration_seeds": CALIBRATION_SEEDS,
        "test_seeds": {sc: list(range(base, base + RUNS_PER_SCENARIO)) for sc, base in TEST_SEED_BASE.items()},
        "fault_injection_second": FAULT_INJECTION_SECOND,
        "per_class": {cls: {"precision": float(p), "recall": float(r), "f1": float(f)}
                      for cls, p, r, f in zip(CLASSES, precision, recall, f1)},
        "macro_f1": macro_f1,
        "confusion": {"labels": CLASSES, "matrix": confusion.tolist()},
        "event_detection": {"detected": detected_events, "total": fault_events,
                            "median_delay_seconds_among_detected": float(np.median(delays)) if delays else None,
                            "definition": "Hybrid: classifier flag OR matching engineering rule, fully post-onset."},
        "normal_run_alarm_episodes": normal_episodes, "normal_run_hours": normal_hours,
        "normal_alarm_episodes_per_hour": normal_episodes / normal_hours if normal_hours else None,
        "limits": ["Classifier learns from synthetic fault injections; field data has not been used.",
                   "Stage 2 Isolation Forest results remain the untouched unsupervised baseline.",
                   "Probabilities are model outputs, not calibrated field failure odds.",
                   "Window samples overlap and are correlated within a run."],
    }

    print("4/4 Saving Stage 3 model, report and predictions...", flush=True)
    STAGE3_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    STAGE3_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, STAGE3_MODEL_PATH)
    STAGE3_REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    csv_path = ROOT / "data" / "stage3_predictions.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(prediction_rows[0].keys()))
        writer.writeheader()
        writer.writerows(prediction_rows)
    print(f"Stage 3 TEST macro-F1 {macro_f1:.1%}; events detected {detected_events}/{fault_events}; "
          f"normal-run false-alarm episodes {normal_episodes}.", flush=True)
    print("Stage 3 training complete. Restart app.py and refresh your browser.", flush=True)
    return report


if __name__ == "__main__":
    train_and_evaluate()
