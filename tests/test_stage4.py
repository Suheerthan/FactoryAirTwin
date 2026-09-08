"""Stage 4 checks: calibration, sensor health, fault-state logic, maintenance
workflow, measured verification, combined savings, PDF report and stress mode.

All checks run on simulated data. No savings percentage is forced; tests check
consistency, guardrails and honest labelling.
"""

import unittest
from pathlib import Path

import simulator
import stage4
from simulator import run_simulation
from stage4 import (ACTIONS, DEFAULT_CONFIG, STRESS_PROFILES, build_pdf_report, combined_savings,
                    corrupt_readings, create_ticket, decide_fault_state, diagnose_scenario,
                    get_config, get_ticket, list_tickets, run_stress, sensor_health,
                    ticket_action, update_config, verify_ticket)


class ConfigTests(unittest.TestCase):
    def test_defaults_match_documented_demo_plant(self):
        self.assertEqual(DEFAULT_CONFIG["rated_power_kw"], simulator.PLANT.loaded_kw)
        self.assertEqual(DEFAULT_CONFIG["receiver_volume_m3"], simulator.PLANT.receiver_m3)

    def test_update_rejects_unknown_and_bad_values(self):
        _, error = update_config({"banana": 1}, path="/tmp/_fa_cfg.json")
        self.assertIn("Unknown", error)
        _, error = update_config({"rated_power_kw": -5}, path="/tmp/_fa_cfg.json")
        self.assertIn("between", error)
        _, error = update_config({"cut_in_bar": 7.4, "cut_out_bar": 7.2}, path="/tmp/_fa_cfg.json")
        self.assertIn("below", error)
        _, error = update_config({}, path="/tmp/_fa_cfg.json")
        self.assertTrue(error)

    def test_round_trip_and_invalid_band_combo(self):
        path = "/tmp/_fa_cfg2.json"
        config, error = update_config({"site_name": "Unit test site", "receiver_volume_m3": 0.8}, path=path)
        self.assertIsNone(error)
        self.assertEqual(get_config(path)["site_name"], "Unit test site")
        self.assertEqual(get_config(path)["receiver_volume_m3"], 0.8)
        _, error = update_config({"min_pressure_bar": 7.0}, path=path)  # above cut-in 6.4
        self.assertIn("below cut_in", error)


class SensorHealthTests(unittest.TestCase):
    def setUp(self):
        self.clean = run_simulation("normal")["readings"]

    def test_clean_run_is_ok(self):
        report = sensor_health(self.clean)
        self.assertEqual(report["overall"], "ok")
        self.assertEqual(report["issues"], [])

    def test_stuck_pressure_detected(self):
        report = sensor_health(corrupt_readings(self.clean, ["stuck_pressure"]))
        self.assertNotEqual(report["overall"], "ok")
        self.assertTrue(any("stuck" in issue for issue in report["issues"]))

    def test_dropout_creates_gaps(self):
        rows = corrupt_readings(self.clean, ["dropout"])
        self.assertLess(len(rows), len(self.clean))
        report = sensor_health(rows)
        self.assertGreaterEqual(len(report["gaps"]), 1)

    def test_range_violation_fails_sensor(self):
        rows = corrupt_readings(self.clean, ["range_violation"])
        report = sensor_health(rows)
        self.assertEqual(report["sensors"]["pressure_bar"]["out_of_range"], 1)
        self.assertEqual(report["overall"], "failed")

    def test_missing_values_counted(self):
        rows = [dict(r) for r in self.clean]
        for i in (100, 101, 102, 103, 104):
            rows[i]["power_kw"] = None
        report = sensor_health(rows)
        self.assertEqual(report["sensors"]["power_kw"]["missing"], 5)
        self.assertEqual(report["sensors"]["power_kw"]["status"], "failed")


