"""Add tenant-scoped building knowledge documents, versions and chunks.

Revision ID: 20261002_07
Revises: 20261002_06
"""
from alembic import op
import sqlalchemy as sa

revision = "20261002_07"
down_revision = "20261002_06"
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "knowledge_documents",
        sa.Column("document_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("organization_id", UUID_TYPE, nullable=False),
        sa.Column("building_id", UUID_TYPE, nullable=False),
        sa.Column("floor_id", UUID_TYPE, nullable=True),
        sa.Column("zone_id", UUID_TYPE, nullable=True),
        sa.Column("name", sa.String(240), nullable=False),
        sa.Column("category", sa.String(40), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("source_reference", sa.String(500), nullable=True),
        sa.Column("ingestion_status", sa.String(24), server_default="REGISTERED", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("simulated", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "building_id"],
            ["buildings.organization_id", "buildings.building_id"], ondelete="RESTRICT",
            name="fk_knowledge_document_org_building"),
        sa.ForeignKeyConstraint(["building_id", "floor_id"],
            ["floors.building_id", "floors.floor_id"], ondelete="RESTRICT",
            name="fk_knowledge_document_building_floor"),
        sa.ForeignKeyConstraint(["floor_id", "zone_id"],
            ["zones.floor_id", "zones.zone_id"], ondelete="RESTRICT",
            name="fk_knowledge_document_floor_zone"),
        sa.CheckConstraint("category IN ('HVAC_MANUAL', 'EQUIPMENT_MANUAL', 'OPERATING_POLICY', 'MAINTENANCE_PROCEDURE', 'BUILDING_GUIDE', 'COMFORT_POLICY', 'SAFETY_POLICY', 'OTHER')", name="ck_knowledge_document_category"),
        sa.CheckConstraint("ingestion_status IN ('REGISTERED', 'INGESTING', 'READY', 'FAILED')", name="ck_knowledge_document_ingestion_status"),
    )
    op.create_index("ix_knowledge_documents_building_status", "knowledge_documents",
        ["organization_id", "building_id", "archived_at"])

    op.create_table(
        "knowledge_document_versions",
        sa.Column("version_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("document_id", UUID_TYPE, nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column("file_format", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("original_content", sa.LargeBinary(), nullable=False),
        sa.Column("ingestion_status", sa.String(24), server_default="REGISTERED", nullable=False),
        sa.Column("ingestion_error_code", sa.String(80), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("provenance", sa.String(80), server_default="LOCAL_EXTRACTOR", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["knowledge_documents.document_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("document_id", "version_number", name="uq_knowledge_document_version"),
        sa.UniqueConstraint("document_id", "content_hash", name="uq_knowledge_document_content_hash"),
        sa.CheckConstraint("ingestion_status IN ('REGISTERED', 'INGESTING', 'READY', 'FAILED')", name="ck_knowledge_version_status"),
    )
    op.create_index("ix_knowledge_versions_active_ready", "knowledge_document_versions",
        ["document_id", "is_active", "ingestion_status"])
    op.create_index("uq_knowledge_one_active_version", "knowledge_document_versions",
        ["document_id"], unique=True, sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active = true"))

    op.create_table(
        "knowledge_chunks",
        sa.Column("chunk_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("version_id", UUID_TYPE, nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("section", sa.String(500), nullable=True),
        sa.Column("embedding", sa.JSON(), nullable=True),
        sa.Column("embedding_provider", sa.String(80), server_default="development_hashing_not_semantic", nullable=False),
        sa.ForeignKeyConstraint(["version_id"], ["knowledge_document_versions.version_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("version_id", "sequence_number", name="uq_knowledge_chunk_sequence"),
    )
    op.create_index("ix_knowledge_chunks_version", "knowledge_chunks", ["version_id", "sequence_number"])


def downgrade() -> None:
    op.drop_index("ix_knowledge_chunks_version", table_name="knowledge_chunks")
    op.drop_table("knowledge_chunks")
    op.drop_index("ix_knowledge_versions_active_ready", table_name="knowledge_document_versions")
    op.drop_index("uq_knowledge_one_active_version", table_name="knowledge_document_versions")
    op.drop_table("knowledge_document_versions")
    op.drop_index("ix_knowledge_documents_building_status", table_name="knowledge_documents")
    op.drop_table("knowledge_documents")
