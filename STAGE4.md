# Stage 4 — Production-readiness layer (SIMULATED DATA)

Stage 4 adds eight features on top of the Stage 1–3 prototype. Everything still
runs on simulated data; no factory measurements exist. Money and carbon figures
are illustrative extrapolations of one 30-minute run to a 24×7 month.

## The eight features

| # | Feature | Where | API |
|---|---------|-------|-----|
| 1 | Plant setup & calibration panel | `stage4.get_config / update_config` | `GET/POST /api/stage4/config` |
| 2 | Sensor health monitoring | `stage4.sensor_health / corrupt_readings` | `POST /api/stage4/sensorhealth` |
| 3 | Unknown & multiple-fault handling | `stage4.decide_fault_state / diagnose_scenario` | `POST /api/stage4/diagnose` |
| 4 | Persistent maintenance workflow | `stage4.create_ticket / ticket_action / list_tickets` | `GET/POST /api/maintenance/tickets`, `POST /api/maintenance/tickets/<id>/action` |
| 5 | Measured post-repair verification | `stage4.verify_ticket` | `POST /api/maintenance/tickets/<id>/verify` |
| 6 | Combined-action savings (no double count) | `stage4.combined_savings` | `POST /api/optimise/combined` |
| 7 | Downloadable PDF report | `stage4.build_pdf_report` | `GET /api/report/pdf` |
| 8 | Stress-test mode | `stage4.run_stress` | `POST /api/stage4/stress` |

All new code lives in **stage4.py**; routes are appended at the bottom of
**app.py**; the dashboard section is the `STAGE 4 / PRODUCTION READINESS`
panel in `templates/index.html` + `static/app.js`.

## Design notes & honest limits

1. **Calibration panel** stores site values in `data/plant_config.json`.
   These feed Stage 4 health ranges, reports and the setpoint guardrail
   display. The *physics* still uses the simulator's illustrative constants —
   the panel is calibration metadata, not a re-tuned plant model.

2. **Sensor health** checks each signal for missing samples, timestamp gaps,
   stuck values and physically impossible ranges. Stuck detection is
   state-aware: zero power while Off and the constant unloaded idle power are
   legitimate, so power is only checked while Loaded; pressure is ignored
   while Off (slow decay below sensor resolution is normal).

3. **Fault-state logic** combines rule alerts, Stage 2 anomaly ratio and
   Stage 3 window predictions into one decision:
   - `known` — exactly one fault signature dominates
   - `multiple` — two or more overlap (repair the dominant one first, re-run)
   - `unknown` — anomalous but matches no known fault → manual investigation
   - `normal` — nothing found

4. **Workflow vocabulary is fixed**: acknowledge → start_repair →
   (verify) → resolve, plus reopen / cancel. Every change appends an operator
   log entry. Tickets persist in `data/maintenance.json`.

5. **Verification** re-runs the matched fault scenario before and after the
   *modelled* repair and reports measured kWh, ₹/month, CO₂e and the deviation
   versus the ticket's predicted savings. Repairs that the simulator cannot
   model (`filter`, `worn`) are honestly labelled `not_modelled` — real
   verification there needs field measurements.

6. **Combined savings**: `repair_leak` + `setpoint` are applied together in ONE
   re-simulation so their overlap is never counted twice. Sequencing and
   scheduling touch different mechanisms and are added separately from their
   own studies. The response reports the combined figure, the standalone sum
   and the double-counting note.

7. **PDF report** is generated on demand with `fpdf2` and labelled SIMULATED.
   Run `pip install fpdf2` (already in requirements.txt) before using it.

8. **Stress mode** injects deterministic corruptions (seeded): 5% dropouts,
   120 s stuck pressure transmitter, heavy power noise + spikes, a +25%
   demand jump (`run_simulation(..., demand_multiplier=1.25)`), and leak +
   filter at the same time (`run_simulation(..., secondary_fault="filter")`).

## Simulator changes (backward compatible)

`run_simulation()` gained two optional keyword arguments —
`secondary_fault` (`None`/`"filter"`/`"worn"`) and `demand_multiplier`
(0.1–3.0). Defaults reproduce Stage 3 behaviour exactly; all 34 original
tests still pass, and the trained models keep their published metrics
(macro-F1 85.9%).

## Tests

`tests/test_stage4.py` adds 30 checks: calibration validation & persistence,
health detection of stuck/missing/range/gap faults, fault-state decisions,
full ticket lifecycle + invalid transitions, measured verification,
combined-vs-standalone arithmetic, guardrails, stress determinism and PDF
generation. Full suite: **64 tests, all passing**.
