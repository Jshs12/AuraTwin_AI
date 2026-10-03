"""Building-scoped supervised integration APIs with a read-only adapter boundary."""
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database.models import (BuildingRecord, DeviceRecord, IntegrationRecord,
    IntegrationLifecycleEventRecord, PointMappingRecord)
from backend.schemas.integration_config import (DeviceCreate, DeviceUpdate,
    IntegrationCreate, IntegrationUpdate, PointCreate, PointUpdate)
from backend.schemas.provider_observation import ProviderObservation, SimulatedObservationRequest
from backend.integrations.lifecycle import ConnectionState, IntegrationLifecycleManager
from backend.integrations.supervised import (AdapterTimeout, ReadOnlyAdapterUnavailable,
    run_bounded)
from backend.integrations.discovery import DiscoveryResult
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


def _audit(request, user, action, resource, resource_id, building_id, metadata=None, success=True):
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action=action, resource=resource, resource_id=resource_id, building_id=building_id,
        success=success, metadata=metadata)


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
        if configuration_changed and row.connection_state in {
                ConnectionState.CONNECTING.value, ConnectionState.CONNECTED.value,
                ConnectionState.DEGRADED.value}:
            raise HTTPException(409, detail={"code": "CONNECTION_ATTEMPT_IN_PROGRESS"})
        if patch.get("status") == "DISABLED" and row.connection_state in {
                ConnectionState.CONNECTING.value, ConnectionState.CONNECTED.value,
                ConnectionState.DEGRADED.value}:
            raise HTTPException(409, detail={"code": "DISCONNECT_BEFORE_DISABLE"})
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
        if row.connection_state in {ConnectionState.CONNECTING.value,
                ConnectionState.CONNECTED.value, ConnectionState.DEGRADED.value}:
            raise HTTPException(409, detail={"code": "CONNECTION_ATTEMPT_IN_PROGRESS"})
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


def _adapter_capabilities(adapter):
    capabilities = getattr(adapter, "capabilities", None)
    if capabilities is None:
        return {"can_read": False, "can_write": False, "physical_io": False,
                "can_discover_devices": False, "can_discover_points": False,
                "can_observe": False, "can_report_health": False}
    result = dict(vars(capabilities))
    # This phase categorically forbids physical actuator writes, even if a future
    # driver accidentally advertises them.
    result["can_write"] = False
    return result


def _safe_adapter_health(adapter, timeout_seconds=5.0):
    if adapter is None:
        return None
    try:
        raw = run_bounded(adapter.health, timeout_seconds)
    except Exception:
        raw = None
    if raw is None:
        return SimpleNamespace(state="ERROR", simulated=True, last_error="PROVIDER_FAILURE")
    allowed_states = {item.value for item in ConnectionState} | {"UNAVAILABLE", "UNKNOWN"}
    state = raw.state if getattr(raw, "state", None) in allowed_states else "UNKNOWN"
    safe_errors = IntegrationLifecycleManager.SAFE_ERROR_CODES | {"ADAPTER_UNAVAILABLE"}
    error = getattr(raw, "last_error", None)
    return SimpleNamespace(state=state,
        simulated=bool(getattr(raw, "simulated", True)),
        last_error=error if error in safe_errors else ("PROVIDER_FAILURE" if error else None))


def _public_discovery_candidates(candidates, *, simulated):
    """Expose only documented discovery fields, never arbitrary adapter metadata."""
    device_fields = ("external_device_id", "name", "device_type", "protocol", "source",
                     "discovery_timestamp", "quality_status")
    point_fields = ("external_point_id", "name", "logical_signal", "data_type", "unit",
                    "readable", "writable", "protocol", "source", "discovery_timestamp",
                    "quality_status")
    safe = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        row = {key: candidate[key] for key in device_fields if key in candidate}
        if not simulated:
            row["source"] = "read_only_adapter"
            row["quality_status"] = "UNASSESSED"
        row["points"] = []
        for point in candidate.get("points", []):
            if not isinstance(point, dict):
                continue
            safe_point = {key: point[key] for key in point_fields if key in point}
            if not simulated:
                safe_point.update({"source": "read_only_adapter", "quality_status": "UNASSESSED",
                                   "writable": False})
            row["points"].append(safe_point)
        safe.append(row)
    return safe


