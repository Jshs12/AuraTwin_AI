import asyncio
import json
import logging

import httpx
import pytest

from backend.core.events import EventTrace
from backend.core.mock_providers import (
    MockBuildingControlProvider, MockEnergyProvider, MockOccupancyProvider,
    MockTariffProvider, MockTemperatureProvider,
)
from backend.core.monitoring import ZoneMonitoringScheduler
from backend.intelligence.factory import intelligence_provider_from_environment
from backend.intelligence.providers import MockIntelligenceProvider
from backend.intelligence.schemas import IntelligenceContext
from backend.intelligence.service import RecommendationWorkflow
from backend.integrations.lyzr.provider import LyzrIntelligenceProvider, LyzrProviderError
from backend.schemas.control import BACnetReadResult
from backend.schemas.events import OccupancyEvent
from backend.schemas.state import ZoneState
from backend.schemas.zone import ComfortLimits, Zone
from backend.services.control import ControlService
from backend.services.zone_state import ZoneStateService


def make_context(zone_id="classroom_01"):
    return IntelligenceContext(
        zone_id=zone_id, zone_type="classroom", occupancy_count=12,
        occupancy_percentage=30, occupancy_level="MEDIUM", current_temperature=25,
        comfort_min_temperature=22, comfort_max_temperature=26,
        current_hvac_setpoint=24, current_power_kw=4.8, energy_kwh=10,
        energy_cost=1.5, tariff_rate_per_kwh=0.15, tariff_currency="USD",
        provenance={"energy": "simulated", "occupancy_percentage": "derived"},
        historical_context=None, previous_recommendation=None,
    )


def make_state(zone_id="classroom_01", setpoint=24.0):
    zone = Zone(zone_id=zone_id, name="Test Classroom", type="classroom", capacity=40,
                area_m2=60, comfort=ComfortLimits(min_temperature=22, max_temperature=26))
    occupancy = OccupancyEvent(zone_id=zone_id, people_count=12, capacity=40,
                               occupancy_percentage=30, occupancy_state="MEDIUM")
    tariff_provider = MockTariffProvider()
    energy = MockEnergyProvider(tariff_provider).get_energy(zone_id)
    return ZoneState(zone=zone, occupancy=occupancy, temperature=25.0, energy=energy,
                     tariff=energy.tariff,
                     hvac_status=BACnetReadResult(zone_id=zone_id, object_id="AV:1", present_value=setpoint))


def model_payload(**overrides):
    payload = {
        "zone_id": "classroom_01",
        "recommended_setpoint": 25.0,
        "rationale": "A modest setpoint adjustment based on supplied context.",
        "confidence": 0.9,
        "action_type": "setpoint_adjustment",
    }
    payload.update(overrides)
    return json.dumps(payload)


class FakeResponse:
    def __init__(self, response_text=None, status_code=200, body=None):
        self.status_code = status_code
        self._body = body if body is not None else {"response": response_text}

    def json(self):
        return self._body


def configured_provider(response_text=None, *, status_code=200, body=None, http_post=None, **kwargs):
    if http_post is None:
        response = FakeResponse(response_text, status_code=status_code, body=body)
        http_post = lambda *args, **kw: response
    return LyzrIntelligenceProvider("test-key", "agent-123", http_post=http_post, **kwargs)


def test_mock_provider_still_works_and_is_default():
    assert isinstance(intelligence_provider_from_environment({}), MockIntelligenceProvider)
    recommendation = MockIntelligenceProvider().generate_recommendation(make_context())
    assert recommendation.provider == "mock_intelligence_provider"


def test_lyzr_provider_configuration_from_environment():
    provider = LyzrIntelligenceProvider.from_environment({
        "LYZR_API_KEY": "key", "LYZR_AGENT_ID": "agent", "LYZR_TIMEOUT_SECONDS": "3.5",
    })
    assert provider.agent_id == "agent"
    assert provider.timeout_seconds == 3.5
    assert provider.api_url == LyzrIntelligenceProvider.DEFAULT_API_URL
    assert isinstance(intelligence_provider_from_environment({"INTELLIGENCE_PROVIDER": "lyzr"}), LyzrIntelligenceProvider)


def test_missing_api_key_raises_sanitized_error_and_workflow_falls_back():
    provider = LyzrIntelligenceProvider(None, "agent-123")
    with pytest.raises(LyzrProviderError, match="API key is not configured"):
        provider.generate_recommendation(make_context())
    decision = RecommendationWorkflow(provider=provider).recommend(make_state())
    assert decision.recommendation_kind == "deterministic_fallback"
    assert decision.validation.outcome == "FALLBACK"


def test_invalid_timeout_configuration_falls_back_without_startup_error():
    provider = LyzrIntelligenceProvider.from_environment({
        "LYZR_API_KEY": "key", "LYZR_AGENT_ID": "agent", "LYZR_TIMEOUT_SECONDS": "not-a-number",
    })
    assert provider.timeout_seconds == LyzrIntelligenceProvider.DEFAULT_TIMEOUT_SECONDS
    decision = RecommendationWorkflow(provider=provider).recommend(make_state())
    assert decision.validation.outcome == "FALLBACK"


def test_successful_structured_lyzr_response_is_parsed():
    seen = {}
    def post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return FakeResponse(model_payload())
    recommendation = configured_provider(http_post=post, timeout_seconds=4).generate_recommendation(make_context())
    assert recommendation.zone_id == "classroom_01"
    assert recommendation.recommended_setpoint == 25.0
    assert recommendation.provider == "lyzr"
    assert recommendation.model_source == "lyzr_agent_api_v3"
    assert seen["url"] == LyzrIntelligenceProvider.DEFAULT_API_URL
    assert seen["timeout"] == 4
    assert seen["headers"]["x-api-key"] == "test-key"
    assert seen["json"]["user_id"] == "auratwin-service"
    message = json.loads(seen["json"]["message"])
    assert message["context"]["schema_version"] == "1.0"
    assert message["context"]["historical_context"] is None
    assert "comfort_min_temperature" in message["context"]


