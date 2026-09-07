"""Stage 3 checks: new scenarios, optimiser, classifier and endpoints.

Like the Stage 2 suites, these tests do not force a savings percentage;
they check physical consistency, guardrails and report arithmetic.
"""

import json
import unittest

import ai_stage3
from ai_stage3 import (STAGE3_FEATURE_NAMES, extract_stage3_windows, infer_stage3,
                       load_stage3_bundle)
from app import app
from optimizer import (compare_sequences, intervention_catalog, schedule_savings,
                       setpoint_search)
from simulator import PLANT, compare_runs, run_simulation


class NewScenarioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {name: run_simulation(name) for name in ("filter", "worn")}

    def test_new_scenarios_raise_their_own_rules(self):
        self.assertTrue(any(a["key"] == "filter" for a in self.runs["filter"]["alerts"]))
        self.assertTrue(any(a["key"] == "worn" for a in self.runs["worn"]["alerts"]))

    def test_original_scenarios_keep_clean_alert_sets(self):
        for name in ("normal", "leak", "unloaded"):
            keys = {a["key"] for a in run_simulation(name)["alerts"]}
            self.assertNotIn("filter", keys)
            self.assertNotIn("worn", keys)

    def test_no_phantom_leak_alert_in_filter_scenario(self):
        self.assertFalse(any(a["key"] == "leak" for a in self.runs["filter"]["alerts"]))

    def test_new_interventions_save_energy_and_keep_guardrails(self):
        for scenario in ("filter", "worn"):
            with self.subTest(scenario=scenario):
                result = compare_runs(scenario)
                self.assertGreater(result["savings_kwh"], 0)
                self.assertTrue(result["simulation_guardrails_pass"])
                self.assertEqual(result["baseline"]["accepted_units"], result["after"]["accepted_units"])

    def test_leak_alert_carries_money_carbon_impact(self):
        run = run_simulation("leak")
        alert = next(a for a in run["alerts"] if a["key"] == "leak")
        impact = alert["impact"]
        self.assertGreater(impact["kwh_per_day"], 0)
        self.assertGreater(impact["inr_per_month"], 0)
        self.assertAlmostEqual(impact["co2_kg_per_month"], impact["kwh_per_day"] * 30 * 0.79, delta=1)

    def test_varied_normal_runs_stay_rule_quiet(self):
        for seed in (3100, 3101, 3102, 3103, 3104):
            with self.subTest(seed=seed):
                self.assertEqual(run_simulation("normal", variation_seed=seed)["alerts"], [])

    def test_setpoint_overrides_validate_inputs(self):
        with self.assertRaises(ValueError):
            run_simulation("normal", cut_in_bar=7.0, cut_out_bar=6.5)
        with self.assertRaises(ValueError):
            run_simulation("normal", cut_in_bar=99)
        shifted = run_simulation("normal", cut_in_bar=6.2, cut_out_bar=7.0)
        self.assertEqual(len(shifted["readings"]), PLANT.duration_seconds)
        # One-second integration overshoots cut-out slightly before unloading.
        self.assertLess(max(r["pressure_bar"] for r in shifted["readings"]), 7.15)


