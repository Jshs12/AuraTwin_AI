"""Persist tenant-scoped optimization interval lifecycle and attribution."""
from alembic import op
import sqlalchemy as sa


revision = "20261003_08"
down_revision = "20261002_07"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "optimization_intervals",
        sa.Column("interval_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("organization_id", UUID_TYPE, nullable=False),
        sa.Column("building_id", UUID_TYPE, nullable=False),
        sa.Column("floor_id", UUID_TYPE, nullable=False),
        sa.Column("zone_id", UUID_TYPE, nullable=False),
        sa.Column("zone_key", sa.String(100), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True)),
        sa.Column("starting_occupancy", sa.Integer(), nullable=False),
        sa.Column("ending_occupancy", sa.Integer()),
        sa.Column("starting_occupancy_observed_at", sa.DateTime(timezone=True)),
        sa.Column("ending_occupancy_observed_at", sa.DateTime(timezone=True)),
        sa.Column("previous_setpoint", sa.Float(precision=53), nullable=False),
        sa.Column("optimized_setpoint", sa.Float(precision=53), nullable=False),
        sa.Column("setpoint_observed_at", sa.DateTime(timezone=True)),
        sa.Column("starting_temperature", sa.Float(precision=53), nullable=False),
        sa.Column("ending_temperature", sa.Float(precision=53)),
        sa.Column("starting_temperature_observed_at", sa.DateTime(timezone=True)),
        sa.Column("ending_temperature_observed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_seconds", sa.Float(precision=53)),
        sa.Column("starting_energy_kwh", sa.Float(precision=53)),
        sa.Column("starting_energy_observed_at", sa.DateTime(timezone=True)),
        sa.Column("ending_energy_kwh", sa.Float(precision=53)),
        sa.Column("ending_energy_observed_at", sa.DateTime(timezone=True)),
        sa.Column("energy_unit", sa.String(8), server_default="kWh", nullable=False),
        sa.Column("energy_consumed_kwh", sa.Float(precision=53)),
        sa.Column("energy_status", sa.String(16), nullable=False),
        sa.Column("energy_reason_code", sa.String(80)),
        sa.Column("energy_source", sa.String(160)),
        sa.Column("energy_quality", sa.String(24)),
        sa.Column("energy_simulated", sa.Boolean()),
        sa.Column("tariff_rate_per_kwh", sa.Float(precision=53)),
        sa.Column("currency", sa.String(3)),
        sa.Column("starting_tariff_observed_at", sa.DateTime(timezone=True)),
        sa.Column("ending_tariff_observed_at", sa.DateTime(timezone=True)),
        sa.Column("tariff_source", sa.String(160)),
        sa.Column("tariff_quality", sa.String(24)),
        sa.Column("tariff_simulated", sa.Boolean()),
        sa.Column("cost_consumed", sa.Float(precision=53)),
        sa.Column("cost_status", sa.String(16), nullable=False),
        sa.Column("cost_reason_code", sa.String(80)),
        sa.Column("quality_state", sa.String(24), nullable=False),
        sa.Column("source", sa.String(160), nullable=False),
        sa.Column("simulated", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "building_id"],
            ["buildings.organization_id", "buildings.building_id"], ondelete="RESTRICT",
            name="fk_optimization_org_building"),
        sa.ForeignKeyConstraint(["building_id", "floor_id"],
            ["floors.building_id", "floors.floor_id"], ondelete="RESTRICT",
            name="fk_optimization_building_floor"),
        sa.ForeignKeyConstraint(["floor_id", "zone_id"],
            ["zones.floor_id", "zones.zone_id"], ondelete="RESTRICT",
            name="fk_optimization_floor_zone"),
        sa.CheckConstraint("status IN ('ACTIVE', 'COMPLETED')", name="ck_optimization_status"),
        sa.CheckConstraint("energy_status IN ('PENDING', 'AVAILABLE', 'UNAVAILABLE', 'INVALID')",
                           name="ck_optimization_energy_status"),
        sa.CheckConstraint("cost_status IN ('PENDING', 'AVAILABLE', 'UNAVAILABLE', 'INVALID')",
                           name="ck_optimization_cost_status"),
        sa.CheckConstraint("energy_unit = 'kWh'", name="ck_optimization_energy_unit"),
    )
    op.create_index("ix_optimization_org_building_started", "optimization_intervals",
                    ["organization_id", "building_id", "started_at"])
    op.create_index("ix_optimization_zone_started", "optimization_intervals", ["zone_id", "started_at"])
    op.create_index("uq_optimization_active_zone", "optimization_intervals", ["zone_id"], unique=True,
                    sqlite_where=sa.text("status = 'ACTIVE'"),
                    postgresql_where=sa.text("status = 'ACTIVE'"))


def downgrade() -> None:
    op.drop_index("uq_optimization_active_zone", table_name="optimization_intervals")
    op.drop_index("ix_optimization_zone_started", table_name="optimization_intervals")
    op.drop_index("ix_optimization_org_building_started", table_name="optimization_intervals")
    op.drop_table("optimization_intervals")
