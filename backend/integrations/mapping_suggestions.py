"""Deterministic, review-only point mapping suggestions."""
from __future__ import annotations


SIGNAL_UNITS = {
    "occupancy": {"people", "person", "count"},
    "temperature": {"c", "°c", "celsius"},
    "cooling_setpoint": {"c", "°c", "celsius"},
    "power": {"kw"},
    "energy": {"kwh"},
}


def suggest_mapping(*, signal: str, unit: str | None, data_type: str,
                    readable: bool, zone_owned: bool) -> tuple[str, float | None, str]:
    """Return status/confidence/reason; never confirms a point."""
    if not zone_owned:
        return "UNMAPPED", None, "ZONE_REQUIRED"
    if not readable:
        return "UNMAPPED", None, "POINT_NOT_READABLE"
    if data_type.lower() not in {"number", "numeric", "float", "integer", "int"}:
        return "UNMAPPED", None, "UNSUPPORTED_DATA_TYPE"
    if signal not in SIGNAL_UNITS or (unit or "").strip().lower() not in SIGNAL_UNITS[signal]:
        return "UNMAPPED", None, "SIGNAL_UNIT_UNSUPPORTED"
    return "SUGGESTED", 1.0, "EXACT_SIGNAL_AND_UNIT_MATCH"
