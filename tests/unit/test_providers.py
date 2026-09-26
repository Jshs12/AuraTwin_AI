from backend.core.mock_providers import (
    MockOccupancyProvider, MockTemperatureProvider, 
    MockTariffProvider, MockEnergyProvider, MockBuildingControlProvider,
    MockEnergyStreamProvider,
)
from backend.schemas.control import HVACCommand

def test_mock_occupancy():
    provider = MockOccupancyProvider()
    event = provider.get_occupancy("classroom_01")
    assert event.people_count == 18
    assert event.occupancy_state == "MEDIUM"

def test_mock_temperature():
    provider = MockTemperatureProvider()
    temp = provider.get_temperature("classroom_01")
    assert temp == 27.1

def test_mock_energy():
    tariff_prov = MockTariffProvider()
    provider = MockEnergyProvider(tariff_prov)
    energy = provider.get_energy("classroom_01")
    assert energy.power_kw == 4.8
    assert energy.tariff.rate_per_kwh == 0.15

def test_mock_control():
    provider = MockBuildingControlProvider()
    
    # Read initial status
    res1 = provider.read_status("classroom_01")
    assert res1.present_value == 24.0
    
    # Write new setpoint
    cmd = HVACCommand(zone_id="classroom_01", setpoint=22.5, source="test")
    assert provider.write_setpoint(cmd) == True
    
    # Read back status
    res2 = provider.read_status("classroom_01")
    assert res2.present_value == 22.5


def test_mock_energy_stream_integrates_power_and_cost_deterministically():
    tariff = MockTariffProvider()
    stream = MockEnergyStreamProvider(tariff, None)
    half_hour = stream.sample("classroom_01", power_kw=10.0, elapsed_seconds=1800)
    next_half_hour = stream.sample("classroom_01", power_kw=10.0, elapsed_seconds=1800)
    assert half_hour.energy_kwh == 5.0
    assert next_half_hour.energy_kwh == 10.0
    assert next_half_hour.cost == 1.5
