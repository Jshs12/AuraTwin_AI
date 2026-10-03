"""Read-only commissioning evaluation using the existing telemetry quality policy."""
from backend.services.data_quality import DataQualityGate
from backend.schemas.data_quality import QualityState


FRESHNESS_POLICY_SIGNAL = {"cooling_setpoint": "setpoint", "power": "energy",
    "cost": "energy", "tariff_rate": "tariff"}


def evaluate_commissioning(integration, devices, points, telemetry_service,
                           configuration_repository, quality_gate: DataQualityGate,
                           *, source_capability_verified: bool = False):
    active_devices = [d for d in devices if d.status == "CONFIGURED" and d.archived_at is None]
    active_points = [p for p in points if p.mapping_status == "CONFIRMED" and p.readable
                     and p.zone_id is not None]
    # This is an adapter-registry assertion, never a user-editable config flag.
    physical_capability = bool(source_capability_verified)
    quality_states = []
    for point in active_points:
        scope = configuration_repository.telemetry_scope(str(point.zone_id))
        if scope is None:
            quality_states.append("MISSING")
            continue
        rows = telemetry_service.list_zone(organization_id=scope["organization_id"],
            building_id=scope["building_id"], zone_id=scope["database_zone_id"],
            signal=point.logical_signal, limit=1)
        if not rows:
            quality_states.append("MISSING")
            continue
        observation = rows[0]
        assessment = quality_gate.assess(point.logical_signal, observation.value,
            source=observation.source or "unknown", observation_timestamp=observation.observed_at,
            simulated=bool(observation.simulated), numeric=True,
            freshness_signal=FRESHNESS_POLICY_SIGNAL.get(point.logical_signal, point.logical_signal))
        if observation.quality_state and observation.quality_state != QualityState.VALID.value:
            try:
                upstream_state = QualityState(observation.quality_state)
            except ValueError:
                upstream_state = QualityState.INVALID
            assessment = assessment.model_copy(update={"state": upstream_state,
                "reason_code": f"PERSISTED_{upstream_state.value}"})
        quality_states.append(assessment.state.value)
    if integration.status != "CONFIGURED":
        state = "BLOCKED"
    elif integration.connection_state == "ERROR":
        state = "BLOCKED"
    elif not physical_capability:
        state = "SIMULATED_COMMISSIONING" if active_devices or active_points else "CONFIGURED"
    elif not active_devices:
        state = "DISCOVERY_REVIEW"
    elif not active_points:
        state = "MAPPING_REVIEW"
    elif not quality_states or any(item != "VALID" for item in quality_states):
        state = "BLOCKED"
    elif integration.connection_state != "CONNECTED":
        state = "CONNECTION_TEST_PENDING"
    else:
        state = "READ_ONLY_READY"
    return {"state": state, "read_only_ready": state == "READ_ONLY_READY",
        "physical_source_verified": physical_capability,
        "confirmed_readable_mappings": len(active_points),
        "active_devices": len(active_devices), "observation_quality": quality_states,
        "simulated": not physical_capability,
        "message": ("SIMULATED COMMISSIONING — no physical connection or hardware readiness is claimed."
                    if not physical_capability else "Read-only source capability and current mappings evaluated.")}
