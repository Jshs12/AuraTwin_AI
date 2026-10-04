"""Authenticated Edge Connector health and explicit observation retry action."""
from fastapi import APIRouter, Depends, HTTPException, Request
from uuid import UUID

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


@router.post("/edge/status/{building_id}/messages/{message_id}/retry")
def retry_edge_message(building_id: str, message_id: UUID, request: Request,
                       user=Depends(require_permission(Permission.INTEGRATIONS_CONFIGURE))):
    """Operator-reviewed retry for a retained failed observation; never a control command."""
    repository = request.app.state.configuration_repository
    resolved_id = repository.resolve_building_id(building_id)
    if resolved_id is None:
        raise HTTPException(404, "Building not found")
    require_building_access(resolved_id, user, request)
    edge = request.app.state.edge_connector
    status = edge.status(resolved_id)
    if status.edge_id is None:
        raise HTTPException(409, {"reason_code": status.reason_code or "EDGE_NOT_CONFIGURED"})
    result = edge.retry_failed(message_id)
    if not result["accepted"]:
        raise HTTPException(404, {"reason_code": result["reason_code"]})
    return result
