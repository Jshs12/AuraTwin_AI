"""Explicit test-only command policy for application instances created at import."""

import os
import pytest

# These values support deterministic unit/integration fixtures only. Runtime
# configuration has no implicit production limits and fails closed if absent.
os.environ["COMMAND_LIMIT_MIN_SETPOINT"] = "16"
os.environ["COMMAND_LIMIT_MAX_SETPOINT"] = "30"
os.environ["COMMAND_LIMIT_MAX_DELTA"] = "2"
# Keep application-level persistence isolated from the developer's local DB.
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"


@pytest.fixture(autouse=True)
def enable_app_control_for_existing_tests():
    """Legacy tests assume the pre-Phase 10.4 app's initially active demo."""
    from backend.api.main import control_service

    for zone_id in ("classroom_01", "classroom_02", "lab_01", "lab_02"):
        control_service.control_states.set_manual_override(zone_id, False, user_id="test-fixture")
        control_service.control_states.set_control_enabled(zone_id, True, user_id="test-fixture")
    yield
    for zone_id in ("classroom_01", "classroom_02", "lab_01", "lab_02"):
        control_service.control_states.set_manual_override(zone_id, False, user_id="test-fixture")
        control_service.control_states.set_control_enabled(zone_id, True, user_id="test-fixture")
