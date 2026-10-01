from tests.security_test_utils import bare_client, make_client
from backend.security.roles import Role


def test_control_policy_readiness_is_operator_only_and_contains_no_policy_values():
    operator = make_client(Role.OPERATOR)
    response = operator.get("/api/control/policy-status")
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is True
    assert body["missing_configuration"] == []
    assert body["invalid_configuration"] == []
    assert "COMMAND_LIMIT_MIN_SETPOINT" not in str(body.get("values", {}))

    assert make_client(Role.ADMIN).get("/api/control/policy-status").status_code == 403
    assert bare_client().get("/api/control/policy-status").status_code == 401
