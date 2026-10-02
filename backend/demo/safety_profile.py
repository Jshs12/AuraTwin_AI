"""Explicit safety bounds for the deterministic, simulated demo only."""

from backend.safety.constraints import SafetyConstraintService


DEMO_SAFETY_PROFILE_NAME = "SIMULATED_COMFORT_BOUNDED"


def build_demo_safety_profile(zones, control_provider) -> SafetyConstraintService:
    """Build demo command limits from configured demo-zone comfort bands.

    This profile is intentionally constructed only by the authenticated demo
    start path and refuses providers that do not identify as simulated. It is
    never installed on the application's normal recommendation/control path.
    """
    if not getattr(control_provider, "is_simulated", False):
        raise ValueError("The demo safety profile requires a simulated control provider.")
    if not getattr(control_provider, "is_ready", False):
        raise ValueError("The simulated control provider is not ready.")
    if not zones:
        raise ValueError("The demo safety profile requires configured demo zones.")

    ranges = [(float(zone.comfort.min_temperature), float(zone.comfort.max_temperature))
              for zone in zones]
    if any(not lower < upper for lower, upper in ranges):
        raise ValueError("A demo zone has invalid configured comfort limits.")

    # Absolute bounds follow configured demo comfort bands. The per-command
    # delta is no wider than the narrowest included zone comfort band.
    return SafetyConstraintService(
        min_setpoint=min(lower for lower, _ in ranges),
        max_setpoint=max(upper for _, upper in ranges),
        max_setpoint_delta=min(upper - lower for lower, upper in ranges),
    )
