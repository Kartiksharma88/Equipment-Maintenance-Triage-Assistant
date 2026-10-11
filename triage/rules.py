"""Deterministic threshold checks + missing/conflicting data analysis."""
from __future__ import annotations

from config import PRESSURE_ENVELOPES, SENSOR_SCHEMA


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def threshold_checks(sensor: dict, equipment_type: str) -> list[dict]:
    """Pure deterministic rules. Returns list of {rule_id, sensor, value, severity, message, citation}."""
    s = sensor or {}
    out: list[dict] = []
    t = _num(s.get("temperature_c"))
    if t is not None:
        if t > 95:
            out.append({"rule_id": "TEMP_CRIT", "sensor": "temperature_c", "value": t,
                        "severity": "critical", "message": f"Temperature {t}°C exceeds 95°C critical limit — stop and inspect.",
                        "citation": "KB-PUMP-03 / KB-COMP-02"})
        elif t > 85:
            out.append({"rule_id": "TEMP_WARN", "sensor": "temperature_c", "value": t,
                        "severity": "warn", "message": f"Temperature {t}°C exceeds 85°C warning limit.",
                        "citation": "KB-PUMP-03 / KB-COMP-02"})
    v = _num(s.get("vibration_mm_s"))
    if v is not None:
        if v > 7.1:
            out.append({"rule_id": "VIB_CRIT", "sensor": "vibration_mm_s", "value": v,
                        "severity": "critical", "message": f"Vibration {v} mm/s exceeds 7.1 mm/s critical limit — do not run.",
                        "citation": "KB-PUMP-02 / KB-FAN-02"})
        elif v > 4.5:
            out.append({"rule_id": "VIB_WARN", "sensor": "vibration_mm_s", "value": v,
                        "severity": "warn", "message": f"Vibration {v} mm/s exceeds 4.5 mm/s warning limit.",
                        "citation": "KB-PUMP-02 / KB-FAN-02"})
    oil = _num(s.get("oil_level_pct"))
    if oil is not None:
        if oil < 15:
            out.append({"rule_id": "OIL_LOW_CRIT", "sensor": "oil_level_pct", "value": oil,
                        "severity": "critical", "message": f"Oil level {oil}% is critically low (<15%). Do not run under load.",
                        "citation": "KB-PUMP-03 / KB-COMP-03"})
        elif oil < 30:
            out.append({"rule_id": "OIL_LOW_WARN", "sensor": "oil_level_pct", "value": oil,
                        "severity": "warn", "message": f"Oil level {oil}% is low (<30%). Top up and check for leaks.",
                        "citation": "KB-PUMP-03 / KB-COMP-03"})
    p = _num(s.get("pressure_bar"))
    if p is not None:
        lo, hi = PRESSURE_ENVELOPES.get(equipment_type, (0.0, 25.0))
        if p > hi:
            out.append({"rule_id": "PRESS_HIGH", "sensor": "pressure_bar", "value": p,
                        "severity": "warn", "message": f"Pressure {p} bar above rated {hi} bar for {equipment_type}. Check blockage/overcharge.",
                        "citation": "KB-PUMP-04 / KB-COMP-04"})
        elif p < lo and p >= 0:
            # pressure 0 with running machine is suspicious but still a warn
            out.append({"rule_id": "PRESS_LOW", "sensor": "pressure_bar", "value": p,
                        "severity": "warn", "message": f"Pressure {p} bar below minimum {lo} bar for {equipment_type}. Check leaks/cavitation/slip.",
                        "citation": "KB-PUMP-04 / KB-COMP-04"})
    cur = _num(s.get("current_a"))
    if cur is not None and cur > 200:
        out.append({"rule_id": "CURRENT_HIGH", "sensor": "current_a", "value": cur,
                    "severity": "warn", "message": f"Motor current {cur} A is high — check overload, binding, voltage.",
                    "citation": "KB-GEN-03 / KB-CONV-03"})
    elif cur is not None and cur > 50 and equipment_type in ("Conveyor Belt", "HVAC Unit", "Industrial Fan"):
        out.append({"rule_id": "CURRENT_HIGH", "sensor": "current_a", "value": cur,
                    "severity": "warn", "message": f"Motor current {cur} A is high for {equipment_type} — check overload/binding.",
                    "citation": "KB-GEN-03 / KB-CONV-03"})
    nz = _num(s.get("noise_db"))
    if nz is not None and nz > 90:
        out.append({"rule_id": "NOISE_HIGH", "sensor": "noise_db", "value": nz,
                    "severity": "warn", "message": f"Noise {nz} dB exceeds 90 dB — cavitation/bearing/belt distress possible.",
                    "citation": "KB-PUMP-02 / KB-CONV-02"})
    rt = _num(s.get("runtime_hours"))
    if rt is not None and rt > 4000:
        out.append({"rule_id": "SERVICE_OVERDUE", "sensor": "runtime_hours", "value": rt,
                    "severity": "warn", "message": f"Runtime {rt} h exceeds 4000 h service interval — preventive maintenance overdue.",
                    "citation": "KB-PUMP-05 (PM-4000H)"})
    return out


