"""Add the bounded durable observation-only edge queue."""
from alembic import op
import sqlalchemy as sa


revision = "20261004_10"
down_revision = "20261003_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "edge_message_queue",
        sa.Column("queue_sequence", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("queue_record_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("message_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("edge_id", sa.String(length=120), nullable=False),
        sa.Column("organization_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("building_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("observation_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("delivery_state", sa.String(length=16), server_default="PENDING", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_reason", sa.String(length=80), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retryable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.CheckConstraint("delivery_state IN ('PENDING', 'IN_FLIGHT', 'DELIVERED', 'FAILED')",
                           name="ck_edge_queue_delivery_state"),
        sa.ForeignKeyConstraint(["organization_id", "building_id"],
            ["buildings.organization_id", "buildings.building_id"], ondelete="RESTRICT",
            name="fk_edge_queue_org_building"),
        sa.PrimaryKeyConstraint("queue_sequence"),
        sa.UniqueConstraint("queue_record_id"),
        sa.UniqueConstraint("edge_id", "message_id", name="uq_edge_queue_edge_message"),
    )
    op.create_index("ix_edge_queue_edge_state_sequence", "edge_message_queue",
                    ["edge_id", "delivery_state", "queue_sequence"])
    op.create_index("ix_edge_queue_edge_created", "edge_message_queue", ["edge_id", "created_at"])
    op.create_index("ix_edge_queue_message_id", "edge_message_queue", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_edge_queue_message_id", table_name="edge_message_queue")
    op.drop_index("ix_edge_queue_edge_created", table_name="edge_message_queue")
    op.drop_index("ix_edge_queue_edge_state_sequence", table_name="edge_message_queue")
    op.drop_table("edge_message_queue")