def _valid_device_discovery(candidates):
    if not isinstance(candidates, (tuple, list)) or len(candidates) > 500:
        return False
    for device in candidates:
        if not isinstance(device, dict):
            return False
        if (not isinstance(device.get("external_device_id"), str)
                or not 1 <= len(device["external_device_id"]) <= 200
                or not isinstance(device.get("name"), str)
                or not 1 <= len(device["name"]) <= 200
                or not isinstance(device.get("device_type"), str)
                or not 1 <= len(device["device_type"]) <= 80
                or not isinstance(device.get("points", []), (list, tuple))):
            return False
        for point in device.get("points", []):
            if (not isinstance(point, dict)
                    or not isinstance(point.get("external_point_id"), str)
                    or not 1 <= len(point["external_point_id"]) <= 250
                    or not isinstance(point.get("name"), str)
                    or not isinstance(point.get("logical_signal"), str)
                    or not isinstance(point.get("data_type"), str)
                    or point.get("readable") is not True
                    or not isinstance(point.get("writable"), bool)):
                return False
    return True


@router.post("/integrations/{integration_id}/connect")
def connect_integration(integration_id: str, request: Request,
                        user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """Explicitly supervise one adapter connection attempt; no automatic retry."""
    registry = request.app.state.integration_adapter_registry
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        if row.status != "CONFIGURED":
            raise HTTPException(409, detail={"code": "INTEGRATION_DISABLED"})
        current = ConnectionState(row.connection_state)
        if current == ConnectionState.CONNECTING:
            raise HTTPException(409, detail={"code": "CONNECTION_ATTEMPT_IN_PROGRESS"})
        if current == ConnectionState.CONNECTED:
            active_adapter = registry.active(integration_id)
            if active_adapter is None:
                # A persisted CONNECTED state cannot survive process memory loss.
                # Reconcile it before attempting a new, explicit connection.
                IntegrationLifecycleManager.transition(session, row, ConnectionState.DISCONNECTED,
                    source="application_recovery", simulated=True)
                current = ConnectionState.DISCONNECTED
            else:
                adapter_health = _safe_adapter_health(active_adapter,
                    float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0)))
                active_simulated = bool(adapter_health.simulated) if adapter_health else True
                physical = bool(getattr(
                    getattr(active_adapter, "capabilities", None), "physical_io", False)
                    and not active_simulated)
                return {"integration_id": integration_id, "connection_state": current.value,
                        "connection_established": True, "simulated": active_simulated,
                        "physical_connection_established": physical,
                        "capabilities": _adapter_capabilities(active_adapter)}
        if current == ConnectionState.ERROR and registry.active(integration_id) is not None:
            raise HTTPException(409, detail={"code": "DISCONNECT_BEFORE_RETRY"})
        source = "adapter_" + row.integration_type.lower()
        if current in {ConnectionState.ERROR, ConnectionState.DEGRADED}:
            IntegrationLifecycleManager.transition(session, row, ConnectionState.DISCONNECTED,
                source=source, simulated=True)
        IntegrationLifecycleManager.transition(session, row, ConnectionState.CONNECTING,
            source=source, simulated=True)
        configuration = dict(row.configuration or {})
        integration_type = row.integration_type
        timeout_seconds = float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0))
    failure_code = None
    adapter_holder = []

    def initialize_and_test():
        created = registry.create(integration_id, integration_type, configuration)
        if created is None:
            raise ReadOnlyAdapterUnavailable("ADAPTER_UNAVAILABLE")
        adapter_holder.append(created)
        return created, created.test_connection()

    try:
        adapter, result = run_bounded(initialize_and_test, timeout_seconds)
        succeeded = bool(getattr(result, "succeeded", False))
        simulated = bool(getattr(result, "simulated", True))
        physical_io = bool(getattr(result, "physical_io", False))
        failure_code = getattr(result, "reason_code", None)
        capabilities = getattr(adapter, "capabilities", None)
        if succeeded and bool(getattr(capabilities, "can_write", False)):
            succeeded, failure_code = False, "ADAPTER_UNAVAILABLE"
        if succeeded and not simulated and not bool(getattr(capabilities, "physical_io", False)):
            succeeded, failure_code = False, "ADAPTER_UNAVAILABLE"
        if not succeeded and failure_code not in IntegrationLifecycleManager.SAFE_ERROR_CODES:
            failure_code = "CONNECTION_FAILED"
    except AdapterTimeout:
        adapter = adapter_holder[0] if adapter_holder else None
        succeeded, simulated, physical_io, failure_code = False, True, False, "CONNECT_TIMEOUT"
    except ReadOnlyAdapterUnavailable:
        adapter = adapter_holder[0] if adapter_holder else None
        succeeded, simulated, physical_io, failure_code = False, True, False, "ADAPTER_UNAVAILABLE"
    except Exception:
        adapter = adapter_holder[0] if adapter_holder else None
        succeeded, simulated, physical_io, failure_code = False, True, False, "CONNECTION_FAILED"
    if adapter is None:
        capabilities = None
    else:
        capabilities = getattr(adapter, "capabilities", None)
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        if row.connection_state != ConnectionState.CONNECTING.value:
            try:
                run_bounded(adapter.close, timeout_seconds)
            except Exception:
                pass
            raise HTTPException(409, detail={"code": "CONNECTION_STATE_CHANGED"})
        if succeeded:
            IntegrationLifecycleManager.transition(session, row, ConnectionState.CONNECTED,
                source=source, simulated=simulated)
            registry.set_active(integration_id, adapter)
            state = ConnectionState.CONNECTED
        else:
            IntegrationLifecycleManager.transition(session, row, ConnectionState.ERROR,
                source=source, simulated=simulated, error_code=failure_code or "CONNECTION_FAILED")
            state = ConnectionState.ERROR
    _audit(request, user, "integration_connection_attempted", "integration", integration_id,
           str(row.building_id), metadata={"connection_state": state.value, "simulated": simulated},
           success=succeeded)
    return {"integration_id": integration_id, "connection_state": state.value,
        "connection_established": succeeded, "simulated": simulated,
        "physical_connection_established": bool(succeeded and physical_io and not simulated
            and getattr(getattr(adapter, "capabilities", None), "physical_io", False)),
        "error_code": failure_code, "capabilities": _adapter_capabilities(adapter),
        "retry": "EXPLICIT_OPERATOR_ACTION_ONLY"}


