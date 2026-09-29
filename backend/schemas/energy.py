from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional
from backend.core.time import utc_now

class Tariff(BaseModel):
    tariff_id: str = Field(..., description="Identifier for the tariff rate")
    rate_per_kwh: float = Field(..., description="Cost per kWh")
    currency: str = Field(default="USD", description="Currency of the rate")
    is_peak: bool = Field(default=False, description="Whether this is a peak period")
    observed_at: Optional[datetime] = None
    source: str = "unknown"
    simulated: bool = False

class EnergyReading(BaseModel):
    zone_id: str = Field(..., description="Zone identifier")
    power_kw: float = Field(..., description="Current power consumption in kW")
    energy_kwh: float = Field(..., description="Accumulated energy consumption in kWh")
    cost: float = Field(..., description="Estimated cost based on current tariff")
    tariff: Tariff = Field(..., description="Tariff applied to this reading")
    baseline_power_kw: Optional[float] = Field(None, description="Expected baseline power")
    peak_demand: Optional[float] = Field(None, description="Peak demand recorded")
    timestamp: datetime = Field(default_factory=utc_now)
    is_simulated: bool = Field(default=True, description="Flag indicating if the data is simulated")
    observed_at: Optional[datetime] = None
    source: str = "unknown"
