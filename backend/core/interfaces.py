from abc import ABC, abstractmethod
from typing import Optional, Tuple
from backend.schemas.events import OccupancyEvent
from backend.schemas.energy import EnergyReading, Tariff
from backend.schemas.control import HVACCommand, BuildingControlState, ControlResult

class OccupancyProvider(ABC):
    @abstractmethod
    def get_occupancy(self, zone_id: str) -> OccupancyEvent:
        pass

class EnergyProvider(ABC):
    @abstractmethod
    def get_energy(self, zone_id: str) -> EnergyReading:
        pass

class TemperatureProvider(ABC):
    @abstractmethod
    def get_temperature(self, zone_id: str) -> float:
        pass

class TariffProvider(ABC):
    @abstractmethod
    def get_current_tariff(self) -> Tariff:
        pass

class BuildingControlProvider(ABC):
    @property
    def provider_identity(self) -> str:
        """Stable display identity; real integrations should override this."""
        return type(self).__name__

    @property
    def is_simulated(self) -> bool:
        return False

    @property
    def is_ready(self) -> bool:
        return True

    @abstractmethod
    def write_setpoint(self, command: HVACCommand) -> bool:
        pass

    @abstractmethod
    def read_status(self, zone_id: str) -> BuildingControlState:
        pass

    def read_control_state(self, zone_id: str) -> Optional[BuildingControlState]:
        """Optional richer, provider-neutral control/telemetry snapshot."""
        return None

    def write_command(self, command: HVACCommand) -> ControlResult:
        """Adapt legacy boolean providers without breaking their interface."""
        previous = self.read_status(command.zone_id).present_value
        try:
            success = bool(self.write_setpoint(command))
            current = self.read_status(command.zone_id).present_value
        except Exception:
            return ControlResult(
                command_id=command.command_id, zone_id=command.zone_id,
                requested_setpoint=command.setpoint, previous_setpoint=previous,
                success=False, status="FAILED", provider=self.provider_identity,
                simulated=self.is_simulated, error_code="PROVIDER_ERROR",
                error_message="Building control provider failed.",
            )
        return ControlResult(
            command_id=command.command_id, zone_id=command.zone_id,
            requested_setpoint=command.setpoint,
            applied_setpoint=float(current) if success else None,
            previous_setpoint=float(previous) if isinstance(previous, (int, float)) else None,
            success=success, status="SUCCESS" if success else "FAILED",
            provider=self.provider_identity, simulated=self.is_simulated,
            recommendation_reference=command.recommendation_reference,
            error_code=None if success else "WRITE_FAILED",
            error_message=None if success else "Provider did not acknowledge the write.",
        )

class CameraProvider(ABC):
    @abstractmethod
    def get_frame(self, zone_id: str) -> Optional[bytes]:
        """
        Captures and returns the latest frame for the given zone as raw bytes (JPEG).
        Returns None if the camera is unavailable or fails.
        """
        pass
