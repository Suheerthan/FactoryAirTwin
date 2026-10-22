"""Capex advisors that reuse the two-week twin's measured load profile.

* Heat recovery - roughly three quarters of a compressor's electrical input
  leaves as recoverable heat in the oil cooler. A plate heat exchanger can
  pre-heat process / boiler-feed / canteen water and displace LPG, diesel or
  electric heating.
* Variable-speed (VSD) upgrade - replays every hour of the measured profile
  through a VSD power model and compares with the fixed-speed load/unload
  energy the twin actually measured.

Every price, efficiency and fraction is an editable illustrative default,
returned with the result so nothing is hidden.
"""

from simulator import GRID_CO2_KG_PER_KWH, PLANT

FUELS = {
    # useful kWh per unit after a typical heater efficiency, ₹ per unit, kg CO2 per unit
    "lpg": {"label": "LPG (commercial)", "unit": "kg", "kwh_per_unit": 12.8, "efficiency": 0.85,
            "inr_per_unit": 95.0, "co2_per_unit": 2.98},
    "diesel": {"label": "Diesel boiler", "unit": "litre", "kwh_per_unit": 10.0, "efficiency": 0.82,
               "inr_per_unit": 92.0, "co2_per_unit": 2.68},
    "electric": {"label": "Electric heater", "unit": "kWh", "kwh_per_unit": 1.0, "efficiency": 0.97,
                 "inr_per_unit": 7.0, "co2_per_unit": GRID_CO2_KG_PER_KWH},
}


