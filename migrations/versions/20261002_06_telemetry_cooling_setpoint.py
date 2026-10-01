"""Allow cooling-setpoint telemetry observations.

Revision ID: 20261002_06
Revises: 20261001_05
"""
from alembic import op

revision = "20261002_06"
down_revision = "20261001_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("telemetry_observations") as batch:
        batch.drop_constraint("ck_telemetry_signal", type_="check")
        batch.create_check_constraint("ck_telemetry_signal",
            "signal IN ('occupancy', 'temperature', 'power', 'energy', 'cost', 'tariff_rate', 'cooling_setpoint')")


def downgrade() -> None:
    with op.batch_alter_table("telemetry_observations") as batch:
        batch.drop_constraint("ck_telemetry_signal", type_="check")
        batch.create_check_constraint("ck_telemetry_signal",
            "signal IN ('occupancy', 'temperature', 'power', 'energy', 'cost', 'tariff_rate')")
