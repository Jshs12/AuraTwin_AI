"""Building-scoped integration commissioning APIs; no hardware I/O is performed."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database.models import BuildingRecord, DeviceRecord, IntegrationRecord, PointMappingRecord
from backend.schemas.integration_config import (DeviceCreate, DeviceUpdate,
    IntegrationCreate, IntegrationUpdate, PointCreate, PointUpdate)
from backend.security.dependencies import require_building_access, require_permission
from backend.security.roles import Permission

router = APIRouter(tags=["Integration Configuration"])


def _uuid(identifier: str):
    try:
        return UUID(identifier)
    except (ValueError, TypeError):
        return None


def _session(request: Request):
    return request.app.state.database_sessions


def _building(request: Request, user, building_id: str, *, write=False):
    building_uuid = _uuid(building_id)
    if not building_uuid:
        raise HTTPException(404, "Building not found")
    with _session(request)() as session:
        row = session.get(BuildingRecord, building_uuid)
        if row is None or row.archived_at is not None:
            raise HTTPException(404, "Building not found")
    require_building_access(str(building_uuid), user, request)
    if write and user.role.value != "OPERATOR":
        raise HTTPException(403, "Insufficient permission")
    return building_uuid


def _integration(session, request, user, integration_id: str, *, write=False, include_archived=False):
    parsed = _uuid(integration_id)
    row = session.get(IntegrationRecord, parsed) if parsed else None
    if row is None or (row.archived_at is not None and not include_archived):
        raise HTTPException(404, "Integration not found")
    _building(request, user, str(row.building_id), write=write)
    return row


def _device(session, request, user, device_id: str, *, write=False, include_archived=False):
    parsed = _uuid(device_id)
    row = session.get(DeviceRecord, parsed) if parsed else None
    if row is None or (row.archived_at is not None and not include_archived):
        raise HTTPException(404, "Device not found")
    integration = session.get(IntegrationRecord, row.integration_id)
    if integration is None:
        raise HTTPException(404, "Device not found")
    _building(request, user, str(integration.building_id), write=write)
    return row, integration


def _point(session, request, user, point_id: str, *, write=False):
    parsed = _uuid(point_id)
    row = session.get(PointMappingRecord, parsed) if parsed else None
    if row is None:
        raise HTTPException(404, "Point not found")
    device, integration = _device(session, request, user, str(row.device_id), write=write)
    return row, device, integration


def _integration_dict(row):
    return {"integration_id": str(row.integration_id), "building_id": str(row.building_id),
        "name": row.name, "integration_type": row.integration_type, "status": row.status,
        "configuration": dict(row.configuration or {}), "credential_configured": bool(row.credential_reference),
        "simulated": True, "archived_at": row.archived_at,
        "created_at": row.created_at, "updated_at": row.updated_at}


def _device_dict(row, integration):
    return {"device_id": str(row.device_id), "integration_id": str(row.integration_id),
        "building_id": str(integration.building_id), "zone_id": str(row.zone_id) if row.zone_id else None,
        "external_device_id": row.external_device_id, "name": row.name, "device_type": row.device_type,
        "manufacturer": row.manufacturer, "model": row.model, "status": row.status,
        "archived_at": row.archived_at, "created_at": row.created_at, "updated_at": row.updated_at}


def _point_dict(row, device, integration):
    return {"point_mapping_id": str(row.point_mapping_id), "point_id": str(row.point_mapping_id),
        "device_id": str(row.device_id), "integration_id": str(device.integration_id),
        "building_id": str(integration.building_id), "external_point_id": row.external_point_id,
        "logical_signal": row.logical_signal, "data_type": row.data_type, "unit": row.unit,
        "readable": row.readable, "writable": row.writable, "metadata": dict(row.metadata_json or {}),
        "mapping_status": row.mapping_status, "mapping_confidence": row.mapping_confidence,
        "mapping_source": row.mapping_source, "created_at": row.created_at, "updated_at": row.updated_at}


def _audit(request, user, action, resource, resource_id, building_id):
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action=action, resource=resource, resource_id=resource_id, building_id=building_id, success=True)


@router.get("/buildings/{building_id}/integrations")
def list_integrations(building_id: str, request: Request,
                      user=Depends(require_permission(Permission.BUILDING_READ))):
    building_uuid = _building(request, user, building_id)
    with _session(request)() as session:
        rows = session.scalars(select(IntegrationRecord).where(
            IntegrationRecord.building_id == building_uuid,
            IntegrationRecord.archived_at.is_(None)).order_by(IntegrationRecord.name)).all()
        return {"integrations": [_integration_dict(row) for row in rows]}


@router.post("/buildings/{building_id}/integrations", status_code=201)
def create_integration(building_id: str, body: IntegrationCreate, request: Request,
                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    building_uuid = _building(request, user, building_id, write=True)
    row = IntegrationRecord(integration_id=uuid4(), building_id=building_uuid, name=body.name,
        integration_type=body.integration_type, status="CONFIGURED", configuration=body.configuration,
        credential_reference=body.credential_reference)
    try:
        with _session(request).begin() as session:
            session.add(row)
            session.flush()
            result = _integration_dict(row)
    except IntegrityError:
        raise HTTPException(409, "An integration with this name already exists in the building") from None
    _audit(request, user, "integration_created", "integration", result["integration_id"], str(building_uuid))
    return result


@router.get("/integrations/{integration_id}")
def get_integration(integration_id: str, request: Request,
                    user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        return _integration_dict(_integration(session, request, user, integration_id))


@router.patch("/integrations/{integration_id}")
def update_integration(integration_id: str, body: IntegrationUpdate, request: Request,
                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    patch = body.model_dump(exclude_unset=True)
    if "credential_reference" in patch and patch["credential_reference"] is None:
        patch.pop("credential_reference")
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        for key, value in patch.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        result = _integration_dict(row)
    _audit(request, user, "integration_updated", "integration", result["integration_id"], result["building_id"])
    return result


@router.delete("/integrations/{integration_id}")
def disable_integration(integration_id: str, request: Request,
                        user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        row.status = "DISABLED"
        row.archived_at = datetime.now(timezone.utc)
        row.updated_at = row.archived_at
        result = _integration_dict(row)
    _audit(request, user, "integration_disabled", "integration", result["integration_id"], result["building_id"])
    return result


@router.post("/integrations/{integration_id}/test-connection")
def test_integration_configuration(integration_id: str, request: Request,
                                  user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request)() as session:
        row = _integration(session, request, user, integration_id, write=True)
        result = request.app.state.integration_connection_tester.test(status=row.status, configuration=row.configuration or {})
        return {"integration_id": str(row.integration_id), **result.__dict__}


@router.post("/integrations/{integration_id}/discover")
def discover_devices(integration_id: str, request: Request,
                     user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request)() as session:
        row = _integration(session, request, user, integration_id, write=True)
        result = request.app.state.discovery_provider.discover(row.integration_type)
        return {"integration_id": str(row.integration_id), "simulated": result.simulated,
            "discovery_performed": result.performed, "candidates": list(result.candidates),
            "message": result.message}


@router.get("/integrations/{integration_id}/devices")
def list_devices(integration_id: str, request: Request,
                 user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id)
        rows = session.scalars(select(DeviceRecord).where(DeviceRecord.integration_id == integration.integration_id,
            DeviceRecord.archived_at.is_(None)).order_by(DeviceRecord.name)).all()
        return {"devices": [_device_dict(row, integration) for row in rows]}


@router.post("/integrations/{integration_id}/devices", status_code=201)
def create_device(integration_id: str, body: DeviceCreate, request: Request,
                  user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        integration = _integration(session, request, user, integration_id, write=True)
        if body.zone_id:
            zone = request.app.state.configuration_repository.resolve_zone(body.zone_id)
            if zone is None or zone.building_id != str(integration.building_id):
                raise HTTPException(404, "Zone not found in this building")
        row = DeviceRecord(device_id=uuid4(), integration_id=integration.integration_id,
            zone_id=_uuid(body.zone_id) if body.zone_id else None,
            external_device_id=body.external_device_id, name=body.name, device_type=body.device_type,
            manufacturer=body.manufacturer, model=body.model, status="CONFIGURED")
        session.add(row)
        session.flush()
        result = _device_dict(row, integration)
    _audit(request, user, "device_created", "device", result["device_id"], result["building_id"])
    return result


@router.get("/devices/{device_id}")
def get_device(device_id: str, request: Request,
               user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        row, integration = _device(session, request, user, device_id)
        return _device_dict(row, integration)


@router.patch("/devices/{device_id}")
def update_device(device_id: str, body: DeviceUpdate, request: Request,
                  user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    patch = body.model_dump(exclude_unset=True)
    with _session(request).begin() as session:
        row, integration = _device(session, request, user, device_id, write=True)
        if patch.get("zone_id"):
            zone = request.app.state.configuration_repository.resolve_zone(patch["zone_id"])
            if zone is None or zone.building_id != str(integration.building_id):
                raise HTTPException(404, "Zone not found in this building")
            patch["zone_id"] = _uuid(patch["zone_id"])
        for key, value in patch.items():
            setattr(row, key, value)
        row.updated_at = datetime.now(timezone.utc)
        result = _device_dict(row, integration)
    _audit(request, user, "device_updated", "device", result["device_id"], result["building_id"])
    return result


@router.delete("/devices/{device_id}")
def disable_device(device_id: str, request: Request,
                   user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        row, integration = _device(session, request, user, device_id, write=True)
        row.status = "DISABLED"
        row.archived_at = datetime.now(timezone.utc)
        row.updated_at = row.archived_at
        result = _device_dict(row, integration)
    _audit(request, user, "device_disabled", "device", result["device_id"], result["building_id"])
    return result


@router.get("/devices/{device_id}/points")
def list_points(device_id: str, request: Request,
                user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        device, integration = _device(session, request, user, device_id)
        rows = session.scalars(select(PointMappingRecord).where(PointMappingRecord.device_id == device.device_id)
            .order_by(PointMappingRecord.external_point_id)).all()
        return {"points": [_point_dict(row, device, integration) for row in rows]}


@router.post("/devices/{device_id}/points", status_code=201)
def create_point(device_id: str, body: PointCreate, request: Request,
                 user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        device, integration = _device(session, request, user, device_id, write=True)
        row = PointMappingRecord(point_mapping_id=uuid4(), device_id=device.device_id,
            external_point_id=body.external_point_id, logical_signal=body.logical_signal,
            data_type=body.data_type, unit=body.unit, readable=body.readable, writable=body.writable,
            metadata_json=body.metadata, mapping_status=body.mapping_status,
            mapping_confidence=body.mapping_confidence,
            mapping_source="OPERATOR" if body.mapping_status == "UNMAPPED" else "OPERATOR_SUGGESTION")
        session.add(row)
        session.flush()
        result = _point_dict(row, device, integration)
    _audit(request, user, "point_mapping_created", "point_mapping", result["point_mapping_id"], result["building_id"])
    return result


@router.get("/point-mappings/{point_id}")
def get_point(point_id: str, request: Request,
              user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        row, device, integration = _point(session, request, user, point_id)
        return _point_dict(row, device, integration)


@router.patch("/point-mappings/{point_id}")
def update_point(point_id: str, body: PointUpdate, request: Request,
                 user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    patch = body.model_dump(exclude_unset=True)
    if "metadata" in patch:
        patch["metadata_json"] = patch.pop("metadata")
    with _session(request).begin() as session:
        row, device, integration = _point(session, request, user, point_id, write=True)
        for key, value in patch.items():
            setattr(row, key, value)
        if patch.get("mapping_status") == "CONFIRMED":
            row.mapping_source = "OPERATOR"
        row.updated_at = datetime.now(timezone.utc)
        result = _point_dict(row, device, integration)
    _audit(request, user, "point_mapping_updated", "point_mapping", result["point_mapping_id"], result["building_id"])
    return result


def _mapping_decision(point_id: str, decision: str, request: Request, user):
    with _session(request).begin() as session:
        row, device, integration = _point(session, request, user, point_id, write=True)
        row.mapping_status = decision
        row.mapping_source = "OPERATOR"
        row.updated_at = datetime.now(timezone.utc)
        result = _point_dict(row, device, integration)
    _audit(request, user, f"point_mapping_{decision.lower()}", "point_mapping", result["point_mapping_id"], result["building_id"])
    return result


@router.post("/point-mappings/{point_id}/confirm")
def confirm_mapping(point_id: str, request: Request,
                    user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    return _mapping_decision(point_id, "CONFIRMED", request, user)


@router.post("/point-mappings/{point_id}/reject")
def reject_mapping(point_id: str, request: Request,
                   user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    return _mapping_decision(point_id, "REJECTED", request, user)


@router.delete("/point-mappings/{point_id}")
def deactivate_mapping(point_id: str, request: Request,
                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    return _mapping_decision(point_id, "INACTIVE", request, user)