@router.post("/integrations/{integration_id}/disconnect")
def disconnect_integration(integration_id: str, request: Request,
                           user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    registry = request.app.state.integration_adapter_registry
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        state = ConnectionState(row.connection_state)
        if state == ConnectionState.CONNECTING:
            raise HTTPException(409, detail={"code": "CONNECTION_ATTEMPT_IN_PROGRESS"})
        adapter = registry.active(integration_id)
        source = "adapter_disconnect"
        timeout_seconds = float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0))
        if state == ConnectionState.DISCONNECTED:
            return {"integration_id": integration_id, "connection_state": state.value,
                    "disconnected": True, "simulated": True}
    failure_code = None
    adapter_health = _safe_adapter_health(adapter,
        float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0)))
    simulated = bool(adapter_health.simulated) if adapter_health is not None else True
    if adapter is not None:
        try:
            run_bounded(adapter.close, timeout_seconds)
        except AdapterTimeout:
            failure_code = "CONNECT_TIMEOUT"
        except Exception:
            failure_code = "CONNECTION_FAILED"
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        if failure_code:
            if state != ConnectionState.ERROR:
                IntegrationLifecycleManager.transition(session, row, ConnectionState.ERROR,
                    source=source, simulated=simulated, error_code=failure_code)
        else:
            IntegrationLifecycleManager.transition(session, row, ConnectionState.DISCONNECTED,
                source=source, simulated=simulated)
    if failure_code is None:
        registry.remove_active(integration_id)
    _audit(request, user, "integration_disconnected", "integration", integration_id, str(row.building_id))
    if failure_code:
        return {"integration_id": integration_id, "connection_state": "ERROR",
                "disconnected": False, "error_code": failure_code, "simulated": simulated}
    return {"integration_id": integration_id, "connection_state": "DISCONNECTED",
            "disconnected": True, "simulated": simulated}


