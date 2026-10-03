"""Building-scoped integration commissioning APIs; no hardware I/O is performed."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database.models import (BuildingRecord, DeviceRecord, IntegrationRecord,
    IntegrationLifecycleEventRecord, PointMappingRecord)
from backend.schemas.integration_config import (DeviceCreate, DeviceUpdate,
    IntegrationCreate, IntegrationUpdate, PointCreate, PointUpdate)
from backend.schemas.provider_observation import SimulatedObservationRequest
from backend.integrations.simulated_observations import ExplicitValueSimulatedProvider
from backend.integrations.mapping_suggestions import suggest_mapping
from backend.integrations.commissioning import evaluate_commissioning
from backend.services.data_quality import DataQualityGate
from backend.schemas.data_quality import QualityState
from backend.security.dependencies import require_building_access, require_permission
from backend.security.roles import Permission

router = APIRouter(tags=["Integration Configuration"])


def _aware(value):
    # SQLite drops timezone metadata on retrieval; persisted AuraTwin datetimes are UTC.
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


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
        "connection_state": row.connection_state, "commissioning_state": row.commissioning_state,
        "last_seen_at": row.last_seen_at, "last_error": row.last_error,
        "configuration_tested_at": row.configuration_tested_at,
        "simulated": True, "physical_connection_implemented": False, "archived_at": row.archived_at,
        "created_at": row.created_at, "updated_at": row.updated_at}


def _lifecycle(session, integration, event_type, new_state, *, source="aura_twin", simulated=True):
    previous = integration.commissioning_state
    integration.commissioning_state = new_state
    integration.updated_at = datetime.now(timezone.utc)
    session.add(IntegrationLifecycleEventRecord(integration_id=integration.integration_id,
        event_type=event_type, previous_state=previous, new_state=new_state,
        source=source, simulated=simulated, occurred_at=datetime.now(timezone.utc)))


def _device_dict(row, integration):
    return {"device_id": str(row.device_id), "integration_id": str(row.integration_id),
        "building_id": str(integration.building_id), "zone_id": str(row.zone_id) if row.zone_id else None,
        "external_device_id": row.external_device_id, "name": row.name, "device_type": row.device_type,
        "manufacturer": row.manufacturer, "model": row.model, "status": row.status,
        "archived_at": row.archived_at, "created_at": row.created_at, "updated_at": row.updated_at}


def _point_dict(row, device, integration):
    return {"point_mapping_id": str(row.point_mapping_id), "point_id": str(row.point_mapping_id),
        "device_id": str(row.device_id), "integration_id": str(device.integration_id),
        "zone_id": str(row.zone_id) if row.zone_id else None,
        "building_id": str(integration.building_id), "external_point_id": row.external_point_id,
        "logical_signal": row.logical_signal, "data_type": row.data_type, "unit": row.unit,
        "readable": row.readable, "writable": row.writable, "metadata": dict(row.metadata_json or {}),
        "mapping_status": row.mapping_status, "mapping_confidence": row.mapping_confidence,
        "mapping_source": row.mapping_source, "created_at": row.created_at, "updated_at": row.updated_at}


def _audit(request, user, action, resource, resource_id, building_id, metadata=None):
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action=action, resource=resource, resource_id=resource_id, building_id=building_id,
        success=True, metadata=metadata)


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
            session.add(IntegrationLifecycleEventRecord(integration_id=row.integration_id,
                event_type="INTEGRATION_CONFIGURED", previous_state=None,
                new_state="CONFIGURED", source="operator", simulated=True,
                occurred_at=datetime.now(timezone.utc)))
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
        configuration_changed = "configuration" in patch and patch["configuration"] != row.configuration
        previous_connection = row.connection_state
        previous_commissioning = row.commissioning_state
        for key, value in patch.items():
            setattr(row, key, value)
        if configuration_changed:
            row.connection_state = "DISCONNECTED"
            row.commissioning_state = "CONFIGURED"
            row.last_error = None
            session.add(IntegrationLifecycleEventRecord(integration_id=row.integration_id,
                event_type="CONFIGURATION_CHANGED", previous_state=previous_connection,
                new_state="DISCONNECTED", source="operator", simulated=True,
                occurred_at=datetime.now(timezone.utc)))
            if previous_commissioning != "CONFIGURED":
                session.add(IntegrationLifecycleEventRecord(integration_id=row.integration_id,
                    event_type="COMMISSIONING_CONFIGURED", previous_state=previous_commissioning,
                    new_state="CONFIGURED", source="operator", simulated=True,
                    occurred_at=datetime.now(timezone.utc)))
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
        previous_state = row.commissioning_state
        row.connection_state = "DISCONNECTED"
        row.commissioning_state = "BLOCKED"
        row.archived_at = datetime.now(timezone.utc)
        row.updated_at = row.archived_at
        session.add(IntegrationLifecycleEventRecord(integration_id=row.integration_id,
            event_type="INTEGRATION_DISABLED", previous_state=previous_state,
            new_state="BLOCKED", source="operator", simulated=True,
            occurred_at=row.archived_at))
        result = _integration_dict(row)
    _audit(request, user, "integration_disabled", "integration", result["integration_id"], result["building_id"])
    return result


@router.post("/integrations/{integration_id}/test-connection")
def test_integration_configuration(integration_id: str, request: Request,
                                  user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        result = request.app.state.integration_connection_tester.test(status=row.status, configuration=row.configuration or {})
        row.configuration_tested_at = datetime.now(timezone.utc)
        row.last_error = None if result.result == "CONFIGURATION_VALID" else "CONFIGURATION_INCOMPLETE"
        session.add(IntegrationLifecycleEventRecord(integration_id=row.integration_id,
            event_type="CONFIGURATION_VALIDATION", previous_state=row.commissioning_state,
            new_state=row.commissioning_state, source="configuration_only_tester",
            simulated=True, occurred_at=datetime.now(timezone.utc)))
        payload = {"integration_id": str(row.integration_id), **result.__dict__,
                   "connection_state": row.connection_state,
                   "commissioning_state": row.commissioning_state,
                   "physical_connection_attempted": False}
    _audit(request, user, "integration_configuration_validated", "integration", integration_id,
           str(row.building_id))
    return payload


@router.post("/integrations/{integration_id}/discover")
def discover_devices(integration_id: str, request: Request,
                     user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        result = request.app.state.discovery_provider.discover(row.integration_type)
        upserted_devices, upserted_points = [], []
        if result.performed:
            for candidate in result.candidates:
                device = session.scalars(select(DeviceRecord).where(
                    DeviceRecord.integration_id == row.integration_id,
                    DeviceRecord.external_device_id == candidate["external_device_id"])).first()
                if device is None:
                    device = DeviceRecord(integration_id=row.integration_id,
                        external_device_id=candidate["external_device_id"], name=candidate["name"],
                        device_type=candidate["device_type"], status="CONFIGURED")
                    session.add(device)
                    session.flush()
                upserted_devices.append(str(device.device_id))
                for candidate_point in candidate.get("points", []):
                    point = session.scalars(select(PointMappingRecord).where(
                        PointMappingRecord.device_id == device.device_id,
                        PointMappingRecord.external_point_id == candidate_point["external_point_id"])).first()
                    if point is None:
                        suggested, confidence, _ = suggest_mapping(
                            signal=candidate_point["logical_signal"], unit=candidate_point.get("unit"),
                            data_type=candidate_point["data_type"], readable=candidate_point["readable"],
                            zone_owned=device.zone_id is not None)
                        point = PointMappingRecord(device_id=device.device_id, zone_id=device.zone_id,
                            external_point_id=candidate_point["external_point_id"],
                            logical_signal=candidate_point["logical_signal"], data_type=candidate_point["data_type"],
                            unit=candidate_point.get("unit"), readable=candidate_point["readable"],
                            writable=candidate_point["writable"],
                            metadata_json={"name": candidate_point["name"], "source": "SIMULATED_FIXTURE",
                                "capabilities": candidate.get("capabilities", []),
                                "protocol": candidate_point["protocol"],
                                "discovery_timestamp": candidate_point["discovery_timestamp"],
                                "quality_status": candidate_point["quality_status"],
                                "error": candidate_point["error"]},
                            mapping_status=suggested, mapping_confidence=confidence,
                            mapping_source="SIMULATED_FIXTURE" if suggested == "SUGGESTED" else None)
                        session.add(point)
                        session.flush()
                    upserted_points.append(str(point.point_mapping_id))
            _lifecycle(session, row, "SIMULATED_DISCOVERY", "DISCOVERY_REVIEW",
                       source="SIMULATED_FIXTURE", simulated=True)
        payload = {"integration_id": str(row.integration_id), "simulated": result.simulated,
            "discovery_performed": result.performed, "candidates": list(result.candidates),
            "devices": upserted_devices, "points": upserted_points,
            "message": result.message}
    _audit(request, user, "integration_discovery_requested", "integration", integration_id,
           str(row.building_id), metadata={"simulated": result.simulated, "performed": result.performed})
    return payload


@router.get("/integrations/{integration_id}/commissioning")
def get_commissioning(integration_id: str, request: Request,
                      user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id)
        devices = session.scalars(select(DeviceRecord).where(
            DeviceRecord.integration_id == integration.integration_id,
            DeviceRecord.archived_at.is_(None))).all()
        device_ids = [item.device_id for item in devices]
        points = session.scalars(select(PointMappingRecord).where(
            PointMappingRecord.device_id.in_(device_ids))).all() if device_ids else []
        report = evaluate_commissioning(integration, devices, points,
            request.app.state.telemetry_service, request.app.state.configuration_repository,
            DataQualityGate())
        return {"integration_id": str(integration.integration_id),
                "building_id": str(integration.building_id), **report}


@router.post("/integrations/{integration_id}/commissioning/evaluate")
def evaluate_integration_commissioning(integration_id: str, request: Request,
                                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """Evaluate and persist read-only readiness metadata; this never writes to hardware."""
    with _session(request).begin() as session:
        integration = _integration(session, request, user, integration_id, write=True)
        devices = session.scalars(select(DeviceRecord).where(
            DeviceRecord.integration_id == integration.integration_id,
            DeviceRecord.archived_at.is_(None))).all()
        device_ids = [item.device_id for item in devices]
        points = session.scalars(select(PointMappingRecord).where(
            PointMappingRecord.device_id.in_(device_ids))).all() if device_ids else []
        report = evaluate_commissioning(integration, devices, points,
            request.app.state.telemetry_service, request.app.state.configuration_repository,
            DataQualityGate(), source_capability_verified=False)
        previous = integration.commissioning_state
        integration.commissioning_state = report["state"]
        if previous != report["state"]:
            session.add(IntegrationLifecycleEventRecord(integration_id=integration.integration_id,
                event_type="COMMISSIONING_EVALUATED", previous_state=previous,
                new_state=report["state"], source="operator_evaluation",
                simulated=report["simulated"], occurred_at=datetime.now(timezone.utc)))
        payload = {"integration_id": str(integration.integration_id),
            "building_id": str(integration.building_id), **report}
    _audit(request, user, "integration_commissioning_evaluated", "integration", integration_id,
           payload["building_id"], metadata={"state": report["state"], "simulated": report["simulated"]})
    return payload


@router.get("/integrations/{integration_id}/health")
def get_integration_health(integration_id: str, request: Request,
                           user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id)
        devices = session.scalars(select(DeviceRecord).where(
            DeviceRecord.integration_id == integration.integration_id,
            DeviceRecord.archived_at.is_(None))).all()
        device_ids = [item.device_id for item in devices]
        points = session.scalars(select(PointMappingRecord).where(
            PointMappingRecord.device_id.in_(device_ids))).all() if device_ids else []
        signals = []
        for point in points:
            observation = None
            if point.zone_id is not None:
                scope = request.app.state.configuration_repository.telemetry_scope(str(point.zone_id))
                if scope:
                    rows = request.app.state.telemetry_service.list_zone(
                        organization_id=scope["organization_id"], building_id=scope["building_id"],
                        zone_id=scope["database_zone_id"], signal=point.logical_signal, limit=1)
                    observation = rows[0] if rows else None
            assessment = (DataQualityGate().assess(point.logical_signal, observation.value,
                source=observation.source or "unknown", observation_timestamp=observation.observed_at,
                simulated=bool(observation.simulated), numeric=True,
                freshness_signal={"cooling_setpoint": "setpoint", "power": "energy",
                    "cost": "energy", "tariff_rate": "tariff"}.get(
                    point.logical_signal, point.logical_signal)) if observation else None)
            if assessment and observation.quality_state and observation.quality_state != QualityState.VALID.value:
                try:
                    upstream_state = QualityState(observation.quality_state)
                except ValueError:
                    upstream_state = QualityState.INVALID
                assessment = assessment.model_copy(update={"state": upstream_state,
                    "reason_code": f"PERSISTED_{upstream_state.value}"})
            signals.append({"point_mapping_id": str(point.point_mapping_id), "signal": point.logical_signal,
                "mapping_status": point.mapping_status,
                "observation": observation.model_dump(mode="json") if observation else None,
                "quality_state": assessment.state.value if assessment else "MISSING",
                "quality_reason": assessment.reason_code if assessment else "OBSERVATION_MISSING",
                "source": observation.source if observation else None,
                "simulated": bool(observation.simulated) if observation else None})
        return {"integration_id": str(integration.integration_id), "building_id": str(integration.building_id),
            "connection_state": integration.connection_state, "last_seen_at": integration.last_seen_at,
            "last_error": integration.last_error, "simulated": True,
            "physical_connection_implemented": False, "signals": signals}


@router.get("/integrations/{integration_id}/lifecycle")
def get_integration_lifecycle(integration_id: str, request: Request,
                              user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id)
        rows = session.scalars(select(IntegrationLifecycleEventRecord).where(
            IntegrationLifecycleEventRecord.integration_id == integration.integration_id)
            .order_by(IntegrationLifecycleEventRecord.occurred_at)).all()
        return {"integration_id": str(integration.integration_id),
            "connection_state": integration.connection_state,
            "commissioning_state": integration.commissioning_state,
            "transitions": [{"event_type": row.event_type, "previous_state": row.previous_state,
                "new_state": row.new_state, "source": row.source,
                "simulated": row.simulated, "occurred_at": row.occurred_at}
                for row in rows]}


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
        zone_uuid = None
        if body.zone_id:
            zone = request.app.state.configuration_repository.resolve_zone(body.zone_id)
            if zone is None or zone.building_id != str(integration.building_id):
                raise HTTPException(404, "Zone not found in this building")
            zone_uuid = _uuid(zone.database_zone_id)
        row = DeviceRecord(device_id=uuid4(), integration_id=integration.integration_id,
            zone_id=zone_uuid,
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
            patch["zone_id"] = _uuid(zone.database_zone_id)
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
        zone_uuid = None
        if body.zone_id:
            zone = request.app.state.configuration_repository.resolve_zone(body.zone_id)
            if zone is None or zone.building_id != str(integration.building_id):
                raise HTTPException(404, "Zone not found in this building")
            zone_uuid = _uuid(zone.database_zone_id)
        mapping_status, confidence, mapping_source = (body.mapping_status,
            body.mapping_confidence, "OPERATOR_SUGGESTION" if body.mapping_status == "SUGGESTED" else "OPERATOR")
        if mapping_status == "UNMAPPED":
            suggested, suggestion_confidence, _ = suggest_mapping(signal=body.logical_signal,
                unit=body.unit, data_type=body.data_type, readable=body.readable,
                zone_owned=zone_uuid is not None)
            if suggested == "SUGGESTED":
                mapping_status, confidence, mapping_source = suggested, suggestion_confidence, "DETERMINISTIC_SUGGESTION"
        row = PointMappingRecord(point_mapping_id=uuid4(), device_id=device.device_id,
            zone_id=zone_uuid,
            external_point_id=body.external_point_id, logical_signal=body.logical_signal,
            data_type=body.data_type, unit=body.unit, readable=body.readable, writable=body.writable,
            metadata_json=body.metadata, mapping_status=mapping_status,
            mapping_confidence=confidence, mapping_source=mapping_source)
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


@router.get("/point-mappings/{point_id}/latest-observation")
def get_latest_point_observation(point_id: str, request: Request,
                                 user=Depends(require_permission(Permission.BUILDING_READ))):
    with _session(request)() as session:
        row, device, integration = _point(session, request, user, point_id)
        if row.mapping_status != "CONFIRMED" or row.zone_id is None:
            return {"observation": None, "reason": "MAPPING_NOT_INGESTIBLE"}
        scope = request.app.state.configuration_repository.telemetry_scope(str(row.zone_id))
        if scope is None:
            return {"observation": None, "reason": "ZONE_NOT_CONFIGURED"}
        signal = row.logical_signal
        observations = request.app.state.telemetry_service.list_zone(
            organization_id=scope["organization_id"], building_id=scope["building_id"],
            zone_id=scope["database_zone_id"], signal=signal, limit=1)
        latest = observations[0].model_dump(mode="json") if observations else None
        return {"observation": latest}


@router.post("/point-mappings/{point_id}/simulated-observation")
def create_simulated_point_observation(point_id: str, body: SimulatedObservationRequest,
                                       request: Request,
                                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """Record one explicit demo value; never performs network/device I/O."""
    with _session(request)() as session:
        point, device, integration = _point(session, request, user, point_id, write=True)
        provider = ExplicitValueSimulatedProvider()
        emitted = provider.observations(integration_id=str(integration.integration_id),
            device_id=str(device.device_id), points=[point],
            values={str(point.point_mapping_id): body.value}, observed_at=body.observed_at,
            runtime_input=True)
    if len(emitted) != 1:
        raise HTTPException(status_code=409, detail={"code": "MAPPING_NOT_CONFIRMED_OR_READABLE",
            "message": "A confirmed, readable point mapping is required."})
    result = request.app.state.provider_observation_ingestion_service.ingest(emitted[0])
    if not result.accepted:
        raise HTTPException(status_code=422, detail={"code": "OBSERVATION_REJECTED",
            "reason_code": result.reason_code, "quality_state": result.quality_state.value
            if result.quality_state else None})
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="simulated_observation_ingested", resource="point_mapping",
        resource_id=point_id, building_id=str(integration.building_id), success=True,
        metadata={"signal": result.signal, "simulated": True,
                  "runtime_input_applied": result.runtime_input_applied})
    with _session(request).begin() as session:
        current = session.get(IntegrationRecord, integration.integration_id)
        if current is not None and (current.last_seen_at is None or
                result.observed_at.astimezone(timezone.utc) > _aware(current.last_seen_at).astimezone(timezone.utc)):
            current.last_seen_at = result.observed_at
    return result


@router.patch("/point-mappings/{point_id}")
def update_point(point_id: str, body: PointUpdate, request: Request,
                 user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    patch = body.model_dump(exclude_unset=True)
    if "metadata" in patch:
        patch["metadata_json"] = patch.pop("metadata")
    with _session(request).begin() as session:
        row, device, integration = _point(session, request, user, point_id, write=True)
        if patch.get("zone_id"):
            zone = request.app.state.configuration_repository.resolve_zone(patch["zone_id"])
            if zone is None or zone.building_id != str(integration.building_id):
                raise HTTPException(404, "Zone not found in this building")
            patch["zone_id"] = _uuid(zone.database_zone_id)
        for key, value in patch.items():
            setattr(row, key, value)
        if row.mapping_status == "UNMAPPED" and row.zone_id is not None:
            suggested, confidence, _ = suggest_mapping(signal=row.logical_signal, unit=row.unit,
                data_type=row.data_type, readable=row.readable, zone_owned=True)
            if suggested == "SUGGESTED":
                row.mapping_status, row.mapping_confidence = suggested, confidence
                row.mapping_source = "DETERMINISTIC_SUGGESTION"
        if patch.get("mapping_status") == "CONFIRMED":
            row.mapping_source = "OPERATOR"
        row.updated_at = datetime.now(timezone.utc)
        result = _point_dict(row, device, integration)
    _audit(request, user, "point_mapping_updated", "point_mapping", result["point_mapping_id"], result["building_id"])
    return result


def _mapping_decision(point_id: str, decision: str, request: Request, user):
    with _session(request).begin() as session:
        row, device, integration = _point(session, request, user, point_id, write=True)
        if decision == "CONFIRMED":
            compatibility, _, reason = suggest_mapping(signal=row.logical_signal, unit=row.unit,
                data_type=row.data_type, readable=row.readable,
                zone_owned=row.zone_id is not None)
            if compatibility != "SUGGESTED":
                raise HTTPException(409, detail={"code": "MAPPING_INCOMPATIBLE", "reason": reason})
        row.mapping_status = decision
        row.mapping_source = "OPERATOR"
        row.updated_at = datetime.now(timezone.utc)
        result = _point_dict(row, device, integration)
        _lifecycle(session, integration, f"MAPPING_{decision}",
            "MAPPING_REVIEW" if decision in {"CONFIRMED", "REJECTED", "INACTIVE"} else "DISCOVERY_REVIEW",
            source="operator", simulated=True)
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
