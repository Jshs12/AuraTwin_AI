"""Explicit adapter from validated provider observations into the existing ZoneState service."""

from backend.schemas.data_quality import QualityState
from backend.telemetry.ingestion import RuntimeObservationConsumer


class ZoneStateRuntimeConsumer(RuntimeObservationConsumer):
    """Delegates only approved current observations to ZoneStateService."""

    def __init__(self, zone_state_service):
        self.zone_state_service = zone_state_service

    def apply_current_observation(self, *, organization_id: str, building_id: str, floor_id: str,
                                  zone_id: str, zone_key: str, signal: str, value: float,
                                  observed_at, source: str, simulated: bool,
                                  quality_state: QualityState) -> bool:
        return self.zone_state_service.apply_runtime_observation(
            organization_id=organization_id, building_id=building_id, floor_id=floor_id,
            zone_id=zone_id, zone_key=zone_key, signal=signal, value=value,
            observed_at=observed_at, source=source, simulated=simulated,
            quality_state=quality_state)