HOT_WORDS = ("hot", "overheat", "overheating", "burning", "smoke", "smell", "thermal", "trip")
VIB_WORDS = ("vibrat", "shak", "rattl", "rumble", "grind")
LEAK_WORDS = ("leak", "drip", "seep", "oil spot", "puddle")
NOISE_WORDS = ("noise", "noisy", "loud", "squeal", "screech", "grinding", "knock")


def data_quality(sensor: dict, issue: str, events: str) -> dict:
    """Detect missing sensors, invalid values, and conflicts between text and readings."""
    sensor = sensor or {}
    text = f"{issue or ''} {events or ''}".lower()
    missing = [k for k in SENSOR_SCHEMA if sensor.get(k) in (None, "")]
    invalid = []
    for k, v in sensor.items():
        if k in SENSOR_SCHEMA and v not in (None, ""):
            try:
                f = float(v)
            except (TypeError, ValueError):
                invalid.append({"sensor": k, "value": v, "message": f"'{k}' is not a number."})
                continue
            meta = SENSOR_SCHEMA[k]
            if f < meta["min"] or f > meta["max"]:
                invalid.append({"sensor": k, "value": f,
                                "message": f"'{k}'={f} outside plausible range {meta['min']}..{meta['max']} {meta['unit']}."})
    conflicts: list[dict] = []
    t = _num(sensor.get("temperature_c"))
    if t is not None and t < 70 and any(w in text for w in HOT_WORDS):
        conflicts.append({"type": "conflict", "sensors": ["temperature_c"],
                          "message": "Description mentions heat/smoke but temperature reading is normal (<70°C). Sensor may be misplaced, stale, or the hot spot is elsewhere — verify with IR survey."})
    if t is not None and t > 85 and not any(w in text for w in HOT_WORDS):
        conflicts.append({"type": "conflict", "sensors": ["temperature_c"],
                          "message": "Temperature reading is high but description does not mention heat. Confirm sensor location and units (°C vs °F)."})
    vib = _num(sensor.get("vibration_mm_s"))
    if vib is not None and vib < 2.0 and any(w in text for w in VIB_WORDS):
        conflicts.append({"type": "conflict", "sensors": ["vibration_mm_s"],
                          "message": "Description mentions vibration/shaking but reading is low (<2 mm/s). Check axis placement and whether reading was taken under load."})
    oil = _num(sensor.get("oil_level_pct"))
    if oil is not None and oil > 50 and any(w in text for w in LEAK_WORDS):
        conflicts.append({"type": "conflict", "sensors": ["oil_level_pct"],
                          "message": "Description mentions a leak but oil level reads normal (>50%). Leak may be water/condensate/seal drain rather than oil — identify fluid."})
    nz = _num(sensor.get("noise_db"))
    if nz is not None and nz < 75 and any(w in text for w in NOISE_WORDS):
        conflicts.append({"type": "conflict", "sensors": ["noise_db"],
                          "message": "Description mentions abnormal noise but dB reading is normal. dB alone does not capture tone (grinding/squeal) — record audio note and frequency if possible."})
    return {"missing": missing, "invalid": invalid, "conflicts": conflicts}