@router.post("/integrations/{integration_id}/poll")
def poll_integration(integration_id: str, request: Request,
                     user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """One explicit read-only poll; only adapter-produced normalized observations enter ingestion."""
    registry = request.app.state.integration_adapter_registry
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id, write=True)
        adapter = registry.active(integration_id)
        if (integration.connection_state != ConnectionState.CONNECTED.value or adapter is None
                or not getattr(getattr(adapter, "capabilities", None), "can_observe", False)):
            raise HTTPException(409, detail={"code": "READ_ONLY_ADAPTER_UNAVAILABLE",
                "message": "No connected read-only adapter is available."})
        points = session.scalars(select(PointMappingRecord).join(DeviceRecord).where(
            DeviceRecord.integration_id == integration.integration_id,
            DeviceRecord.status == "CONFIGURED", PointMappingRecord.mapping_status == "CONFIRMED",
            PointMappingRecord.readable.is_(True))).all()
        point_data = [(point.point_mapping_id, point.external_point_id, point.device_id,
                       point.logical_signal, point.unit)
                      for point in points]
        timeout_seconds = float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0))
    results = []
    adapter_failure = None
    for point_id, external_id, device_id, logical_signal, unit in point_data:
        try:
            observation = run_bounded(lambda: adapter.observe(external_id), timeout_seconds)
            if not isinstance(observation, ProviderObservation):
                adapter_failure = "OBSERVATION_ERROR"
                results.append({"point_mapping_id": str(point_id), "accepted": False,
                                "reason_code": "OBSERVATION_FORMAT_INVALID"})
                continue
            if (observation.integration_id != integration_id or observation.device_id != str(device_id)
                    or observation.point_mapping_id != str(point_id)):
                adapter_failure = "OBSERVATION_ERROR"
                results.append({"point_mapping_id": str(point_id), "accepted": False,
                                "reason_code": "OBSERVATION_IDENTITY_MISMATCH"})
                continue
            result = request.app.state.provider_observation_ingestion_service.ingest(observation)
            results.append({**result.model_dump(mode="json"), "logical_signal": logical_signal,
                            "unit": unit})
        except AdapterTimeout:
            adapter_failure = "CONNECT_TIMEOUT"
            results.append({"point_mapping_id": str(point_id), "accepted": False,
                            "reason_code": "OBSERVATION_TIMEOUT"})
        except Exception:
            adapter_failure = "OBSERVATION_ERROR"
            results.append({"point_mapping_id": str(point_id), "accepted": False,
                            "reason_code": "OBSERVATION_ERROR"})
    health = _safe_adapter_health(adapter, timeout_seconds)
    if adapter_failure:
        with _session(request).begin() as session:
            current = _integration(session, request, user, integration_id, write=True)
            if current.connection_state == ConnectionState.CONNECTED.value:
                IntegrationLifecycleManager.transition(session, current, ConnectionState.DEGRADED,
                    source="read_only_adapter", simulated=bool(health.simulated),
                    error_code=adapter_failure)
            response_state = current.connection_state
    else:
        response_state = ConnectionState.CONNECTED.value
    _audit(request, user, "integration_read_only_poll", "integration", integration_id,
           str(integration.building_id), metadata={"observation_count": len(results)},
           success=adapter_failure is None)
    return {"integration_id": integration_id, "connection_state": response_state,
        "simulated": bool(health.simulated),
        "read_only": True, "write_capability": False, "observations": results}