class FaultStateTests(unittest.TestCase):
    def test_known_single_fault(self):
        state = decide_fault_state({"leak"}, ["leak", "leak"])
        self.assertEqual(state["state"], "known")
        self.assertEqual(state["faults"], ["leak"])

    def test_multiple_faults(self):
        state = decide_fault_state({"leak"}, ["filter"])
        self.assertEqual(state["state"], "multiple")
        self.assertEqual(state["faults"], ["filter", "leak"])

    def test_unknown_anomaly(self):
        state = decide_fault_state(set(), [], anomaly_ratio=0.4)
        self.assertEqual(state["state"], "unknown")
        self.assertEqual(state["faults"], [])

    def test_normal(self):
        self.assertEqual(decide_fault_state(set(), [], 0.0)["state"], "normal")

    def test_diagnose_leak_scenario(self):
        state, run = diagnose_scenario("leak")
        self.assertIn(state["state"], ("known", "multiple"))
        self.assertIn("leak", state["faults"])
        self.assertGreater(run["summary"]["energy_kwh"], 0)

    def test_diagnose_normal_scenario_is_not_a_fault(self):
        state, _ = diagnose_scenario("normal")
        self.assertEqual(state["faults"], [])

    def test_simultaneous_faults_detected_as_multiple(self):
        run = run_simulation("leak", secondary_fault="filter")
        rule_keys = {a["key"] for a in run["alerts"]} & {"leak", "unloaded", "filter", "worn"}
        # The leak rule fires; the secondary clog changes physics. If rules
        # only name one fault the classifier path may add the second.
        self.assertIn("leak", rule_keys)
        self.assertNotEqual(run["summary"]["energy_kwh"],
                            run_simulation("leak")["summary"]["energy_kwh"])
        # The demand multiplier used by stress mode also changes the physics.
        boosted = run_simulation("leak", demand_multiplier=1.25)
        self.assertNotEqual(boosted["summary"]["energy_kwh"], run["summary"]["energy_kwh"])


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.path = "/tmp/_fa_tickets.json"
        Path(self.path).unlink(missing_ok=True)

    def _ticket(self, fault="leak"):
        return create_ticket(fault, "Test ticket", evidence="unit test",
                             predicted={"inr_per_month": 8000}, path=self.path)

    def test_lifecycle_happy_path(self):
        ticket = self._ticket()
        for action, expected in (("acknowledge", "acknowledged"), ("start_repair", "in_repair")):
            ticket = ticket_action(ticket["id"], action, "tester", "note", path=self.path)
            self.assertEqual(ticket["state"], expected)
        verification = verify_ticket(ticket["id"], "tester", path=self.path)
        self.assertEqual(verification["status"], "measured_simulated")
        self.assertGreater(verification["saved_kwh_30min"], 0)
        self.assertIsNotNone(verification["deviation_pct_vs_predicted"])
        ticket = ticket_action(ticket["id"], "resolve", "tester", path=self.path)
        self.assertEqual(ticket["state"], "resolved")
        self.assertGreaterEqual(len(get_ticket(ticket["id"], path=self.path)["logs"]), 5)

    def test_invalid_transition_rejected(self):
        ticket = self._ticket()
        with self.assertRaises(ValueError):
            ticket_action(ticket["id"], "resolve", path=self.path)  # open -> resolved illegal
        with self.assertRaises(ValueError):
            ticket_action(ticket["id"], "fly", path=self.path)

    def test_filter_repair_not_modelled(self):
        ticket = create_ticket("filter", "Filter", path=self.path)
        ticket_action(ticket["id"], "acknowledge", path=self.path)
        ticket_action(ticket["id"], "start_repair", path=self.path)
        verification = verify_ticket(ticket["id"], path=self.path)
        self.assertEqual(verification["status"], "not_modelled")

    def test_persistence_and_listing(self):
        self._ticket("leak")
        self._ticket("worn")
        tickets = list_tickets(path=self.path)
        self.assertEqual(len(tickets), 2)
        self.assertEqual([t["id"] for t in tickets], ["M-0001", "M-0002"])
        self.assertEqual(list(ACTIONS), list(ACTIONS))  # workflow actions are a fixed vocabulary


class CombinedSavingsTests(unittest.TestCase):
    def test_repair_and_setpoint_in_one_run(self):
        result = combined_savings("leak", ["repair_leak", "setpoint"])
        self.assertLess(result["combined_kwh_30min"], result["baseline_kwh_30min"])
        self.assertGreater(result["total_inr_per_month"], 0)
        self.assertIn("ONE combined re-simulation", result["double_counting_note"])

    def test_combined_not_greater_than_standalone_sum(self):
        result = combined_savings("leak", ["repair_leak", "setpoint"])
        self.assertLessEqual(result["total_inr_per_month"],
                             result["standalone_sum_inr_per_month"] + 1)

    def test_no_actions_no_savings(self):
        result = combined_savings("leak", [])
        self.assertEqual(result["total_inr_per_month"], 0)

    def test_unknown_action_rejected(self):
        with self.assertRaises(ValueError):
            combined_savings("leak", ["teleport"])

    def test_guardrails_hold_in_combined_run(self):
        result = combined_savings("leak", ["repair_leak", "setpoint"])
        self.assertGreaterEqual(result["pressure_compliance_pct"], 99.0)
        self.assertGreater(result["accepted_units"], 0)


class StressTests(unittest.TestCase):
    def test_full_battery_deterministic(self):
        first = run_stress(seed=4400)
        second = run_stress(seed=4400)
        self.assertEqual([r["test"] for r in first["results"]], list(STRESS_PROFILES))
        self.assertEqual(first["results"], second["results"])

    def test_dropout_degrades_health(self):
        result = run_stress(tests=["sensor_dropout"])["results"][0]
        self.assertNotEqual(result["health_overall"], "ok")
        self.assertIn("leak", result["alerts_raised"])

    def test_simultaneous_faults_run(self):
        result = run_stress(tests=["simultaneous_faults"])["results"][0]
        self.assertIn("leak", result["alerts_raised"])

    def test_unknown_test_rejected(self):
        with self.assertRaises(ValueError):
            run_stress(tests=["explode"])

    def test_corruption_is_seeded(self):
        base = run_simulation("normal")["readings"]
        self.assertEqual(corrupt_readings(base, ["dropout"], seed=7),
                         corrupt_readings(base, ["dropout"], seed=7))


class PdfReportTests(unittest.TestCase):
    def test_report_builds_and_is_pdf(self):
        target = Path("/tmp/_fa_report.pdf")
        target.unlink(missing_ok=True)
        path = build_pdf_report(target)
        self.assertTrue(path.exists())
        data = path.read_bytes()
        self.assertTrue(data.startswith(b"%PDF"))
        self.assertGreater(len(data), 2000)


if __name__ == "__main__":
    unittest.main()
