import pytest
from backend.schemas.zone import Zone
from backend.schemas.events import OccupancyEvent
from datetime import datetime

def test_zone_schema_valid():
    zone = Zone(
        zone_id="test_01",
        name="Test",
        type="test",
        capacity=10,
        area_m2=20.0,
        comfort={"min_temperature": 20.0, "max_temperature": 25.0}
    )
    assert zone.zone_id == "test_01"
    assert zone.comfort.min_temperature == 20.0

def test_occupancy_event_valid():
    event = OccupancyEvent(
        zone_id="test_01",
        people_count=5,
        capacity=10,
        occupancy_percentage=50.0,
        occupancy_state="MEDIUM"
    )
    assert event.people_count == 5