@router.post("/devices/{device_id}/discover-points")
def discover_adapter_points(device_id: str, request: Request,
                           user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """One explicit read-only point discovery call; discovered mappings are never confirmed."""
    registry = request.app.state.integration_adapter_registry
    with _session(request)() as session:
        device, integration = _device(session, request, user, device_id, write=True)
        adapter = registry.active(str(integration.integration_id))
        capabilities = getattr(adapter, "capabilities", None)
        if (integration.connection_state != ConnectionState.CONNECTED.value or adapter is None
                or not capabilities or not capabilities.can_discover_points or capabilities.can_write):
            raise HTTPException(409, detail={"code": "POINT_DISCOVERY_UNAVAILABLE",
                "message": "No connected read-only point discovery adapter is available."})
        device_identifier = device.external_device_id
        timeout_seconds = float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0))
        zone_owned = device.zone_id is not None
    try:
        candidates = run_bounded(lambda: adapter.discover_points(device_identifier), timeout_seconds)
    except AdapterTimeout:
        raise HTTPException(504, detail={"code": "DISCOVERY_TIMEOUT"}) from None
    except Exception:
        raise HTTPException(502, detail={"code": "DISCOVERY_FAILED"}) from None
    if not isinstance(candidates, (tuple, list)) or len(candidates) > 1000:
        raise HTTPException(502, detail={"code": "DISCOVERY_FORMAT_INVALID"})
    safe_rows = []
    adapter_health = _safe_adapter_health(adapter,
        float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0)))
    source = "simulated_adapter_discovery" if bool(adapter_health.simulated) else "read_only_adapter_discovery"
    with _session(request).begin() as session:
        device, integration = _device(session, request, user, device_id, write=True)
        for candidate in candidates:
            if not isinstance(candidate, dict):
                raise HTTPException(502, detail={"code": "DISCOVERY_FORMAT_INVALID"})
            external_id = candidate.get("external_point_id")
            signal = candidate.get("logical_signal")
            data_type = candidate.get("data_type")
            readable = candidate.get("readable")
            writable = candidate.get("writable", False)
            unit = candidate.get("unit")
            name = candidate.get("name")
            if (not isinstance(external_id, str) or not external_id or len(external_id) > 250
                    or not isinstance(signal, str) or len(signal) > 100
                    or not isinstance(data_type, str) or len(data_type) > 40
                    or readable is not True or not isinstance(writable, bool)
                    or (unit is not None and (not isinstance(unit, str) or len(unit) > 40))
                    or (name is not None and (not isinstance(name, str) or len(name) > 240))):
                raise HTTPException(502, detail={"code": "DISCOVERY_FORMAT_INVALID"})
            existing = session.scalars(select(PointMappingRecord).where(
                PointMappingRecord.device_id == device.device_id,
                PointMappingRecord.external_point_id == external_id)).first()
            if existing is None:
                mapping_status, confidence, _ = suggest_mapping(signal=signal, unit=unit,
                    data_type=data_type, readable=readable, zone_owned=zone_owned)
                existing = PointMappingRecord(device_id=device.device_id, zone_id=device.zone_id,
                    external_point_id=external_id, logical_signal=signal, data_type=data_type,
                    unit=unit, readable=readable, writable=False,
                    metadata_json={"name": name, "source": source, "simulated": source.startswith("simulated")},
                    mapping_status=mapping_status, mapping_confidence=confidence,
                    mapping_source=source if mapping_status == "SUGGESTED" else None)
                session.add(existing)
                session.flush()
            safe_rows.append(_point_dict(existing, device, integration))
    _audit(request, user, "integration_point_discovery_requested", "device", device_id,
        str(integration.building_id), metadata={"point_count": len(safe_rows),
        "simulated": source.startswith("simulated")})
    return {"device_id": device_id, "integration_id": str(integration.integration_id),
        "read_only": True, "write_capability": False,
        "simulated": source.startswith("simulated"), "discovery_performed": True,
        "points": safe_rows}