@pytest.mark.parametrize("response_text", ["not json", "```json\\n{}\\n```", json.dumps({"zone_id": "classroom_01"})])
def test_malformed_or_incomplete_lyzr_response_is_rejected(response_text):
    with pytest.raises(LyzrProviderError, match="malformed advisory JSON"):
        configured_provider(response_text).generate_recommendation(make_context())


@pytest.mark.parametrize("value", ["cold", None, float("nan"), float("inf")])
def test_invalid_setpoint_is_rejected(value):
    with pytest.raises(LyzrProviderError):
        configured_provider(model_payload(recommended_setpoint=value)).generate_recommendation(make_context())


@pytest.mark.parametrize("confidence", ["high", -0.1, 1.1, float("nan")])
def test_invalid_confidence_is_rejected(confidence):
    with pytest.raises(LyzrProviderError):
        configured_provider(model_payload(confidence=confidence)).generate_recommendation(make_context())


def test_wrong_zone_and_invalid_action_are_rejected():
    with pytest.raises(LyzrProviderError, match="different zone"):
        configured_provider(model_payload(zone_id="office_01")).generate_recommendation(make_context())
    with pytest.raises(LyzrProviderError, match="unsupported action"):
        configured_provider(model_payload(action_type="hvac_command")).generate_recommendation(make_context())


def test_missing_rationale_is_rejected():
    with pytest.raises(LyzrProviderError, match="malformed advisory JSON"):
        configured_provider(json.dumps({
            "zone_id": "classroom_01", "recommended_setpoint": 25,
            "confidence": 0.9, "action_type": "setpoint_adjustment",
        })).generate_recommendation(make_context())


def test_timeout_becomes_sanitized_provider_failure_and_fallback():
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timeout with credential=test-key")
    provider = configured_provider(http_post=timeout)
    with pytest.raises(LyzrProviderError, match="timed out") as error:
        provider.generate_recommendation(make_context())
    assert "test-key" not in str(error.value)
    assert RecommendationWorkflow(provider=provider).recommend(make_state()).validation.outcome == "FALLBACK"


def test_network_exception_is_sanitized():
    def fail(*args, **kwargs):
        raise RuntimeError("request failed with key=test-key")
    with pytest.raises(LyzrProviderError, match="request failed") as error:
        configured_provider(http_post=fail).generate_recommendation(make_context())
    assert "test-key" not in str(error.value)


def test_safety_rejection_uses_fallback_and_lyzr_success_reaches_validator():
    EventTrace.clear()
    unsafe = configured_provider(model_payload(recommended_setpoint=27))
    decision = RecommendationWorkflow(provider=unsafe).recommend(make_state())
    assert decision.recommendation_kind == "deterministic_fallback"
    assert decision.validation.outcome == "FALLBACK"
    event_types = [event.event_type for event in EventTrace.get_history("classroom_01")]
    assert "RECOMMENDATION_REJECTED" in event_types
    assert "FALLBACK_ACTIVATED" in event_types

    EventTrace.clear()
    valid = configured_provider(model_payload(recommended_setpoint=25))
    decision = RecommendationWorkflow(provider=valid).recommend(make_state())
    assert decision.validation.outcome == "VALIDATED"
    assert decision.intelligence_recommendation.provider == "lyzr"
    assert "RECOMMENDATION_VALIDATED" in [e.event_type for e in EventTrace.get_history("classroom_01")]


def test_api_key_never_enters_events_or_logs(caplog):
    secret = "sensitive-test-token-987"
    def fail(*args, **kwargs):
        raise RuntimeError(f"transport echoed {secret}")
    provider = LyzrIntelligenceProvider(secret, "agent-123", http_post=fail)
    EventTrace.clear()
    with caplog.at_level(logging.DEBUG):
        RecommendationWorkflow(provider=provider).recommend(make_state())
    event_text = json.dumps([event.model_dump(mode="json") for event in EventTrace.get_history("classroom_01")])
    assert secret not in event_text
    assert secret not in caplog.text


def test_monitoring_continues_after_lyzr_failure():
    state = make_state()
    zone_state_service = ZoneStateService(MockOccupancyProvider(), MockTemperatureProvider(),
                                         MockEnergyProvider(MockTariffProvider()), MockBuildingControlProvider())
    zone_state_service._zones[state.zone.zone_id] = state.zone
    zone_state_service.get_zone_state = lambda zone_id: state

    class Detector:
        def detect_from_image(self, frame, zone_id, capacity):
            return state.occupancy, {}

    control = ControlService(MockBuildingControlProvider())
    scheduler = ZoneMonitoringScheduler(zone_state_service,
        RecommendationWorkflow(LyzrIntelligenceProvider(None, "agent-id")), control, Detector())
    scheduler.monitored_zones = [state.zone.zone_id]
    scheduler.zone_states = {state.zone.zone_id: scheduler.zone_states["classroom_01"]}
    scheduler.camera.get_frame = lambda zone_id: b"frame"
    EventTrace.clear()
    asyncio.run(scheduler._process_zone(state.zone.zone_id))
    assert scheduler.zone_states[state.zone.zone_id]["status"] == "MONITORING"
    assert "FALLBACK_ACTIVATED" in [e.event_type for e in EventTrace.get_history(state.zone.zone_id)]
