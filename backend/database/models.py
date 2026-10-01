"""Normalized organization/building configuration models shared by SQLite and PostgreSQL."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (Boolean, CheckConstraint, DateTime, Float, ForeignKey,
                        ForeignKeyConstraint, Index, Integer, JSON, String,
                        UniqueConstraint, Uuid, func)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class OrganizationRecord(TimestampMixin, Base):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug", name="uq_organizations_slug"),)

    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    buildings: Mapped[list["BuildingRecord"]] = relationship(back_populates="organization")
    user_memberships: Mapped[list["OrganizationMembershipRecord"]] = relationship(back_populates="organization", cascade="all, delete-orphan")


class UserRecord(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('ADMIN', 'OPERATOR')", name="ck_users_role"),
        CheckConstraint("lower(email) = email_normalized", name="ck_users_email_normalized"),
        UniqueConstraint("email_normalized", name="uq_users_email_normalized"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    email_normalized: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    organization_memberships: Mapped[list["OrganizationMembershipRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="OrganizationMembershipRecord.user_id",
    )
    building_access: Mapped[list["UserBuildingAccessRecord"]] = relationship(
        back_populates="user", cascade="all, delete-orphan",
        foreign_keys="UserBuildingAccessRecord.user_id",
    )


class BuildingRecord(TimestampMixin, Base):
    __tablename__ = "buildings"
    __table_args__ = (
        UniqueConstraint("organization_id", "slug", name="uq_buildings_org_slug"),
        UniqueConstraint("building_key", name="uq_buildings_building_key"),
        UniqueConstraint("organization_id", "building_id", name="uq_buildings_org_id"),
    )

    building_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="RESTRICT"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    building_key: Mapped[str] = mapped_column(String(160), nullable=False,
        default=lambda context: context.get_current_parameters().get("slug", "building"))
    timezone: Mapped[str] = mapped_column(String(100), nullable=False, default="UTC", server_default="UTC")
    address: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    organization: Mapped[OrganizationRecord] = relationship(back_populates="buildings")
    access_assignments: Mapped[list["UserBuildingAccessRecord"]] = relationship(back_populates="building", cascade="all, delete-orphan")
    floors: Mapped[list["FloorRecord"]] = relationship(back_populates="building", cascade="all, delete-orphan")
    integrations: Mapped[list["IntegrationRecord"]] = relationship(back_populates="building", cascade="all, delete-orphan")


class UserBuildingAccessRecord(TimestampMixin, Base):
    __tablename__ = "user_building_access"
    __table_args__ = (UniqueConstraint("user_id", "building_id", name="uq_user_building_access"),)

    assignment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    building_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))

    user: Mapped[UserRecord] = relationship(back_populates="building_access", foreign_keys=[user_id])
    building: Mapped[BuildingRecord] = relationship(back_populates="access_assignments")


class OrganizationMembershipRecord(TimestampMixin, Base):
    __tablename__ = "organization_memberships"
    __table_args__ = (UniqueConstraint("organization_id", "user_id", name="uq_organization_memberships_org_user"),)

    membership_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("organizations.organization_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    assigned_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.user_id", ondelete="SET NULL"))

    organization: Mapped[OrganizationRecord] = relationship(back_populates="user_memberships")
    user: Mapped[UserRecord] = relationship(back_populates="organization_memberships", foreign_keys=[user_id])


class FloorRecord(TimestampMixin, Base):
    __tablename__ = "floors"
    __table_args__ = (UniqueConstraint("building_id", "floor_key", name="uq_floors_building_key"),
                      UniqueConstraint("building_id", "floor_id", name="uq_floors_building_id"))

    floor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    building_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    floor_key: Mapped[str] = mapped_column(String(80), nullable=False)
    level_number: Mapped[int | None] = mapped_column(Integer)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    building: Mapped[BuildingRecord] = relationship(back_populates="floors")
    zones: Mapped[list["ZoneRecord"]] = relationship(back_populates="floor", cascade="all, delete-orphan")


class ZoneRecord(TimestampMixin, Base):
    __tablename__ = "zones"
    __table_args__ = (
        UniqueConstraint("floor_id", "zone_key", name="uq_zones_floor_key"),
        UniqueConstraint("legacy_zone_id", name="uq_zones_legacy_id"),
        UniqueConstraint("floor_id", "zone_id", name="uq_zones_floor_id"),
        CheckConstraint("capacity >= 0", name="ck_zones_capacity_nonnegative"),
        CheckConstraint("area_m2 >= 0", name="ck_zones_area_nonnegative"),
        CheckConstraint("comfort_min_c < comfort_max_c", name="ck_zones_comfort_order"),
    )

    zone_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    floor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("floors.floor_id", ondelete="CASCADE"), nullable=False, index=True)
    zone_key: Mapped[str] = mapped_column(String(100), nullable=False)
    legacy_zone_id: Mapped[str | None] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    zone_type: Mapped[str] = mapped_column(String(80), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    area_m2: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    comfort_min_c: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    comfort_max_c: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    floor: Mapped[FloorRecord] = relationship(back_populates="zones")
    devices: Mapped[list["DeviceRecord"]] = relationship(back_populates="zone")


class TelemetryObservationRecord(Base):
    """Privacy-minimized, tenant-scoped signal observation history."""

    __tablename__ = "telemetry_observations"
    __table_args__ = (
        ForeignKeyConstraint(["organization_id", "building_id"],
            ["buildings.organization_id", "buildings.building_id"], ondelete="RESTRICT",
            name="fk_telemetry_org_building"),
        ForeignKeyConstraint(["building_id", "floor_id"],
            ["floors.building_id", "floors.floor_id"], ondelete="RESTRICT",
            name="fk_telemetry_building_floor"),
        ForeignKeyConstraint(["floor_id", "zone_id"],
            ["zones.floor_id", "zones.zone_id"], ondelete="RESTRICT",
            name="fk_telemetry_floor_zone"),
        UniqueConstraint("idempotency_key", name="uq_telemetry_idempotency_key"),
        Index("ix_telemetry_org_building_zone_time", "organization_id", "building_id", "zone_id", "observed_at"),
        Index("ix_telemetry_building_time", "building_id", "observed_at"),
        Index("ix_telemetry_building_floor_time", "building_id", "floor_id", "observed_at"),
        Index("ix_telemetry_zone_signal_time", "zone_id", "signal", "observed_at"),
        CheckConstraint("signal IN ('occupancy', 'temperature', 'power', 'energy', 'cost', 'tariff_rate')", name="ck_telemetry_signal"),
    )

    observation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    building_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    floor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    zone_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    signal: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[float] = mapped_column(Float(precision=53), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    source: Mapped[str | None] = mapped_column(String(160))
    quality_state: Mapped[str | None] = mapped_column(String(24))
    simulated: Mapped[bool | None] = mapped_column(Boolean)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)


class IntegrationRecord(TimestampMixin, Base):
    __tablename__ = "integrations"
    __table_args__ = (UniqueConstraint("building_id", "name", name="uq_integrations_building_name"),)

    integration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    building_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("buildings.building_id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    integration_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNCONFIGURED", server_default="UNCONFIGURED")
    configuration: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    credential_reference: Mapped[str | None] = mapped_column(String(500))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    building: Mapped[BuildingRecord] = relationship(back_populates="integrations")
    devices: Mapped[list["DeviceRecord"]] = relationship(back_populates="integration", cascade="all, delete-orphan")


class DeviceRecord(TimestampMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("integration_id", "external_device_id", name="uq_devices_integration_external"),)

    device_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    integration_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("integrations.integration_id", ondelete="CASCADE"), nullable=False, index=True)
    zone_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("zones.zone_id", ondelete="SET NULL"), index=True)
    external_device_id: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    device_type: Mapped[str] = mapped_column(String(80), nullable=False)
    manufacturer: Mapped[str | None] = mapped_column(String(160))
    model: Mapped[str | None] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNCONFIGURED", server_default="UNCONFIGURED")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    integration: Mapped[IntegrationRecord] = relationship(back_populates="devices")
    zone: Mapped[ZoneRecord | None] = relationship(back_populates="devices")
    point_mappings: Mapped[list["PointMappingRecord"]] = relationship(back_populates="device", cascade="all, delete-orphan")


class PointMappingRecord(TimestampMixin, Base):
    __tablename__ = "point_mappings"
    __table_args__ = (
        UniqueConstraint("device_id", "external_point_id", name="uq_point_mappings_device_external"),
        CheckConstraint("readable = true OR writable = true", name="ck_point_mapping_access_direction"),
    )

    point_mapping_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    device_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("devices.device_id", ondelete="CASCADE"), nullable=False, index=True)
    zone_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("zones.zone_id", ondelete="SET NULL"), index=True)
    external_point_id: Mapped[str] = mapped_column(String(250), nullable=False)
    logical_signal: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    data_type: Mapped[str] = mapped_column(String(40), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(40))
    readable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default="true")
    writable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSON, nullable=False, default=dict)
    mapping_status: Mapped[str] = mapped_column(String(24), nullable=False, default="UNMAPPED", server_default="UNMAPPED")
    mapping_confidence: Mapped[float | None] = mapped_column(Float(precision=53))
    mapping_source: Mapped[str | None] = mapped_column(String(40))

    device: Mapped[DeviceRecord] = relationship(back_populates="point_mappings")