@router.post("/integrations/{integration_id}/discover")
def discover_devices(integration_id: str, request: Request,
                     user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    with _session(request).begin() as session:
        row = _integration(session, request, user, integration_id, write=True)
        adapter = request.app.state.integration_adapter_registry.active(integration_id)
        adapter_caps = getattr(adapter, "capabilities", None)
        if row.connection_state == ConnectionState.CONNECTED.value and adapter is not None:
            if adapter_caps and adapter_caps.can_discover_devices and not adapter_caps.can_write:
                try:
                    result = run_bounded(adapter.discover_devices,
                        float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0)))
                except AdapterTimeout:
                    raise HTTPException(504, detail={"code": "DISCOVERY_TIMEOUT"}) from None
                except Exception:
                    raise HTTPException(502, detail={"code": "DISCOVERY_FAILED"}) from None
                if not isinstance(result, DiscoveryResult):
                    raise HTTPException(502, detail={"code": "DISCOVERY_FORMAT_INVALID"})
            else:
                result = DiscoveryResult(simulated=True, performed=False, candidates=(),
                    message="Read-only device discovery is unavailable for this adapter.")
        else:
            result = request.app.state.discovery_provider.discover(row.integration_type)
        if result.performed and not _valid_device_discovery(result.candidates):
            raise HTTPException(502, detail={"code": "DISCOVERY_FORMAT_INVALID"})
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
                        discovered_source = candidate_point.get("source", "SIMULATED_FIXTURE") if result.simulated else "read_only_adapter"
                        point = PointMappingRecord(device_id=device.device_id, zone_id=device.zone_id,
                            external_point_id=candidate_point["external_point_id"],
                            logical_signal=candidate_point["logical_signal"], data_type=candidate_point["data_type"],
                            unit=candidate_point.get("unit"), readable=candidate_point["readable"],
                            writable=candidate_point["writable"] if result.simulated else False,
                            metadata_json={"name": candidate_point["name"], "source": discovered_source,
                                "capabilities": candidate.get("capabilities", []) if result.simulated else [],
                                "protocol": candidate_point.get("protocol", candidate.get("protocol")) if result.simulated else row.integration_type,
                                "discovery_timestamp": candidate_point.get("discovery_timestamp"),
                                "quality_status": candidate_point.get("quality_status", "UNASSESSED") if result.simulated else "UNASSESSED",
                                "error": candidate_point.get("error") if result.simulated else None},
                            mapping_status=suggested, mapping_confidence=confidence,
                            mapping_source=discovered_source if suggested == "SUGGESTED" else None)
                        session.add(point)
                        session.flush()
                    upserted_points.append(str(point.point_mapping_id))
            _lifecycle(session, row, "SIMULATED_DISCOVERY" if result.simulated else "ADAPTER_DISCOVERY",
                       "DISCOVERY_REVIEW", source="SIMULATED_FIXTURE" if result.simulated else "read_only_adapter",
                       simulated=result.simulated)
        payload = {"integration_id": str(row.integration_id), "simulated": result.simulated,
            "discovery_performed": result.performed,
            "candidates": _public_discovery_candidates(result.candidates, simulated=result.simulated),
            "devices": upserted_devices, "points": upserted_points,
            "message": (result.message if result.simulated else
                ("Read-only adapter discovery completed." if result.performed else
                 "Read-only adapter discovery did not return devices."))}
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
    adapter = None
    with _session(request)() as session:
        integration = _integration(session, request, user, integration_id)
        adapter = request.app.state.integration_adapter_registry.active(integration_id)
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
        adapter_health = _safe_adapter_health(adapter,
            float(getattr(request.app.state, "integration_adapter_timeout_seconds", 5.0)))
        return {"integration_id": str(integration.integration_id), "building_id": str(integration.building_id),
            "connection_state": integration.connection_state, "last_seen_at": integration.last_seen_at,
            "last_error": integration.last_error,
            "simulated": bool(adapter_health.simulated) if adapter_health else True,
            "physical_connection_implemented": bool(adapter
                and integration.connection_state == ConnectionState.CONNECTED.value
                and adapter_health and not adapter_health.simulated
                and getattr(getattr(adapter, "capabilities", None), "physical_io", False)),
            "adapter_health": adapter_health.state if adapter_health else "NOT_CONFIGURED",
            "capabilities": _adapter_capabilities(adapter),
            "read_only": True, "write_capability": False, "signals": signals}


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
