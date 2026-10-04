"""Authenticated, read-only status view for the configured building-local edge process."""
from fastapi import APIRouter, Depends, HTTPException, Request

from backend.schemas.edge import EdgeHealth
from backend.security.dependencies import require_building_access, require_permission
from backend.security.roles import Permission

router = APIRouter(tags=["Edge Connector Foundation"])


@router.get("/edge/status/{building_id}", response_model=EdgeHealth)
def get_edge_status(building_id: str, request: Request,
                    user=Depends(require_permission(Permission.BUILDING_READ))):
    repository = request.app.state.configuration_repository
    resolved_id = repository.resolve_building_id(building_id)
    if resolved_id is None:
        raise HTTPException(404, "Building not found")
    require_building_access(resolved_id, user, request)
    return request.app.state.edge_connector.status(resolved_id)
