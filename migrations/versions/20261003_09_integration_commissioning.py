"""Add integration connection and commissioning lifecycle metadata."""
from alembic import op
import sqlalchemy as sa

revision = "20261003_09"
down_revision = "20261003_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("integrations", sa.Column("connection_state", sa.String(24), nullable=False,
        server_default="DISCONNECTED"))
    op.add_column("integrations", sa.Column("commissioning_state", sa.String(32), nullable=False,
        server_default="CONFIGURED"))
    op.add_column("integrations", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("integrations", sa.Column("last_error", sa.String(240), nullable=True))
    op.add_column("integrations", sa.Column("configuration_tested_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table("integration_lifecycle_events",
        sa.Column("lifecycle_event_id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("integration_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(48), nullable=False),
        sa.Column("previous_state", sa.String(32), nullable=True),
        sa.Column("new_state", sa.String(32), nullable=False),
        sa.Column("source", sa.String(100), nullable=False, server_default="aura_twin"),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["integration_id"], ["integrations.integration_id"], ondelete="CASCADE"))
    op.create_index("ix_integration_lifecycle_events_integration_id",
        "integration_lifecycle_events", ["integration_id"])


def downgrade() -> None:
    op.drop_index("ix_integration_lifecycle_events_integration_id", table_name="integration_lifecycle_events")
    op.drop_table("integration_lifecycle_events")
    op.drop_column("integrations", "configuration_tested_at")
    op.drop_column("integrations", "last_error")
    op.drop_column("integrations", "last_seen_at")
    op.drop_column("integrations", "commissioning_state")
    op.drop_column("integrations", "connection_state")