def heat_recovery(monthly_kwh, fuel="lpg", recoverable_pct=75.0, usable_pct=60.0,
                  inlet_c=28.0, outlet_c=60.0, fuel_price=None, kit_cost_inr=65000.0):
    if fuel not in FUELS:
        raise ValueError("fuel must be lpg, diesel or electric.")
    for name, value, lo, hi in (("recoverable_pct", recoverable_pct, 0, 95), ("usable_pct", usable_pct, 0, 100),
                                ("monthly_kwh", monthly_kwh, 0, 1e7), ("kit_cost_inr", kit_cost_inr, 0, 1e8)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not lo <= value <= hi:
            raise ValueError(f"{name} must be between {lo} and {hi}.")
    if not 0 <= inlet_c < outlet_c <= 90:
        raise ValueError("Water temperatures must satisfy 0 ≤ inlet < outlet ≤ 90 °C.")
    f = dict(FUELS[fuel])
    if fuel_price is not None:
        if isinstance(fuel_price, bool) or not isinstance(fuel_price, (int, float)) or not 0 < fuel_price < 1000:
            raise ValueError("fuel_price must be a positive number.")
        f["inr_per_unit"] = float(fuel_price)
    heat_kwh = monthly_kwh * recoverable_pct / 100 * usable_pct / 100
    litres_day = heat_kwh / 30 * 3600 / (4.186 * (outlet_c - inlet_c))
    fuel_units = heat_kwh / (f["kwh_per_unit"] * f["efficiency"])
    saving = fuel_units * f["inr_per_unit"]
    return {
        "recovered_heat_kwh_month": round(heat_kwh), "hot_water_litres_per_day": round(litres_day),
        "water_c": [inlet_c, outlet_c], "fuel": f["label"],
        "fuel_displaced_per_month": round(fuel_units, 1), "fuel_unit": f["unit"],
        "inr_per_month": round(saving), "co2_kg_month": round(fuel_units * f["co2_per_unit"]),
        "kit_cost_inr": kit_cost_inr,
        "payback_months": round(kit_cost_inr / saving, 1) if saving > 0 else None,
        "assumptions": {"recoverable_pct": recoverable_pct, "usable_pct": usable_pct,
                        "fuel_price_inr": f["inr_per_unit"], "heater_efficiency": f["efficiency"]},
        "note": "Plate heat exchanger on the oil circuit. Usable % = share of recovered heat the plant can actually use.",
    }


def vsd_upgrade(hourly, days, tariff=7.0, fad_nominal=PLANT.rated_flow_m3_min,
                rated_kw=PLANT.loaded_kw, min_speed_pct=30.0, drive_loss_pct=3.0,
                upgrade_cost_inr=140000.0):
    """Replay each measured hour through a VSD power model.

    Delivered air per hour is recovered from the loaded fraction (pressure +
    power only). A VSD follows that flow down to its minimum speed, below
    which it start/stops at minimum speed. Its specific power is taken equal
    to the fixed-speed loaded value plus drive losses - a conservative model.
    """
    if not hourly:
        raise ValueError("Run the two-week twin first.")
    if not 10 <= min_speed_pct <= 60:
        raise ValueError("min_speed_pct must be between 10 and 60.")
    sp = rated_kw / fad_nominal * (1 + drive_loss_pct / 100)
    fixed = vsd = 0.0
    profile = []
    for h in hourly:
        q = h["loaded_fraction"] * fad_nominal  # m³/min average over the hour
        kw_vsd = q * sp  # linear above min speed; start/stop below keeps the same average
        fixed += h["kwh"]
        vsd += kw_vsd
        profile.append({"hour": h["hour"], "flow_pct": round(100 * h["loaded_fraction"], 1),
                        "fixed_kwh": h["kwh"], "vsd_kwh": round(kw_vsd, 3)})
    scale = 30.0 / days
    saving_kwh = (fixed - vsd) * scale
    inr = saving_kwh * tariff
    return {
        "fixed_speed_kwh_month": round(fixed * scale), "vsd_kwh_month": round(vsd * scale),
        "saving_kwh_month": round(saving_kwh), "inr_per_month": round(inr),
        "co2_kg_month": round(saving_kwh * GRID_CO2_KG_PER_KWH),
        "saving_pct": round(100 * (fixed - vsd) / fixed, 1) if fixed else None,
        "upgrade_cost_inr": upgrade_cost_inr,
        "payback_months": round(upgrade_cost_inr / inr, 1) if inr > 0 else None,
        "mean_flow_pct": round(100 * sum(h["loaded_fraction"] for h in hourly) / len(hourly), 1),
        "profile_by_hour_of_day": _by_hod(profile),
        "assumptions": {"min_speed_pct": min_speed_pct, "drive_loss_pct": drive_loss_pct,
                        "specific_power_kw_per_m3_min": round(sp, 3)},
        "note": ("VSD saving comes from removing unloaded running and pressure-band overshoot. If most "
                 "hours already run near full load, the twin will say a VSD is not worth it."),
    }


def _by_hod(profile):
    buckets = {}
    for row in profile:
        hod = row["hour"] % 24
        b = buckets.setdefault(hod, [0.0, 0.0, 0.0, 0])
        b[0] += row["flow_pct"]; b[1] += row["fixed_kwh"]; b[2] += row["vsd_kwh"]; b[3] += 1
    return [{"hod": h, "flow_pct": round(b[0] / b[3], 1), "fixed_kwh": round(b[1] / b[3], 3),
             "vsd_kwh": round(b[2] / b[3], 3)} for h, b in sorted(buckets.items())]


def pressure_opportunity(mean_header_bar, lowest_needed_bar, monthly_kwh, tariff=7.0, margin_bar=0.3):
    """Rule of thumb (BEE / DOE): ~7 % compressor energy per bar of excess
    discharge pressure, plus leakage falls in proportion to pressure."""
    excess = max(0.0, mean_header_bar - (lowest_needed_bar + margin_bar))
    pct = 7.0 * excess
    kwh = monthly_kwh * pct / 100
    return {"excess_bar": round(excess, 2), "saving_pct": round(pct, 1), "kwh_month": round(kwh),
            "inr_per_month": round(kwh * tariff), "co2_kg_month": round(kwh * GRID_CO2_KG_PER_KWH),
            "basis": "≈7 % energy per bar of excess pressure (BEE/DOE rule of thumb), after a safety margin."}


def opportunity_ladder(longrun, tariff=7.0):
    """Everything the owner could save, ranked, from one two-week twin."""
    s = longrun["summary"]
    items = []
    if s.get("latest_leak_inr_month") is not None:
        items.append({"key": "leak", "title": "Fix the remaining leaks", "kind": "electricity",
                      "inr_per_month": s["latest_leak_inr_month"], "cost_inr": 8000,
                      "detail": f"Last night's test: {s['latest_leak_pct']}% of capacity still leaks. "
                                "Leak Hunter finds the zone; the nightly test verifies the repair."})
    items.append({"key": "unloaded", "title": "Stop unloaded running", "kind": "electricity",
                  "inr_per_month": s["unloaded_inr_month"], "cost_inr": 0,
                  "detail": f"{s['unloaded_share_pct']}% of electricity buys no air. Shorten the stop timer."})
    po = pressure_opportunity(s["mean_header_bar_in_production"], PLANT.minimum_bar, s["monthly_kwh"], tariff)
    items.append({"key": "pressure", "title": "Lower the pressure band", "kind": "electricity",
                  "inr_per_month": po["inr_per_month"], "cost_inr": 0,
                  "detail": f"Header averages {s['mean_header_bar_in_production']} bar in production; "
                            f"{po['excess_bar']} bar above need + margin."})
    hr = heat_recovery(s["monthly_kwh"])
    items.append({"key": "heat", "title": "Recover compressor heat", "kind": "fuel",
                  "inr_per_month": hr["inr_per_month"], "cost_inr": hr["kit_cost_inr"],
                  "detail": f"≈{hr['hot_water_litres_per_day']:,} L/day of 60 °C water replacing LPG."})
    vs = vsd_upgrade(longrun["hourly"], longrun["summary"]["days"], tariff)
    items.append({"key": "vsd", "title": "Variable-speed compressor", "kind": "electricity",
                  "inr_per_month": vs["inr_per_month"], "cost_inr": vs["upgrade_cost_inr"],
                  "detail": f"Average demand {vs['mean_flow_pct']}% of capacity."})
    for it in items:
        it["payback_months"] = (round(it["cost_inr"] / it["inr_per_month"], 1)
                                if it["cost_inr"] and it["inr_per_month"] > 0 else (0 if it["inr_per_month"] > 0 else None))
    items.sort(key=lambda it: (it["payback_months"] is None, it["payback_months"] or 0, -it["inr_per_month"]))
    electricity = sum(it["inr_per_month"] for it in items if it["kind"] == "electricity" and it["key"] != "vsd")
    fuel = sum(it["inr_per_month"] for it in items if it["kind"] == "fuel")
    return {"items": items, "electricity_inr_month": electricity, "fuel_inr_month": fuel,
            "electricity_share_of_bill_pct": round(100 * electricity / s["monthly_cost_inr"], 1)
            if s["monthly_cost_inr"] else None,
            "note": ("Electricity items are indicative and overlap slightly (lower pressure also shrinks leaks); "
                     "a VSD would replace the unloaded-running fix, so it is not added. Heat recovery saves "
                     "fuel, not electricity, and is shown separately.")}
