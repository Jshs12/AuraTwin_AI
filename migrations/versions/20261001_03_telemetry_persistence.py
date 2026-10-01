"""Add tenant-scoped telemetry observations.

Revision ID: 20261001_03
Revises: 20261001_02
"""
from alembic import op
import sqlalchemy as sa

revision = "20261001_03"
down_revision = "20261001_02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("buildings") as batch:
        batch.create_unique_constraint("uq_buildings_org_id", ["organization_id", "building_id"])
    with op.batch_alter_table("floors") as batch:
        batch.create_unique_constraint("uq_floors_building_id", ["building_id", "floor_id"])
    with op.batch_alter_table("zones") as batch:
        batch.create_unique_constraint("uq_zones_floor_id", ["floor_id", "zone_id"])

    op.create_table(
        "telemetry_observations",
        sa.Column("observation_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("building_id", sa.Uuid(), nullable=False),
        sa.Column("floor_id", sa.Uuid(), nullable=False),
        sa.Column("zone_id", sa.Uuid(), nullable=False),
        sa.Column("signal", sa.String(length=32), nullable=False),
        sa.Column("value", sa.Float(precision=53), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("source", sa.String(length=160), nullable=True),
        sa.Column("quality_state", sa.String(length=24), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.CheckConstraint("signal IN ('occupancy', 'temperature', 'power', 'energy', 'cost', 'tariff_rate')", name="ck_telemetry_signal"),
        sa.ForeignKeyConstraint(["organization_id", "building_id"], ["buildings.organization_id", "buildings.building_id"], ondelete="RESTRICT", name="fk_telemetry_org_building"),
        sa.ForeignKeyConstraint(["building_id", "floor_id"], ["floors.building_id", "floors.floor_id"], ondelete="RESTRICT", name="fk_telemetry_building_floor"),
        sa.ForeignKeyConstraint(["floor_id", "zone_id"], ["zones.floor_id", "zones.zone_id"], ondelete="RESTRICT", name="fk_telemetry_floor_zone"),
        sa.PrimaryKeyConstraint("observation_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_telemetry_idempotency_key"),
    )
    op.create_index("ix_telemetry_org_building_zone_time", "telemetry_observations", ["organization_id", "building_id", "zone_id", "observed_at"])
    op.create_index("ix_telemetry_building_time", "telemetry_observations", ["building_id", "observed_at"])
    op.create_index("ix_telemetry_zone_signal_time", "telemetry_observations", ["zone_id", "signal", "observed_at"])


def downgrade() -> None:
    op.drop_index("ix_telemetry_zone_signal_time", table_name="telemetry_observations")
    op.drop_index("ix_telemetry_building_time", table_name="telemetry_observations")
    op.drop_index("ix_telemetry_org_building_zone_time", table_name="telemetry_observations")
    op.drop_table("telemetry_observations")
    with op.batch_alter_table("zones") as batch:
        batch.drop_constraint("uq_zones_floor_id", type_="unique")
    with op.batch_alter_table("floors") as batch:
        batch.drop_constraint("uq_floors_building_id", type_="unique")
    with op.batch_alter_table("buildings") as batch:
        batch.drop_constraint("uq_buildings_org_id", type_="unique")
