"""Add explicit zone scope to logical point mappings.

Revision ID: 20261001_05
Revises: 20261001_04
"""
from alembic import op
import sqlalchemy as sa

revision = "20261001_05"
down_revision = "20261001_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("point_mappings") as batch:
        batch.add_column(sa.Column("zone_id", sa.Uuid(as_uuid=True), nullable=True))
        batch.create_foreign_key("fk_point_mappings_zone_id_zones", "zones", ["zone_id"], ["zone_id"], ondelete="SET NULL")
        batch.create_index("ix_point_mappings_zone_id", ["zone_id"])


def downgrade() -> None:
    with op.batch_alter_table("point_mappings") as batch:
        batch.drop_index("ix_point_mappings_zone_id")
        batch.drop_constraint("fk_point_mappings_zone_id_zones", type_="foreignkey")
        batch.drop_column("zone_id")
