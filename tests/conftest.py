"""Explicit test-only command policy for application instances created at import."""

import os

# These values support deterministic unit/integration fixtures only. Runtime
# configuration has no implicit production limits and fails closed if absent.
os.environ["COMMAND_LIMIT_MIN_SETPOINT"] = "16"
os.environ["COMMAND_LIMIT_MAX_SETPOINT"] = "30"
os.environ["COMMAND_LIMIT_MAX_DELTA"] = "2"
