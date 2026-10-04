"""Explicit-value simulated observation adapter for tests/demos; performs no device reads."""
from datetime import datetime
from typing import Iterable

from backend.database.models import PointMappingRecord
from backend.schemas.provider_observation import ProviderObservation


class ExplicitValueSimulatedProvider:
    """Wrap caller-supplied test values as simulated observations for confirmed readable points."""
    provider_name = "explicit_value_simulated_provider"
    simulated = True

    def observations(self, *, integration_id: str, device_id: str,
                     points: Iterable[PointMappingRecord], values: dict[str, float],
                     observed_at: datetime, runtime_input: bool = False) -> list[ProviderObservation]:
        results = []
        for point in points:
            point_id = str(point.point_mapping_id)
            if point.mapping_status != "CONFIRMED" or not point.readable or point_id not in values:
                continue
            results.append(ProviderObservation(integration_id=integration_id, device_id=device_id,
                point_mapping_id=point_id, observed_at=observed_at, value=values[point_id],
                source=self.provider_name, simulated=True,
                signal=point.logical_signal, unit=point.unit,
                runtime_input=bool(runtime_input and point.logical_signal in {
                    "occupancy", "temperature", "cooling_setpoint"})))
        return results
