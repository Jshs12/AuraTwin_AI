"""Add stable public building keys and legacy zone ids.

Revision ID: 20261001_02
Revises: 20261001_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20261001_02"
down_revision = "20261001_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("buildings", sa.Column("building_key", sa.String(length=160), nullable=True))
    bind = op.get_bind()
    buildings = sa.table("buildings",
        sa.column("building_id", sa.Uuid()), sa.column("organization_id", sa.Uuid()),
        sa.column("slug", sa.String()), sa.column("building_key", sa.String()))
    stable_building_key = sa.case(
        (buildings.c.slug == "development-building", sa.literal("development-building")),
        else_=(sa.func.replace(sa.cast(buildings.c.organization_id, sa.String()), "-", "")
               + sa.literal(":") + buildings.c.slug),
    )
    bind.execute(buildings.update().values(building_key=stable_building_key))
    with op.batch_alter_table("buildings") as batch:
        batch.alter_column("building_key", existing_type=sa.String(length=160), nullable=False)
        batch.create_unique_constraint("uq_buildings_building_key", ["building_key"])

    op.add_column("zones", sa.Column("legacy_zone_id", sa.String(length=100), nullable=True))
    zones = sa.table("zones", sa.column("zone_id", sa.Uuid()), sa.column("zone_key", sa.String()),
                     sa.column("legacy_zone_id", sa.String()))
    unambiguous_zone_keys = (sa.select(zones.c.zone_key).group_by(zones.c.zone_key)
                             .having(sa.func.count() == 1))
    bind.execute(zones.update().where(zones.c.zone_key.in_(unambiguous_zone_keys))
                 .values(legacy_zone_id=zones.c.zone_key))
    with op.batch_alter_table("zones") as batch:
        batch.create_unique_constraint("uq_zones_legacy_id", ["legacy_zone_id"])


def downgrade() -> None:
    with op.batch_alter_table("zones") as batch:
        batch.drop_constraint("uq_zones_legacy_id", type_="unique")
        batch.drop_column("legacy_zone_id")
    with op.batch_alter_table("buildings") as batch:
        batch.drop_constraint("uq_buildings_building_key", type_="unique")
        batch.drop_column("building_key")
