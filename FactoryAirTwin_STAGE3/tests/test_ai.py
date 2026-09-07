"""Checks for data separation, causal windows and reported evaluation results."""

import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

import ai_detector
from ai_detector import extract_windows, infer_readings, load_local_bundle
from app import app
from simulator import compare_runs, run_simulation
from train_model import (CALIBRATION_SEEDS, TEST_RUNS, THRESHOLD_QUANTILE,
                         TRAIN_SEEDS, generate_run, label_windows, metric_summary)


class FeatureAndFallbackTests(unittest.TestCase):
    def test_original_demonstration_is_preserved(self):
        result = compare_runs("leak")
        self.assertEqual(result["energy_reduction_pct"], 34.24)
        self.assertAlmostEqual(result["baseline"]["energy_kwh"], 2.282, places=3)
        self.assertAlmostEqual(result["after"]["energy_kwh"], 1.501, places=3)
        self.assertEqual(result["baseline"]["accepted_units"], 84)
        self.assertEqual(result["after"]["accepted_units"], 84)

    def test_run_splits_are_disjoint(self):
        train, calibration, test = map(set, (TRAIN_SEEDS, CALIBRATION_SEEDS, [seed for _, seed in TEST_RUNS]))
        self.assertFalse(train & calibration or train & test or calibration & test)
        self.assertEqual((len(train), len(calibration), len(test)), (40, 12, 36))

    def test_features_ignore_labels_and_future_readings(self):
        rows = generate_run("leak", 42000)["readings"]
        matrix, windows = extract_windows(rows)
        self.assertEqual(matrix.shape, (59, 4))
        self.assertTrue(np.isfinite(matrix).all())
        polluted = [dict(r, scenario="anything", injected_fault_label=999,
                         estimated_outflow_m3_min=-1000, accepted_units=-1,
                         energy_kwh=-1) for r in rows]
        np.testing.assert_array_equal(matrix, extract_windows(polluted)[0])
        prefix, prefix_windows = extract_windows(rows[:600])
        np.testing.assert_array_equal(prefix, matrix[:len(prefix)])
        self.assertEqual(prefix_windows, windows[:len(prefix)])
        self.assertTrue(all(w["end_second"] <= 600 for w in prefix_windows))
        self.assertEqual(extract_windows(rows[:59])[0].shape, (0, 4))

    def test_labels_follow_injected_timing(self):
        windows = [{"start_second": 540, "end_second": 600},
                   {"start_second": 570, "end_second": 630},
                   {"start_second": 600, "end_second": 660}]
        self.assertEqual(label_windows(windows, {"start_second": 600}), [0, None, 1])
        self.assertEqual(label_windows(windows, None), [0, 0, 0])

    def test_missing_model_preserves_simulation(self):
        try:
            with tempfile.TemporaryDirectory() as folder, patch.object(ai_detector, "MODEL_PATH", Path(folder) / "missing.joblib"):
                load_local_bundle.cache_clear()
                with app.test_client() as client:
                    status = client.get("/api/ai/status")
                    self.assertFalse(status.json["enabled"])
                    run = client.post("/api/simulate", json={"scenario": "leak"}).json
                    self.assertEqual(len(run["readings"]), 1800)
                    self.assertFalse(run["ai"]["enabled"])
                    self.assertTrue(run["alerts"])
                    self.assertEqual(client.get("/api/ai/report").status_code, 404)
        finally:
            load_local_bundle.cache_clear()


class TrainedModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle, message = load_local_bundle()
        if cls.bundle is None or not ai_detector.REPORT_PATH.exists():
            raise unittest.SkipTest("Run train_model.py first to verify trained-model outputs. " + (message or ""))
        cls.report = json.loads(ai_detector.REPORT_PATH.read_text(encoding="utf-8"))

    def test_threshold_comes_from_normal_calibration_runs(self):
        matrix = np.vstack([extract_windows(generate_run("normal", seed)["readings"])[0] for seed in CALIBRATION_SEEDS])
        expected = float(np.quantile(-self.bundle["model"].score_samples(matrix), THRESHOLD_QUANTILE))
        self.assertAlmostEqual(expected, self.bundle["threshold"], places=12)

    def test_report_metrics_recompute_from_exported_labels(self):
        with (ai_detector.ROOT / "data" / "test_predictions.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        evaluated = [row for row in rows if row["injected_fault_label"] != "transition_excluded"]
        actual = [int(row["injected_fault_label"]) for row in evaluated]
        predicted = [int(row["predicted_anomaly"]) for row in evaluated]
        self.assertEqual(metric_summary(actual, predicted), self.report["metrics"])
        self.assertEqual(len(evaluated), self.report["test_windows"])
        self.assertEqual(len(rows) - len(evaluated), self.report["excluded_transition_windows"])
        self.assertEqual({int(row["run_seed"]) for row in rows}, set(self.report["test_seeds"]))
        m = self.report["metrics"]
        self.assertEqual(sum(m[k] for k in ("true_positive", "false_positive", "false_negative", "true_negative")), len(evaluated))

    def test_scores_and_report_are_exposed_without_changing_physics(self):
        run = run_simulation("leak")
        inference = infer_readings(run["readings"])
        self.assertTrue(inference["enabled"])
        self.assertEqual(len(inference["windows"]), 59)
        for window in inference["windows"]:
            self.assertEqual(window["is_anomaly"], window["score"] > window["threshold"])
        with app.test_client() as client:
            served = client.post("/api/simulate", json={"scenario": "leak"}).json
            self.assertEqual(served["readings"], run["readings"])
            self.assertEqual(served["summary"], run["summary"])
            self.assertEqual(served["ai"], inference)
            self.assertTrue(client.get("/api/ai/status").json["enabled"])
            report_response = client.get("/api/ai/report")
            self.assertEqual(report_response.status_code, 200)
            self.assertEqual(report_response.json, self.report)
            self.assertIn("attachment", report_response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
