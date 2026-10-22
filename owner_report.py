"""One-page monthly owner report (English / Tamil), print-ready HTML.

Rendered by the browser instead of the PDF library because Tamil script needs
proper text shaping; "Print → Save as PDF" gives an A4 page. Every figure
comes from the same two-week twin, opportunity ladder and ticket store the
dashboard uses.
"""

TEXT = {
    "en": {
        "title": "Compressed-air report", "sim": "SIMULATED DATA · prototype",
        "period": "Last 14 days", "headline": "of electricity a month buys no useful air",
        "share": "{0}% of the ₹{1} monthly compressor bill.",
        "bill": "Compressor bill / month", "leak_now": "Leaks now (last night)", "verified": "Repair saving, measured",
        "fuel": "Fuel saving available", "events": "What happened", "actions": "Do these next",
        "free": "free", "payback": "payback {0} months", "no_cost": "no cost", "skip": "not recommended",
        "trend": "Nightly leak test, % of capacity", "tickets": "Repairs",
        "tickets_line": "{0} open · {1} resolved and verified",
        "method": ("Method: every night 23:00-05:00 the twin runs a load/unload leak test on header pressure "
                   "and motor power only, and learns this plant's storage volume and specific power. "
                   "No flow meter is used. Figures are simulated until a site pilot."),
        "print": "Print / save as PDF", "lang": "தமிழ்",
        "new_leak": "New leak detected overnight", "leak_reduced": "Leak reduction confirmed overnight",
        "night": "Night of {0} {1}", "per_month": "/month",
        "items": {"leak": "Fix the remaining leaks", "unloaded": "Stop unloaded running", "pressure": "Lower the pressure band",
                  "heat": "Recover compressor heat", "vsd": "Variable-speed compressor"},
        "days": {"Mon": "Mon", "Tue": "Tue", "Wed": "Wed", "Thu": "Thu", "Fri": "Fri", "Sat": "Sat", "Sun": "Sun"},
    },
    "ta": {
        "title": "அழுத்தக் காற்று அறிக்கை", "sim": "உருவகத் தரவு · முன்மாதிரி",
        "period": "கடந்த 14 நாட்கள்", "headline": "மாதம் மின்சாரம் பயனுள்ள காற்றைத் தரவில்லை",
        "share": "மாதாந்திர ₹{1} அமுக்கி கட்டணத்தில் {0}%.",
        "bill": "அமுக்கி கட்டணம் / மாதம்", "leak_now": "தற்போதைய கசிவு (நேற்றிரவு)", "verified": "பழுது சேமிப்பு, அளக்கப்பட்டது",
        "fuel": "எரிபொருள் சேமிப்பு வாய்ப்பு", "events": "என்ன நடந்தது", "actions": "அடுத்து செய்ய வேண்டியவை",
        "free": "இலவசம்", "payback": "மீட்பு {0} மாதம்", "no_cost": "செலவில்லை", "skip": "பரிந்துரைக்கப்படவில்லை",
        "trend": "இரவுக் கசிவுச் சோதனை, கொள்ளளவின் %", "tickets": "பழுதுகள்",
        "tickets_line": "{0} திறந்தவை · {1} தீர்க்கப்பட்டு உறுதிசெய்யப்பட்டவை",
        "method": ("முறை: ஒவ்வொரு இரவும் 23:00-05:00 வரை அழுத்தம் மற்றும் மோட்டார் மின்திறனை மட்டும் கொண்டு "
                   "சுமை/சுமையற்ற கசிவுச் சோதனை செய்து, இந்த ஆலையின் சேமிப்புக் கொள்ளளவையும் தனித்திறனையும் கற்கிறது. "
                   "ஓட்டமானி பயன்படுத்தப்படவில்லை. கள சோதனை வரை எண்கள் உருவகமானவை."),
        "print": "அச்சிடு / PDF ஆக சேமி", "lang": "English",
        "new_leak": "இரவில் புதிய கசிவு கண்டறியப்பட்டது", "leak_reduced": "இரவில் கசிவு குறைவு உறுதிசெய்யப்பட்டது",
        "night": "{0} {1} இரவு", "per_month": "/மாதம்",
        "items": {"leak": "மீதமுள்ள கசிவுகளை சரிசெய்", "unloaded": "சுமையற்ற இயக்கத்தை நிறுத்து",
                  "pressure": "அழுத்த வரம்பைக் குறை", "heat": "அமுக்கி வெப்பத்தை மீட்டெடு", "vsd": "மாறும்-வேக அமுக்கி"},
        "days": {"Mon": "திங்", "Tue": "செவ்", "Wed": "புத", "Thu": "வியா", "Fri": "வெள்", "Sat": "சனி", "Sun": "ஞாயி"},
    },
}


def _spark(daily, width=520, height=120):
    """Inline SVG points for the nightly leak index (no JS needed to print)."""
    pts = [(d["day"], d["leak_pct_tonight"]) for d in daily if d.get("leak_pct_tonight") is not None]
    if not pts:
        return {"path": "", "dots": [], "max": 0}
    top = int(max(5, -(-max(v for _, v in pts) // 5) * 5))
    x = lambda i: 40 + i / 13 * (width - 50)
    y = lambda v: height - 18 - v / top * (height - 30)
    path = " ".join(f"{'L' if k else 'M'}{x(i):.1f},{y(v):.1f}" for k, (i, v) in enumerate(pts))
    return {"path": path, "dots": [{"x": round(x(i), 1), "y": round(y(v), 1), "v": v} for i, v in pts],
            "max": top, "y0": round(y(0), 1), "ymax": round(y(top), 1), "width": width, "height": height}


def build_context(overview, tickets, lang="en"):
    if lang not in TEXT:
        lang = "en"
    L = TEXT[lang]
    s, ladder = overview["summary"], overview["ladder"]
    events = []
    for e in overview["events"]:
        d = overview["daily"][e["night"]]
        events.append({"good": e["kind"] == "leak_reduced", "title": L[e["kind"]],
                       "when": L["night"].format(L["days"][d["weekday"]], e["night"] + 1),
                       "inr": e["inr_per_month"], "delta": abs(e["delta_m3_min"])})
    actions = []
    for item in ladder["items"]:
        pay = item.get("payback_months")
        skip = pay is not None and pay > 36
        actions.append({"title": L["items"].get(item["key"], item["title"]), "inr": item["inr_per_month"],
                        "fuel": item["kind"] == "fuel", "skip": skip,
                        "pay": L["skip"] if skip else (L["no_cost"] if pay == 0 else L["payback"].format(pay))})
    verified = next((e["inr_per_month"] for e in overview["events"] if e["kind"] == "leak_reduced"), None)
    return {"L": L, "lang": lang, "site": overview.get("site") or "Compressor room 01",
            "waste": ladder["electricity_inr_month"],
            "share": L["share"].format(round(ladder["electricity_share_of_bill_pct"] or 0), f"{s['monthly_cost_inr']:,}"),
            "bill": s["monthly_cost_inr"], "leak_pct": s["latest_leak_pct"], "verified": verified,
            "fuel": ladder["fuel_inr_month"], "events": events, "actions": actions,
            "spark": _spark(overview["daily"]),
            "tickets_line": L["tickets_line"].format(sum(t["state"] not in ("resolved", "cancelled") for t in tickets),
                                                     sum(t["state"] == "resolved" for t in tickets)),
            "identified": overview["identified"]}
