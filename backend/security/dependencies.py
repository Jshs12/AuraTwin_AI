from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from .models import User
from .roles import Permission, Role, has_permission
from .jwt import decode_access_token
from .repository import BuildingAccessRepository, DEVELOPMENT_BUILDING_ID

bearer = HTTPBearer(auto_error=False)


def get_current_user(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = decode_access_token(credentials.credentials)
        user = request.app.state.auth_service.users.get_by_id(claims["sub"])
        if user is None or not user.active or user.role.value != claims["role"]: raise InvalidTokenError("Unknown user")
        return user
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid or expired authentication", headers={"WWW-Authenticate": "Bearer"}) from None


def require_permission(permission: Permission):
    def dependency(user: User = Depends(get_current_user)):
        if not has_permission(user.role, permission): raise HTTPException(403, "Insufficient permission")
        if user.role == Role.OPERATOR and DEVELOPMENT_BUILDING_ID not in user.building_ids:
            raise HTTPException(403, "Building access denied")
        return user
    return dependency


def require_any_permission(*permissions: Permission):
    def dependency(user: User = Depends(get_current_user)):
        if not any(has_permission(user.role, permission) for permission in permissions):
            raise HTTPException(403, "Insufficient permission")
        if user.role == Role.OPERATOR and DEVELOPMENT_BUILDING_ID not in user.building_ids:
            raise HTTPException(403, "Building access denied")
        return user
    return dependency


def require_role(role: Role):
    def dependency(user: User = Depends(get_current_user)):
        if user.role != role: raise HTTPException(403, "Insufficient role")
        return user
    return dependency


def require_building_access(building_id: str, user: User, request: Request):
    access = getattr(request.app.state, "building_access", BuildingAccessRepository())
    if not access.has_access(user, building_id): raise HTTPException(403, "Building access denied")


def zone_building_id(zone_id: str) -> str:
    # Current data has one simulated building; Phase 11 replaces this resolver with repository-backed ownership.
    return DEVELOPMENT_BUILDING_ID


def require_zone_access(request: Request, user: User, zone_id: str):
    require_building_access(zone_building_id(zone_id), user, request)


def require_zone_permission(permission: Permission):
    def dependency(request: Request, zone_id: str, user: User = Depends(require_permission(permission))):
        require_zone_access(request, user, zone_id)
        return user
    return dependency


def require_zone_any_permission(*permissions: Permission):
    def dependency(request: Request, zone_id: str,
                   user: User = Depends(require_any_permission(*permissions))):
        require_zone_access(request, user, zone_id)
        return user
    return dependency
