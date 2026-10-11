"""Central configuration: deterministic thresholds, equipment types, sensor schema."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv() -> None:
    """Minimal .env loader (stdlib): KEY=VALUE lines, # comments, no overrides."""
    try:
        env_file = BASE_DIR / ".env"
        if not env_file.exists():
            return
        for raw in env_file.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip("\"'")
            if key and key not in os.environ:
                os.environ[key] = val
    except OSError:
        pass


_load_dotenv()


def data_dir() -> Path:
    """Writable directory for SQLite DB, knowledge-base JSON and log files.

    Overridable with DATA_DIR (used by deployment + tests). Resolved per call
    so tests can redirect it with monkeypatched env vars.
    """
    d = Path(os.environ.get("DATA_DIR", BASE_DIR / "data"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def server_config() -> dict:
    return {"host": os.environ.get("HOST", "127.0.0.1"),
            "port": int(os.environ.get("PORT", "5000")),
            "debug": os.environ.get("FLASK_DEBUG", "1") == "1"}

EQUIPMENT_TYPES = [
    "Centrifugal Pump",
    "Air Compressor",
    "HVAC Unit",
    "Conveyor Belt",
    "Diesel Generator",
    "Industrial Fan",
]

# Optional sensor schema: key -> {label, unit, min, max}
SENSOR_SCHEMA = {
    "temperature_c": {"label": "Bearing / winding temperature", "unit": "°C", "min": -20, "max": 200},
    "vibration_mm_s": {"label": "Vibration velocity", "unit": "mm/s", "min": 0, "max": 30},
    "pressure_bar": {"label": "Discharge / line pressure", "unit": "bar", "min": 0, "max": 25},
    "oil_level_pct": {"label": "Oil level", "unit": "%", "min": 0, "max": 100},
    "current_a": {"label": "Motor current", "unit": "A", "min": 0, "max": 500},
    "runtime_hours": {"label": "Runtime since last service", "unit": "h", "min": 0, "max": 50000},
    "noise_db": {"label": "Noise level", "unit": "dB", "min": 30, "max": 130},
    "flow_m3h": {"label": "Flow rate", "unit": "m³/h", "min": 0, "max": 1000},
}

# Deterministic threshold rules. Each rule is evaluated by triage/rules.py.
# severity: warn | critical. `condition` is documented; logic lives in rules.py.
THRESHOLDS = [
    {"id": "TEMP_WARN", "sensor": "temperature_c", "severity": "warn",
     "description": "Temperature > 85°C exceeds normal operating envelope.",
     "citation": "General rotating-equipment practice; see pump/compressor manual sections."},
    {"id": "TEMP_CRIT", "sensor": "temperature_c", "severity": "critical",
     "description": "Temperature > 95°C indicates likely bearing/lubrication failure risk. Stop and inspect.",
     "citation": "KB-PUMP-03, KB-COMP-02"},
    {"id": "VIB_WARN", "sensor": "vibration_mm_s", "severity": "warn",
     "description": "Vibration > 4.5 mm/s exceeds ISO 10816 Zone B for most machines.",
     "citation": "KB-PUMP-02, KB-FAN-02"},
    {"id": "VIB_CRIT", "sensor": "vibration_mm_s", "severity": "critical",
     "description": "Vibration > 7.1 mm/s is Zone C/D: damage likely, urgent inspection required.",
     "citation": "KB-PUMP-02, KB-FAN-02"},
    {"id": "OIL_LOW_WARN", "sensor": "oil_level_pct", "severity": "warn",
     "description": "Oil level < 30% — top up and check for leaks.",
     "citation": "KB-PUMP-03, KB-COMP-03"},
    {"id": "OIL_LOW_CRIT", "sensor": "oil_level_pct", "severity": "critical",
     "description": "Oil level < 15% — critical starvation risk. Do not run under load.",
     "citation": "KB-PUMP-03, KB-COMP-03"},
    {"id": "PRESS_HIGH", "sensor": "pressure_bar", "severity": "warn",
     "description": "Pressure above rated envelope for type (pump >8 bar, compressor >10 bar, HVAC >6 bar). Check blockage/overcharge.",
     "citation": "KB-PUMP-04, KB-COMP-04, KB-HVAC-02"},
    {"id": "PRESS_LOW", "sensor": "pressure_bar", "severity": "warn",
     "description": "Pressure below minimum (pump <1.5 bar, compressor <5 bar when running). Check leaks, cavitation, belt slip.",
     "citation": "KB-PUMP-04, KB-COMP-04"},
    {"id": "CURRENT_HIGH", "sensor": "current_a", "severity": "warn",
     "description": "Motor current > 15% above rated (heuristic: >50A small units / >200A large). Check overload, binding, voltage.",
     "citation": "KB-GEN-03, KB-CONV-03"},
    {"id": "NOISE_HIGH", "sensor": "noise_db", "severity": "warn",
     "description": "Noise > 90 dB suggests cavitation, bearing spall, belt distress or looseness.",
     "citation": "KB-PUMP-02, KB-CONV-02"},
    {"id": "SERVICE_OVERDUE", "sensor": "runtime_hours", "severity": "warn",
     "description": "Runtime > 4000 h since last service — preventive maintenance overdue.",
     "citation": "All preventive-maintenance sections (e.g. KB-PUMP-05)."},
]

# Rated pressure envelopes per equipment type (bar): (low_warn, high_warn)
PRESSURE_ENVELOPES = {
    "Centrifugal Pump": (1.5, 8.0),
    "Air Compressor": (5.0, 10.0),
    "HVAC Unit": (1.0, 6.0),
    "Conveyor Belt": (0.0, 8.0),
    "Diesel Generator": (0.0, 8.0),
    "Industrial Fan": (0.0, 5.0),
}

PRIORITY_DEFINITIONS = {
    "P1 - Emergency": "Immediate danger to people, environment, or risk of catastrophic damage. Stop equipment, isolate, escalate now.",
    "P2 - Urgent": "Critical threshold breached or multiple warnings. Inspect within 24h; do not run unattended.",
    "P3 - Planned": "Single warning or degraded performance. Schedule maintenance within 7 days.",
    "P4 - Routine": "No threshold breach. Monitor and include in next preventive window.",
}

SAFETY_NOTICE = (
    "This assistant never controls equipment and never auto-approves work. "
    "Every work order stays in DRAFT until a qualified technician reviews, edits, and explicitly approves or rejects it."
)
