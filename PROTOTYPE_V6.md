# FactoryAir Twin — software prototype v7 (Vishwakarma Stage 2)

Everything below runs on **simulated physics** on one laptop, offline, unless
marked *real data*. No equipment is connected. Every number is reproduced by
`pytest` (124 tests).

## What is new in v7

| Feature | What it does | Where |
|---|---|---|
| **Virtual edge node + Live gateway** | Software twin of the clip-on box. Streams the exact firmware frames `{t, pressure_bar, power_kw, production}` through the real ingestion path. Operators end the shift, open a leak and inject faults (Wi-Fi dropout, stuck transmitter, impossible spike, clock jump); the screen shows frames generated → accepted/rejected → sensor health → twin alerts, a live 15-minute chart, and self-calibration on the live buffer. | `edge_emulator.py`, `static/gateway.js`, `/api/edge/emulator`, `/api/edge/live` |
| **Edge node firmware + hardware design** | ESP32-S3 sketch (ADS1115, 4–20 mA pressure, clip-on CT, machine-on contact, SD spooling, NTP) and wiring/BOM/safety notes. Not yet compiled or flashed. | `firmware/` |
| **Try it with your own data** | Upload any logger CSV (pressure + kW or motor current, optional timestamp/production). Idle windows picked from the production flag, the night, or the whole file; loaded/unloaded split learned per file (Otsu). Returns leak, ₹, storage, specific power, unloaded share, motor health. Sample file included. | `csv_import.py`, `/api/twin/upload`, `/api/twin/sample.csv` |
| **One-page owner report** | Print-ready A4 summary in English or Tamil (browser renders Tamil correctly; Print → Save as PDF). | `owner_report.py`, `/report/owner?lang=ta` |
| **Self-calibration on real data** | Runs the leak test on MetroPT-3 depot nights (real TP3 pressure + motor current, ~0.1 Hz) and checks whether documented-leak nights read higher. Leak reported as a fraction of capacity only (FAD unknown). | `metropt_selfcal.py`, Evidence screen |

Measured (simulated): live identification on the virtual node found leak
0.185 m³/min (true ≈ 0.18) and storage 0.65 m³ (true 0.62); the sample CSV
(5-s rows, 36 h) gives storage 0.64 m³, both nights identified, in 0.8 s.

Bugs fixed in v7: an O(n²) `max()` in the threshold finder (17 s → 0.8 s),
sensor health marking the absent temperature sensor "failed" (now "not
fitted"), and `field_publisher.py` posting to port 5000 instead of 5001.

## What is new in v6

| Feature | What it does | Where | Measured result (simulated) |
|---|---|---|---|
| **Self-calibrating twin** | Load/unload leak test on idle pressure + power. Two independent estimators (time ratio, slope ratio) of the same leak, plus the effective storage volume and specific power. Needs only the nameplate FAD. | `twin_id.py` | 30 unseen random plants: leak error **1.8 %** mean, storage error **1.9 %**, 30/30 within ±10 % |
| **Two-week plant twin** | Same receiver physics second by second over 14 days: two shifts, breaks, Saturday half-day, Sunday off, growing background leak, a failed coupling (Thu day 4), a repair (Thu day 11). | `longrun.py` | New leak flagged the same night (+₹5,580/month); repair verified overnight (−₹5,453/month); storage found 0.64 m³ vs 0.50 m³ tank nameplate (true 0.62) |
| **Leak Hunter** | Guided valve-isolation survey using the plant's existing branch valves: 2-minute decay tests, differenced to allocate leakage by zone. "New random plant" changes the hidden answer. | `leak_locator.py` | Dominant zone correct on every tested seed; per-zone error < 0.01 m³/min |
| **Zone repair verification** | A Leak Hunter ticket is verified by repeating the survey after repair (92 % repair efficacy assumed). | `stage4.py` | Plant #11: predicted ₹4,741 vs measured ₹4,276 per month (−9.8 %) |
| **Heat-recovery advisor** | ~75 % of compressor electricity is recoverable heat; sizes hot water and fuel displaced. | `advisor.py` | ≈761 L/day of 60 °C water, ₹7,414/month LPG, 8.8-month payback (editable defaults) |
| **VSD advisor** | Replays all 336 measured hours through a VSD power model. | `advisor.py` | 6.7 % saving, 159-month payback → the twin says **don't buy** |
| **Owner overview** | One screen: ₹ wasted per month, nightly leak index, what happened this fortnight, cheapest fix first, the operator's alert message. | `static/twin.js` | ₹4,767/month of electricity buys no useful air = 36 % of a ₹13,206 bill |

## Honest limits (say these before a judge asks)

* The identification assumes the nameplate FAD. A machine delivering 3 % below
  nameplate biases leak and volume by ~3 % — visible in the validation table.
* Leak Hunter assumes tight valve seats and known pipe volumes; real valves
  seep, so field surveys will be noisier than this simulation.
* Monthly ₹ on the overview come from the 14-day calendar. The live-twin
  optimiser cards still extrapolate one 30-minute run 24×7 and say so.
* Heat-recovery and VSD prices are editable defaults, not vendor quotes.
* Sequencing (two compressors) is shown as an option but is **not** counted in
  the overview, because the default site has one compressor.

## Bugs fixed while building v6

* `static/app.js` — a local variable named `t` shadowed the translate function
  in `draw()`, so rendering any alert threw `t is not a function`.
* `static/app.js` — `loadScenario()` ran before `LANG`/`EN_T` were initialised,
  so the dashboard's first scenario never loaded on page open.
* `.venv` — NumPy/SciPy DLLs were corrupted (OneDrive sync). Reinstalled.
  Tip: keep the virtual environment outside OneDrive, or mark it "Always keep
  on this device".

## Run

```powershell
.\.venv\Scripts\Activate.ps1
python train_model.py ; python train_stage3.py   # only after a fresh clone
python app.py                                    # http://127.0.0.1:5001
python -m pytest tests -q                        # 105 passed
python longrun.py                                # prints the 14-day twin + validation
```

## Demo script (6 minutes)

1. **Plant overview** — read the ₹ headline, point at the two events on the
   nightly leak chart, show the WhatsApp alert, toggle **தமிழ்**.
2. **Self-calibration** — "no flow meter": storage 0.64 m³ found, nightly table
   with two agreeing estimators, 30-plant validation scatter.
3. **Leak Hunter** — tick "show hidden true leaks", run the guided survey,
   watch valves open, Bay 2 = 58 %. Raise the ticket.
4. **Maintenance** — acknowledge → start repair → verify: measured vs predicted.
5. **Savings plan** — heat recovery pays back in 9 months; VSD: "not worth it".
6. **Live twin** — the original 30-minute replay with rules + Stage 2 + Stage 3.
7. **Evidence & limits** — MetroPT-3 real-data result and the limits above.

API additions: `GET /api/overview`, `GET /api/twin/longrun`,
`GET /api/twin/validation`, `POST /api/twin/identify` (`scenario` or `edge`),
`GET /api/leaks/layout`, `POST /api/leaks/test`, `POST /api/leaks/survey`,
`POST /api/advisor/heat`, `POST /api/advisor/vsd`.
