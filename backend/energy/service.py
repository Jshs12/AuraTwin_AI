from backend.schemas.energy import EnergyReading, Tariff
from backend.schemas.zone import Zone
from backend.core.interfaces import TariffProvider
from datetime import datetime
from backend.core.time import utc_now

class EnergyService:
    def __init__(self, tariff_provider: TariffProvider):
        self.tariff_provider = tariff_provider
        
    def calculate_energy(self, zone: Zone, power_kw: float, elapsed_hours: float, current_accumulated_kwh: float = 0.0) -> EnergyReading:
        tariff = self.tariff_provider.get_current_tariff()
        added_energy = power_kw * elapsed_hours
        new_total_energy = current_accumulated_kwh + added_energy
        cost = new_total_energy * tariff.rate_per_kwh
        
        return EnergyReading(
            zone_id=zone.zone_id,
            power_kw=power_kw,
            energy_kwh=new_total_energy,
            cost=cost,
            tariff=tariff,
            is_simulated=True,
            timestamp=utc_now()
        )
