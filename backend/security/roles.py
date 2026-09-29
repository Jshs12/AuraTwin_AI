from enum import StrEnum


class Role(StrEnum):
    ADMIN = "ADMIN"
    OPERATOR = "OPERATOR"


class Permission(StrEnum):
    BUILDING_READ = "building:read"
    BUILDING_CONFIGURE = "building:configure"
    ZONES_READ = "zones:read"
    ZONES_CREATE = "zones:create"
    ZONES_UPDATE = "zones:update"
    ZONES_DELETE = "zones:delete"
    TELEMETRY_READ = "telemetry:read"
    ENERGY_READ = "energy:read"
    EVENTS_READ = "events:read"
    SYSTEM_READ = "system:read"
    AUDIT_READ = "audit:read"
    ACCESS_READ = "access:read"
    ACCESS_MANAGE = "access:manage"
    INTEGRATIONS_CONFIGURE = "integrations:configure"
    MONITORING_MANAGE = "monitoring:manage"
    RECOMMENDATIONS_READ = "recommendations:read"
    CONTROL_EXECUTE = "control:execute"


ADMIN_PERMISSIONS = frozenset({
    Permission.BUILDING_READ, Permission.ZONES_READ, Permission.TELEMETRY_READ,
    Permission.ENERGY_READ, Permission.EVENTS_READ, Permission.SYSTEM_READ,
    Permission.AUDIT_READ, Permission.ACCESS_READ,
})
OPERATOR_PERMISSIONS = frozenset({
    Permission.BUILDING_READ, Permission.BUILDING_CONFIGURE, Permission.ZONES_READ,
    Permission.ZONES_CREATE, Permission.ZONES_UPDATE, Permission.ZONES_DELETE,
    Permission.INTEGRATIONS_CONFIGURE, Permission.MONITORING_MANAGE,
    Permission.RECOMMENDATIONS_READ, Permission.CONTROL_EXECUTE, Permission.ACCESS_MANAGE,
})
ROLE_PERMISSIONS = {Role.ADMIN: ADMIN_PERMISSIONS, Role.OPERATOR: OPERATOR_PERMISSIONS}


def has_permission(role: Role, permission: Permission | str) -> bool:
    try:
        permission = Permission(permission)
    except ValueError:
        return False
    return permission in ROLE_PERMISSIONS[role]
