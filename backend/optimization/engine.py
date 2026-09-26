from backend.schemas.state import ZoneState
from backend.schemas.optimization import OptimizationRecommendation
from datetime import datetime
from backend.core.time import utc_now

class OptimizationEngine:
    @staticmethod
    def generate_recommendation(state: ZoneState) -> OptimizationRecommendation:
        current_temp = state.temperature
        comfort = state.zone.comfort
        occupancy_state = state.occupancy.occupancy_state
        
        # Base logic: If occupied, keep within comfort limits.
        # If EMPTY, we can relax the setpoint to save energy.
        
        # Default behavior: try to stay in middle of comfort band if occupied
        target_setpoint = (comfort.min_temperature + comfort.max_temperature) / 2.0
        reason = "Maintaining optimal comfort setpoint for occupied zone."
        
        if occupancy_state == "EMPTY":
            # Relax cooling/heating by expanding the limits implicitly (e.g. set to max_temp + 2 if cooling)
            # Assuming a generic cooling scenario for now:
            target_setpoint = comfort.max_temperature + 2.0
            reason = "Zone is EMPTY. Relaxing setpoint to conserve energy."
        elif occupancy_state == "HIGH":
            # Pre-cool or aggressive cool
            target_setpoint = comfort.min_temperature + 1.0
            reason = "High occupancy detected. Lowering setpoint to maintain comfort."

        # Estimate power - simple heuristic for now
        expected_power = 2.0 if occupancy_state == "EMPTY" else 5.0
        estimated_hourly_cost = expected_power * state.tariff.rate_per_kwh
        
        current_temperature_status = "WITHIN_RANGE"
        if current_temp < comfort.min_temperature or current_temp > comfort.max_temperature:
            current_temperature_status = "OUT_OF_RANGE"
            
        recommended_setpoint_status = "WITHIN_RANGE"
        if target_setpoint < comfort.min_temperature or target_setpoint > comfort.max_temperature:
            recommended_setpoint_status = "OUT_OF_RANGE"

        return OptimizationRecommendation(
            zone_id=state.zone.zone_id,
            current_setpoint=state.hvac_status.present_value,
            recommended_setpoint=target_setpoint,
            expected_power_kw=expected_power,
            estimated_hourly_cost=estimated_hourly_cost,
            current_temperature_status=current_temperature_status,
            recommended_setpoint_status=recommended_setpoint_status,
            reason=reason,
            source="deterministic_optimizer",
            timestamp=utc_now()
        )
