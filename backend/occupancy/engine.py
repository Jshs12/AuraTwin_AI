from backend.schemas.events import OccupancyEvent
from backend.schemas.zone import Zone
from datetime import datetime
from backend.core.time import utc_now

class OccupancyEngine:
    @staticmethod
    def calculate_occupancy(zone: Zone, people_count: int) -> OccupancyEvent:
        capacity = max(zone.capacity, 1) # Prevent division by zero
        percentage = (people_count / capacity) * 100.0
        
        state = "EMPTY"
        if people_count > 0:
            if percentage < 30.0:
                state = "LOW"
            elif percentage < 70.0:
                state = "MEDIUM"
            else:
                state = "HIGH"
                
        return OccupancyEvent(
            zone_id=zone.zone_id,
            people_count=people_count,
            capacity=capacity,
            occupancy_percentage=percentage,
            occupancy_state=state,
            timestamp=utc_now()
        )
