// === Core schemas — match backend Pydantic models exactly ===

export interface ComfortLimits {
  min_temperature: number;
  max_temperature: number;
}

export interface Zone {
  zone_id: string;
  name: string;
  type: string;
  capacity: number;
  area_m2: number;
  comfort: ComfortLimits;
  current_occupancy: number;
  current_temperature: number | null;
  current_setpoint: number | null;
}

export interface OccupancyEvent {
  zone_id: string;
  people_count: number;
  capacity: number;
  occupancy_percentage: number;
  occupancy_state: "EMPTY" | "LOW" | "MEDIUM" | "HIGH" | "UNKNOWN";
  timestamp: string;
}

export interface Tariff {
  tariff_id: string;
  rate_per_kwh: number;
  currency: string;
  is_peak: boolean;
}

export interface EnergyReading {
  zone_id: string;
  power_kw: number;
  energy_kwh: number;
  cost: number;
  tariff: Tariff;
  baseline_power_kw: number | null;
  peak_demand: number | null;
  timestamp: string;
  is_simulated: boolean;
}

export interface BuildingControlState {
  zone_id: string;
  object_id: string;
  present_value: number;
  timestamp: string;
  provider: string;
  control_state: "READY" | "APPLIED" | "REJECTED" | "FAILED";
  requested_setpoint: number | null;
  previous_setpoint: number | null;
  current_temperature: number | null;
  hvac_mode: "COOLING" | "HEATING" | "IDLE" | "OFFLINE" | null;
  fan_status: boolean | null;
  power_kw: number | null;
  energy_kwh: number | null;
  last_command_timestamp: string | null;
}
// Kept as a type alias for consumers of the earlier API schema name.
export type BACnetReadResult = BuildingControlState;

export interface ControlResult {
  command_id: string;
  zone_id: string;
  requested_setpoint: number | null;
  applied_setpoint: number | null;
  previous_setpoint: number | null;
  success: boolean;
  status: "SUCCESS" | "REJECTED" | "FAILED";
  provider: string;
  simulated: boolean;
  timestamp: string;
  command: string;
  recommendation_reference: string | null;
  hvac_mode: string | null;
  current_temperature: number | null;
  power_kw: number | null;
  energy_kwh: number | null;
  error_code: string | null;
  error_message: string | null;
}

export interface ZoneState {
  zone: Zone;
  occupancy: OccupancyEvent;
  temperature: number;
  energy: EnergyReading;
  tariff: Tariff;
  hvac_status: BuildingControlState;
  occupancy_source?: string;
}

export interface OptimizationRecommendation {
  zone_id: string;
  current_setpoint: number;
  recommended_setpoint: number;
  expected_power_kw: number;
  estimated_hourly_cost: number;
  current_temperature_status: "WITHIN_RANGE" | "OUT_OF_RANGE";
  recommended_setpoint_status: "WITHIN_RANGE" | "OUT_OF_RANGE";
  reason: string;
  source: string;
  timestamp: string;
}

export interface IntelligenceRecommendation {
  zone_id: string;
  recommended_setpoint: number;
  rationale: string;
  confidence: number;
  provider: string;
  model_source: string;
  timestamp: string;
  context_reference: Record<string, unknown>;
  action_type: string;
}

export interface SafetyValidationResult {
  outcome: "VALIDATED" | "REJECTED" | "FALLBACK";
  original_recommendation: Record<string, unknown> | null;
  validated_setpoint: number | null;
  rejection_reason: string | null;
  fallback_reason: string | null;
  validation_timestamp: string;
  source: string;
}

export interface RecommendationDecision {
  zone_id: string;
  intelligence_recommendation: IntelligenceRecommendation | null;
  deterministic_recommendation: OptimizationRecommendation | null;
  validation: SafetyValidationResult;
  recommendation_kind: "intelligence" | "deterministic_fallback" | "rejected";
}

export interface SystemEvent {
  event_id: string;
  event_type: string;
  zone_id: string;
  timestamp: string;
  source: string;
  payload: Record<string, unknown>;
  status: string;
}

export interface DetectionMetadata {
  people_count: number;
  confidences: number[];
  processing_time_ms: number;
  annotated_image_path: string | null;
  model_name: string;
  provider_source: string;
}

export interface OccupancyDetectionResponse {
  occupancy: OccupancyEvent;
  detection: DetectionMetadata;
}

export interface OccupancyStatus {
  provider: "mock" | "yolo";
  ready: boolean;
  model?: string;
  confidence_threshold?: number;
}

export interface MonitoringZoneStatus {
  zone_id: string;
  status: string;
  last_snapshot: string | null;
  last_inference: string | null;
  cooldown_remaining_seconds: number;
  last_people_count: number;
  occupancy_source?: string;
  phase?: string | null;
}

export interface MonitoringStatus {
  running: boolean;
  zones_enabled: number;
  zones_total: number;
  camera_provider: string;
  occupancy_provider?: "mock" | "yolo" | "unknown";
  occupancy_provider_ready?: boolean;
  snapshot_interval_seconds: number;
  zones: MonitoringZoneStatus[];
  demo_simulation?: boolean;
  demo_phase?: string | null;
}

export interface DemoStatus {
  scenario_id: string | null;
  scenario_name: string;
  status: "IDLE" | "RUNNING" | "PAUSED" | "COMPLETED" | "STOPPED";
  current_phase: string;
  phase: string;
  phase_number: number;
  total_phases: number;
  elapsed_seconds: number;
  phase_elapsed_seconds: number;
  phase_duration_seconds: number;
  speed_multiplier: number;
  started_at: string | null;
  simulation: true;
}

export interface DemoBuildingSummary {
  simulation: true;
  scenario_id: string | null;
  active_zones: number;
  occupied_zones: number;
  total_occupants: number;
  simulated_power_kw: number;
  simulated_energy_kwh: number;
  simulated_cost: number;
  currency: string;
  zones_under_active_control: number;
  zones_requiring_attention: number;
  average_zone_temperature: number | null;
  average_setpoint: number | null;
  provider: string;
  energy_history: Array<{ timestamp: string; power_kw: number; energy_kwh: number; occupancy: number; hvac_power_kw: number; occupancy_power_kw: number; base_load_kw: number }>;
  energy_model: string;
  energy_metrics: { current_power_kw: number; average_power_kw: number; peak_power_kw: number };
}
