from backend.schemas.optimization import OptimizationRecommendation
from backend.schemas.state import ZoneState
from backend.optimization.engine import OptimizationEngine
from backend.schemas.zone import Zone, ComfortLimits
from backend.schemas.events import OccupancyEvent
from backend.schemas.energy import EnergyReading, Tariff
from backend.schemas.control import BACnetReadResult

def create_mock_state(current_temp: float, occ_state: str) -> ZoneState:
    zone = Zone(
        zone_id="test_zone",
        name="Test",
        type="classroom",
        capacity=40,
        area_m2=65.0,
        comfort=ComfortLimits(min_temperature=22.0, max_temperature=26.0)
    )
    occ = OccupancyEvent(
        zone_id="test_zone", people_count=20, capacity=40,
        occupancy_percentage=50.0, occupancy_state=occ_state
    )
    tariff = Tariff(tariff_id="t1", rate_per_kwh=0.15, currency="USD", is_peak=False)
    energy = EnergyReading(
        zone_id="test_zone", power_kw=5.0, energy_kwh=10.0, cost=1.5, tariff=tariff, is_simulated=True
    )
    hvac = BACnetReadResult(zone_id="test_zone", object_id="AV:1", present_value=24.0)
    
    return ZoneState(
        zone=zone, occupancy=occ, temperature=current_temp,
        energy=energy, tariff=tariff, hvac_status=hvac
    )

def test_current_temperature_below_range():
    state = create_mock_state(20.0, "MEDIUM")
    rec = OptimizationEngine.generate_recommendation(state)
    assert rec.current_temperature_status == "OUT_OF_RANGE"
    assert rec.recommended_setpoint_status == "WITHIN_RANGE" # defaults to 24.0

def test_current_temperature_within_range():
    state = create_mock_state(24.0, "MEDIUM")
    rec = OptimizationEngine.generate_recommendation(state)
    assert rec.current_temperature_status == "WITHIN_RANGE"
    assert rec.recommended_setpoint_status == "WITHIN_RANGE"

def test_current_temperature_above_range():
    state = create_mock_state(28.0, "MEDIUM")
    rec = OptimizationEngine.generate_recommendation(state)
    assert rec.current_temperature_status == "OUT_OF_RANGE"
    assert rec.recommended_setpoint_status == "WITHIN_RANGE"

def test_recommended_setpoint_outside_range_when_empty():
    state = create_mock_state(24.0, "EMPTY")
    rec = OptimizationEngine.generate_recommendation(state)
    assert rec.current_temperature_status == "WITHIN_RANGE"
    assert rec.recommended_setpoint == 28.0 # 26.0 + 2.0
    assert rec.recommended_setpoint_status == "OUT_OF_RANGE"
    
def test_optimizer_metadata_and_cost():
    state = create_mock_state(24.0, "MEDIUM")
    rec = OptimizationEngine.generate_recommendation(state)
    assert rec.source == "deterministic_optimizer"
    assert rec.estimated_hourly_cost == 5.0 * 0.15
