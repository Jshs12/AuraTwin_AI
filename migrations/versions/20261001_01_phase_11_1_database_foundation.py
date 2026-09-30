"""Create normalized organization and building configuration foundation.

Revision ID: 20261001_01
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "20261001_01"
down_revision = None
branch_labels = None
depends_on = None

UUID_TYPE = sa.Uuid(as_uuid=True)
JSON_TYPE = sa.JSON()


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("organization_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_table(
        "users",
        sa.Column("user_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("email_normalized", sa.String(320), nullable=False),
        sa.Column("password_hash", sa.String(512), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("role IN ('ADMIN', 'OPERATOR')", name="ck_users_role"),
        sa.CheckConstraint("lower(email) = email_normalized", name="ck_users_email_normalized"),
        sa.UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
    )
    op.create_table(
        "organization_memberships",
        sa.Column("membership_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("organization_id", UUID_TYPE, sa.ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by_user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),
    )
    op.create_index("ix_organization_memberships_organization_id", "organization_memberships", ["organization_id"])
    op.create_index("ix_organization_memberships_user_id", "organization_memberships", ["user_id"])
    op.create_table(
        "buildings",
        sa.Column("building_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("organization_id", UUID_TYPE, sa.ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("timezone", sa.String(100), server_default="UTC", nullable=False),
        sa.Column("address", JSON_TYPE, nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", "slug", name="uq_buildings_org_slug"),
    )
    op.create_index("ix_buildings_organization_id", "buildings", ["organization_id"])

    op.create_table(
        "user_building_access",
        sa.Column("assignment_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False),
        sa.Column("building_id", UUID_TYPE, sa.ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False),
        sa.Column("assigned_by_user_id", UUID_TYPE, sa.ForeignKey("users.user_id", ondelete="SET NULL")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "building_id", name="uq_user_building_access"),
    )
    op.create_index("ix_user_building_access_user_id", "user_building_access", ["user_id"])
    op.create_index("ix_user_building_access_building_id", "user_building_access", ["building_id"])

    op.create_table(
        "floors",
        sa.Column("floor_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("building_id", UUID_TYPE, sa.ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("floor_key", sa.String(80), nullable=False),
        sa.Column("level_number", sa.Integer()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("building_id", "floor_key", name="uq_floors_building_key"),
    )
    op.create_index("ix_floors_building_id", "floors", ["building_id"])

    op.create_table(
        "zones",
        sa.Column("zone_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("floor_id", UUID_TYPE, sa.ForeignKey("floors.floor_id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone_key", sa.String(100), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("zone_type", sa.String(80), nullable=False),
        sa.Column("capacity", sa.Integer(), server_default="0", nullable=False),
        sa.Column("area_m2", sa.Float(), nullable=False),
        sa.Column("comfort_min_c", sa.Float(), nullable=False),
        sa.Column("comfort_max_c", sa.Float(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("capacity >= 0", name="ck_zones_capacity_nonnegative"),
        sa.CheckConstraint("area_m2 >= 0", name="ck_zones_area_nonnegative"),
        sa.CheckConstraint("comfort_min_c < comfort_max_c", name="ck_zones_comfort_order"),
        sa.UniqueConstraint("floor_id", "zone_key", name="uq_zones_floor_key"),
    )
    op.create_index("ix_zones_floor_id", "zones", ["floor_id"])

    op.create_table(
        "integrations",
        sa.Column("integration_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("building_id", UUID_TYPE, sa.ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(160), nullable=False),
        sa.Column("integration_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), server_default="UNCONFIGURED", nullable=False),
        sa.Column("configuration", JSON_TYPE, nullable=False),
        sa.Column("credential_reference", sa.String(500)),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("building_id", "name", name="uq_integrations_building_name"),
    )
    op.create_index("ix_integrations_building_id", "integrations", ["building_id"])

    op.create_table(
        "devices",
        sa.Column("device_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("integration_id", UUID_TYPE, sa.ForeignKey("integrations.integration_id", ondelete="CASCADE"), nullable=False),
        sa.Column("zone_id", UUID_TYPE, sa.ForeignKey("zones.zone_id", ondelete="SET NULL")),
        sa.Column("external_device_id", sa.String(200), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("device_type", sa.String(80), nullable=False),
        sa.Column("status", sa.String(40), server_default="UNCONFIGURED", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("integration_id", "external_device_id", name="uq_devices_integration_external"),
    )
    op.create_index("ix_devices_integration_id", "devices", ["integration_id"])
    op.create_index("ix_devices_zone_id", "devices", ["zone_id"])

    op.create_table(
        "point_mappings",
        sa.Column("point_mapping_id", UUID_TYPE, primary_key=True, nullable=False),
        sa.Column("device_id", UUID_TYPE, sa.ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False),
        sa.Column("external_point_id", sa.String(250), nullable=False),
        sa.Column("logical_signal", sa.String(100), nullable=False),
        sa.Column("data_type", sa.String(40), nullable=False),
        sa.Column("unit", sa.String(40)),
        sa.Column("readable", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("writable", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("metadata", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("readable = true OR writable = true", name="ck_point_mapping_access_direction"),
        sa.UniqueConstraint("device_id", "external_point_id", name="uq_point_mappings_device_external"),
    )
    op.create_index("ix_point_mappings_device_id", "point_mappings", ["device_id"])
    op.create_index("ix_point_mappings_logical_signal", "point_mappings", ["logical_signal"])


def downgrade() -> None:
    op.drop_index("ix_point_mappings_logical_signal", table_name="point_mappings")
    op.drop_index("ix_point_mappings_device_id", table_name="point_mappings")
    op.drop_table("point_mappings")
    op.drop_index("ix_organization_memberships_user_id", table_name="organization_memberships")
    op.drop_index("ix_organization_memberships_organization_id", table_name="organization_memberships")
    op.drop_table("organization_memberships")
    op.drop_index("ix_devices_zone_id", table_name="devices")
    op.drop_index("ix_devices_integration_id", table_name="devices")
    op.drop_table("devices")
    op.drop_index("ix_integrations_building_id", table_name="integrations")
    op.drop_table("integrations")
    op.drop_index("ix_zones_floor_id", table_name="zones")
    op.drop_table("zones")
    op.drop_index("ix_floors_building_id", table_name="floors")
    op.drop_table("floors")
    op.drop_index("ix_user_building_access_building_id", table_name="user_building_access")
    op.drop_index("ix_user_building_access_user_id", table_name="user_building_access")
    op.drop_table("user_building_access")
    op.drop_index("ix_buildings_organization_id", table_name="buildings")
    op.drop_table("buildings")
    op.drop_table("users")
    op.drop_table("organizations")
