"""Authenticated organization and building configuration API."""

from fastapi import APIRouter, Depends, HTTPException, Request
from uuid import UUID

from backend.schemas.configuration import (BuildingCreate, BuildingUpdate, FloorCreate,
    FloorUpdate, ZoneCreate, ZoneUpdate)
from backend.security.dependencies import (require_permission, require_building_access,
    require_organization_access)
from backend.security.roles import Permission

router = APIRouter(tags=["Configuration"])


def _repo(request: Request):
    return request.app.state.configuration_repository


def _audit(request: Request, user, action: str, resource: str, resource_id: str,
           building_id: str | None = None):
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action=action, resource=resource, resource_id=resource_id,
        building_id=building_id, success=True)


def _building_scope(request: Request, user, building_id: str, *, configure: bool = False):
    if configure and user.role.value != "OPERATOR":
        raise HTTPException(403, "Insufficient permission")
    repo = _repo(request)
    building = repo.get_building(building_id)
    if building is None:
        raise HTTPException(404, "Building not found")
    require_building_access(building_id, user, request)
    return building


def _floor_scope(request: Request, user, floor_id: str, *, configure: bool = False):
    floor = _repo(request).get_floor(floor_id)
    if floor is None:
        raise HTTPException(404, "Floor not found")
    _building_scope(request, user, floor["building_id"], configure=configure)
    return floor


