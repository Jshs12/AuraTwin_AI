import random
import asyncio
from typing import Dict, List
from datetime import datetime
from backend.core.time import utc_now
from backend.core.interfaces import OccupancyProvider, EnergyProvider, TemperatureProvider, TariffProvider
from backend.schemas.events import OccupancyEvent, TemperatureReading
from backend.schemas.energy import EnergyReading, Tariff
from backend.integrations.bacnet.simulated import SimulatedBACnetBuildingControlProvider

class MockOccupancyProvider(OccupancyProvider):
    def get_occupancy(self, zone_id: str) -> OccupancyEvent:
        observed = utc_now()
        return OccupancyEvent(
            zone_id=zone_id,
            people_count=18,
            capacity=40,
            occupancy_percentage=45.0,
            occupancy_state="MEDIUM",
            timestamp=observed, observed_at=observed, source="mock_occupancy_provider", simulated=True,
        )
        
    def detect_from_image(self, image_bytes: bytes, zone_id: str, zone_capacity: int):
        import time
        from backend.core.events import EventTrace
        event = self.get_occupancy(zone_id)
        
        # Fluctuate people count slightly for testing
        event.people_count = random.randint(15, 20)
        event.occupancy_percentage = (event.people_count / max(zone_capacity, 1)) * 100.0
        
        metadata = {
            "people_count": event.people_count,
            "confidences": [0.99] * event.people_count,
            "processing_time_ms": 42.0,
            "annotated_image_path": None,
            "model_name": "mock",
            "provider_source": "mock"
        }
        EventTrace.log_event("YOLO_DETECTION", zone_id, "mock_occupancy_provider", metadata)
        return event, metadata

class MockTemperatureProvider(TemperatureProvider):
    def get_temperature(self, zone_id: str) -> float:
        return 27.1

    def get_temperature_reading(self, zone_id: str) -> TemperatureReading:
        observed = utc_now()
        return TemperatureReading(zone_id=zone_id, temperature=27.1, timestamp=observed,
                                 observed_at=observed, source="mock_temperature_provider", simulated=True)

class MockTariffProvider(TariffProvider):
    def get_current_tariff(self) -> Tariff:
        return Tariff(
            tariff_id="default_tariff",
            rate_per_kwh=0.15,
            currency="USD",
            is_peak=False,
            observed_at=utc_now(), source="mock_tariff_provider", simulated=True,
        )

class MockEnergyProvider(EnergyProvider):
    def __init__(self, tariff_provider: TariffProvider):
        self.tariff_provider = tariff_provider

    def get_energy(self, zone_id: str) -> EnergyReading:
        return EnergyReading(
            zone_id=zone_id,
            power_kw=4.8,
            energy_kwh=100.5,
            cost=15.07,
            tariff=self.tariff_provider.get_current_tariff(),
            is_simulated=True,
            observed_at=utc_now(), source="mock_energy_provider",
        )

class MockBuildingControlProvider(SimulatedBACnetBuildingControlProvider):
    """Backward-compatible name for the local deterministic control simulator."""

    def __init__(self):
        # Dynamic zones preserve the legacy test/provider behavior. The app uses
        # the strict, configured SimulatedBACnetBuildingControlProvider directly.
        super().__init__()

class MockEnergyStreamProvider:
    """Emit a deterministic mock stream; this is not meter data."""
    def __init__(self, tariff_provider: TariffProvider, event_broadcaster):
        self.tariff_provider = tariff_provider
        self.event_broadcaster = event_broadcaster
        self.running = False
        self.task = None
        self._tick = 0
        self._energy_kwh: dict[str, float] = {}

    def start(self):
        if not self.running:
            self.running = True
            self._tick = 0
            self._energy_kwh.clear()
            self.task = asyncio.create_task(self._stream_loop())

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()
            self.task = None

    async def stop_and_wait(self):
        task = self.task
        self.stop()
        if task is not None and task is not asyncio.current_task():
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _stream_loop(self):
        interval_seconds = 5.0
        zone_ids = ["classroom_01", "classroom_02", "lab_01", "lab_02"]
        profile = (0.0, 0.25, 0.5, 0.25)
        while self.running:
            for index, zone_id in enumerate(zone_ids):
                # Deterministic illustrative profile for the non-Demo mock
                # stream. Demo Mode uses BuildingEnergyTelemetry instead.
                power = round(2.5 + index * 0.35 + profile[self._tick % len(profile)], 3)
                reading = self.sample(zone_id, power, interval_seconds)
                # We need to wrap it in a ControlEvent or define a new event type
                from backend.core.events import EventTrace
                EventTrace.log_event("ENERGY_UPDATE", zone_id, "mock_energy_stream", reading.model_dump())
            self._tick += 1
            await asyncio.sleep(interval_seconds)

    def sample(self, zone_id: str, power_kw: float, elapsed_seconds: float) -> EnergyReading:
        """Create one mock reading with dimensionally correct cumulative kWh."""
        import math

        if (not math.isfinite(float(power_kw)) or power_kw < 0
                or not math.isfinite(float(elapsed_seconds)) or elapsed_seconds < 0):
            raise ValueError("Mock energy inputs must be finite and non-negative.")
        energy = self._energy_kwh.get(zone_id, 0.0) + float(power_kw) * float(elapsed_seconds) / 3600.0
        self._energy_kwh[zone_id] = energy
        tariff = self.tariff_provider.get_current_tariff()
        return EnergyReading(
            zone_id=zone_id,
            power_kw=float(power_kw),
            energy_kwh=energy,
            cost=energy * tariff.rate_per_kwh,
            tariff=tariff,
            is_simulated=True,
        )

class EnergyAggregator:
    @staticmethod
    def aggregate(readings: List[EnergyReading]) -> dict:
        total_power = sum(r.power_kw for r in readings)
        total_energy = sum(r.energy_kwh for r in readings)
        total_cost = sum(r.cost for r in readings if r.cost)
        peak_power = max((r.power_kw for r in readings), default=0.0)
        
        return {
            "total_power_kw": round(total_power, 2),
            "total_energy_kwh": round(total_energy, 2),
            "total_cost": round(total_cost, 2),
            "peak_power_kw": round(peak_power, 2)
        }
