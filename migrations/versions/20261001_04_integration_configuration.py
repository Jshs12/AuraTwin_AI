"""Add commissioning metadata and logical mapping lifecycle fields.

Revision ID: 20261001_04
Revises: 20261001_03
"""
from alembic import op
import sqlalchemy as sa

revision = "20261001_04"
down_revision = "20261001_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("manufacturer", sa.String(160), nullable=True))
        batch.add_column(sa.Column("model", sa.String(160), nullable=True))
    with op.batch_alter_table("point_mappings") as batch:
        batch.add_column(sa.Column("mapping_status", sa.String(24), server_default="UNMAPPED", nullable=False))
        batch.add_column(sa.Column("mapping_confidence", sa.Float(precision=53), nullable=True))
        batch.add_column(sa.Column("mapping_source", sa.String(40), nullable=True))
        batch.create_check_constraint(
            "ck_point_mapping_status",
            "mapping_status IN ('UNMAPPED', 'SUGGESTED', 'CONFIRMED', 'REJECTED', 'INACTIVE')",
        )


def downgrade() -> None:
    with op.batch_alter_table("point_mappings") as batch:
        batch.drop_constraint("ck_point_mapping_status", type_="check")
        batch.drop_column("mapping_source")
        batch.drop_column("mapping_confidence")
        batch.drop_column("mapping_status")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("model")
        batch.drop_column("manufacturer")