class OptimiserTests(unittest.TestCase):
    def test_setpoint_search_keeps_guardrails_and_reports_candidates(self):
        result = setpoint_search("leak")
        self.assertEqual(len(result["candidates"]), 3)
        for candidate in result["candidates"]:
            self.assertLess(candidate["cut_in_bar"], PLANT.cut_in_bar)
        recommended = result["recommended"]
        self.assertIsNotNone(recommended)
        self.assertTrue(recommended["guardrails_pass"])
        self.assertGreaterEqual(recommended["savings_kwh"], 0)

    def test_setpoint_never_costs_energy_when_recommended(self):
        for scenario in ("normal", "unloaded", "filter", "worn"):
            with self.subTest(scenario=scenario):
                result = setpoint_search(scenario)
                if result["recommended"]:
                    self.assertGreaterEqual(result["recommended"]["savings_kwh"], 0)
                    self.assertTrue(result["recommended"]["guardrails_pass"])

    def test_sequence_comparison_is_matched_and_consistent(self):
        result = compare_sequences()
        strategies = result["strategies"]
        self.assertEqual(set(strategies), {"A leads (efficient base-load)", "B leads (small base-load)"})
        self.assertGreater(result["saving_kwh_per_30min"], 0)
        self.assertEqual(result["recommended"], min(strategies, key=lambda k: strategies[k]["energy_kwh"]))

    def test_schedule_savings_math(self):
        result = schedule_savings(0.02, units_per_day=300, shiftable_pct=30, peak_rate=9.0,
                                  off_peak_rate=5.5, solar_rate=6.0, solar_share_pct=50)
        self.assertAlmostEqual(result["shifted_kwh_per_day"], 0.02 * 300 * 0.30, places=2)
        self.assertGreater(result["monthly_saving_inr"], 0)
        with self.assertRaises(ValueError):
            schedule_savings(-1)
        with self.assertRaises(ValueError):
            schedule_savings(0.02, shiftable_pct=130)

    def test_catalog_is_ranked_by_payback(self):
        result = intervention_catalog("leak")
        self.assertTrue(result["actions"])
        paybacks = [a["payback_days"] for a in result["actions"]]
        self.assertEqual(paybacks, sorted(paybacks))
        self.assertTrue(any(a["id"] == "repair-leak" for a in result["actions"]))

    def test_optimiser_endpoints(self):
        with app.test_client() as client:
            self.assertEqual(client.post("/api/optimise/setpoint", json={"scenario": "leak"}).status_code, 200)
            self.assertEqual(client.post("/api/optimise/setpoint", json={"scenario": "bad"}).status_code, 400)
            self.assertEqual(client.post("/api/optimise/sequence", json={}).status_code, 200)
            self.assertEqual(client.post("/api/schedule", json={}).status_code, 400)
            self.assertEqual(client.post("/api/catalog", json={"scenario": "filter"}).status_code, 200)
            sample = client.get("/api/connectivity/sample").json
            self.assertIn("mqtt", sample)
            self.assertIn("modbus", sample)
            run = client.post("/api/simulate", json={"scenario": "worn"}).json
            self.assertIn("ai_stage3", run)


class Stage3ModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle, message = load_stage3_bundle()
        if cls.bundle is None or not ai_stage3.STAGE3_REPORT_PATH.exists():
            raise unittest.SkipTest("Run train_stage3.py first to verify Stage 3 outputs. " + (message or ""))
        cls.report = json.loads(ai_stage3.STAGE3_REPORT_PATH.read_text(encoding="utf-8"))

    def test_features_are_finite_and_schema_sized(self):
        matrix, windows = extract_stage3_windows(run_simulation("leak")["readings"])
        self.assertEqual(matrix.shape, (59, len(STAGE3_FEATURE_NAMES)))
        self.assertTrue((matrix == matrix).all())
        self.assertEqual(len(windows), 59)

    def test_stage3_test_seeds_are_disjoint_from_stage2(self):
        stage2_test = set(range(9100, 9112)) | set(range(9200, 9212)) | set(range(9300, 9312))
        stage3_test = {seed for seeds in self.report["test_seeds"].values() for seed in seeds}
        self.assertFalse(stage2_test & stage3_test)
        self.assertFalse(stage3_test & set(self.report["calibration_seeds"]))

    def test_macro_f1_clears_the_validation_target(self):
        self.assertGreaterEqual(self.report["macro_f1"], 0.85)

    def test_event_detection_is_strong(self):
        events = self.report["event_detection"]
        self.assertGreaterEqual(events["detected"] / events["total"], 0.95)

    def test_normal_run_false_alarms_are_rare(self):
        rate = self.report["normal_run_alarm_episodes"] / self.report["normal_run_hours"]
        self.assertLessEqual(rate, 1.0)

    def test_demo_scenarios_are_named_correctly(self):
        for scenario in ("leak", "unloaded", "filter", "worn"):
            with self.subTest(scenario=scenario):
                inference = infer_stage3(run_simulation(scenario)["readings"])
                final = inference["windows"][-1]
                self.assertEqual(final["predicted_class"], scenario)
                self.assertTrue(final["is_fault"])
        normal = infer_stage3(run_simulation("normal")["readings"])
        self.assertFalse(any(w["is_fault"] for w in normal["windows"]))

    def test_status_and_report_endpoints(self):
        with app.test_client() as client:
            status = client.get("/api/stage3/status").json
            self.assertTrue(status["enabled"])
            response = client.get("/api/stage3/report")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json["macro_f1"], self.report["macro_f1"])
            self.assertIn("attachment", response.headers["Content-Disposition"])


if __name__ == "__main__":
    unittest.main()