def _zone_scope(request: Request, user, zone_id: str, *, configure: bool = False):
    try:
        zone = _repo(request).resolve_zone(zone_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None
    if zone is None:
        raise HTTPException(404, "Zone not found")
    _building_scope(request, user, zone.building_id, configure=configure)
    return zone


@router.get("/organizations")
def list_organizations(request: Request, user=Depends(require_permission(Permission.BUILDING_READ))):
    return {"organizations": _repo(request).list_organizations(user)}


@router.get("/organizations/{organization_id}")
def get_organization(organization_id: str, request: Request,
                     user=Depends(require_permission(Permission.BUILDING_READ))):
    if not _repo(request).user_has_organization(user, organization_id):
        raise HTTPException(404, "Organization not found")
    result = _repo(request).get_organization(organization_id)
    if result is None:
        raise HTTPException(404, "Organization not found")
    return result


@router.get("/buildings")
def list_buildings(request: Request, organization_id: str | None = None,
                   user=Depends(require_permission(Permission.BUILDING_READ))):
    if organization_id and not _repo(request).user_has_organization(user, organization_id):
        raise HTTPException(404, "Organization not found")
    return {"buildings": _repo(request).list_building_dicts(user, organization_id)}


@router.post("/buildings", status_code=201)
def create_building(body: BuildingCreate, request: Request,
                    user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    require_organization_access(request, user, body.organization_id)
    values = body.model_dump(exclude={"organization_id"})
    values["building_key"] = f"{UUID(body.organization_id).hex}:{body.slug}"
    try:
        result = _repo(request).create_building(body.organization_id, values)
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Building slug already exists in this organization") from None
        raise
    _repo(request).assign_building_access(user.user_id, result["building_id"])
    request.app.state.audit_service.record(user_id=user.user_id, role=user.role.value,
        action="building_created", resource="building", resource_id=result["building_id"],
        building_id=result["building_id"], success=True)
    return result


@router.get("/buildings/{building_id}")
def get_building(building_id: str, request: Request,
                 user=Depends(require_permission(Permission.BUILDING_READ))):
    building = _building_scope(request, user, building_id)
    return building


@router.patch("/buildings/{building_id}")
def update_building(building_id: str, body: BuildingUpdate, request: Request,
                    user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    _building_scope(request, user, building_id, configure=True)
    try:
        result = _repo(request).update_building(building_id, body.model_dump(exclude_unset=True))
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Building slug already exists in this organization") from None
        raise
    if result is None:
        raise HTTPException(404, "Building not found")
    _audit(request, user, "building_updated", "building", result["building_id"], result["building_id"])
    return result


@router.delete("/buildings/{building_id}")
def archive_building(building_id: str, request: Request,
                     user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    _building_scope(request, user, building_id, configure=True)
    result = _repo(request).archive_building(building_id)
    if result is None:
        raise HTTPException(404, "Building not found")
    _audit(request, user, "building_archived", "building", result["building_id"], result["building_id"])
    _sync_runtime_configuration(request)
    return result


@router.get("/buildings/{building_id}/floors")
def list_floors(building_id: str, request: Request,
                user=Depends(require_permission(Permission.BUILDING_READ))):
    _building_scope(request, user, building_id)
    return {"floors": _repo(request).list_floors(building_id)}


@router.post("/buildings/{building_id}/floors", status_code=201)
def create_floor(building_id: str, body: FloorCreate, request: Request,
                 user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    _building_scope(request, user, building_id, configure=True)
    try:
        result = _repo(request).create_floor(building_id, body.model_dump())
        _audit(request, user, "floor_created", "floor", result["floor_id"], building_id)
        return result
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Floor key already exists in this building") from None
        raise


@router.get("/floors/{floor_id}")
def get_floor(floor_id: str, request: Request,
              user=Depends(require_permission(Permission.BUILDING_READ))):
    return _floor_scope(request, user, floor_id)


@router.patch("/floors/{floor_id}")
def update_floor(floor_id: str, body: FloorUpdate, request: Request,
                 user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    _floor_scope(request, user, floor_id, configure=True)
    try:
        result = _repo(request).update_floor(floor_id, body.model_dump(exclude_unset=True))
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Floor key already exists in this building") from None
        raise
    if result is None:
        raise HTTPException(404, "Floor not found")
    _audit(request, user, "floor_updated", "floor", result["floor_id"], result["building_id"])
    return result


@router.delete("/floors/{floor_id}")
def archive_floor(floor_id: str, request: Request,
                  user=Depends(require_permission(Permission.BUILDING_CONFIGURE))):
    _floor_scope(request, user, floor_id, configure=True)
    result = _repo(request).archive_floor(floor_id)
    if result is None:
        raise HTTPException(404, "Floor not found")
    _audit(request, user, "floor_archived", "floor", result["floor_id"], result["building_id"])
    _sync_runtime_configuration(request)
    return result


@router.get("/buildings/{building_id}/zones")
def list_building_zones(building_id: str, request: Request,
                        user=Depends(require_permission(Permission.ZONES_READ))):
    _building_scope(request, user, building_id)
    return {"zones": [zone.api_dict() for zone in _repo(request).list_zones(building_id=building_id)]}


@router.get("/floors/{floor_id}/zones")
def list_floor_zones(floor_id: str, request: Request,
                     user=Depends(require_permission(Permission.ZONES_READ))):
    _floor_scope(request, user, floor_id)
    return {"zones": [zone.api_dict() for zone in _repo(request).list_zones(floor_id=floor_id)]}


@router.post("/floors/{floor_id}/zones", status_code=201)
def create_zone(floor_id: str, body: ZoneCreate, request: Request,
                user=Depends(require_permission(Permission.ZONES_CREATE))):
    _floor_scope(request, user, floor_id, configure=True)
    data = body.model_dump()
    comfort = data.pop("comfort")
    zone_type = data.pop("type")
    values = {**data, "zone_type": zone_type, "comfort_min_c": comfort["min_temperature"],
              "comfort_max_c": comfort["max_temperature"]}
    try:
        result = _repo(request).create_zone(floor_id, values)
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Zone key already exists on this floor") from None
        raise
    _audit(request, user, "zone_created", "zone", result["zone_id"], result["building_id"])
    _sync_runtime_configuration(request)
    return result


def _sync_runtime_configuration(request: Request):
    runtime = request.app.state.zone_state_service
    scheduler = request.app.state.monitoring_scheduler
    scoped_buildings = {config.building_id for current in scheduler.monitored_zones
                        if (config := request.app.state.configuration_repository.resolve_zone(current)) is not None}
    runtime.refresh_configuration()
    provider = runtime.control_provider
    for configured_zone_id in runtime._zones:
        if hasattr(provider, "register_zone"):
            provider.register_zone(configured_zone_id)
    refreshed_ids = [config.zone_id for config in request.app.state.configuration_repository.runtime_zone_configs()
                     if config.building_id in scoped_buildings]
    scheduler.configure_zones(refreshed_ids)


@router.get("/zones/{zone_id}/configuration")
def get_zone_configuration(zone_id: str, request: Request,
                           user=Depends(require_permission(Permission.ZONES_READ))):
    return _zone_scope(request, user, zone_id).api_dict()


@router.patch("/zones/{zone_id}/configuration")
def update_zone(zone_id: str, body: ZoneUpdate, request: Request,
                user=Depends(require_permission(Permission.ZONES_UPDATE))):
    _zone_scope(request, user, zone_id, configure=True)
    patch = body.model_dump(exclude_unset=True)
    if "comfort" in patch and patch["comfort"] is not None:
        comfort = patch.pop("comfort")
        patch.update(comfort_min_c=comfort["min_temperature"], comfort_max_c=comfort["max_temperature"])
    if "type" in patch:
        patch["zone_type"] = patch.pop("type")
    try:
        result = _repo(request).update_zone(zone_id, patch)
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise HTTPException(409, "Zone key already exists on this floor") from None
        raise
    if result is None:
        raise HTTPException(404, "Zone not found")
    _audit(request, user, "zone_updated", "zone", result["zone_id"], result["building_id"])
    _sync_runtime_configuration(request)
    return result


@router.delete("/zones/{zone_id}/configuration")
def archive_zone(zone_id: str, request: Request,
                 user=Depends(require_permission(Permission.ZONES_DELETE))):
    _zone_scope(request, user, zone_id, configure=True)
    result = _repo(request).archive_zone(zone_id)
    if result is None:
        raise HTTPException(404, "Zone not found")
    _audit(request, user, "zone_archived", "zone", result["zone_id"], result["building_id"])
    _sync_runtime_configuration(request)
    return result
