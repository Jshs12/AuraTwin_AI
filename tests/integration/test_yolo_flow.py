"""
Integration test: End-to-end YOLO occupancy flow for classroom_01.

This test checks whether the entire pipeline works when a real image
is processed through YOLO inference.

IMPORTANT: This test is SKIPPED if ultralytics / YOLO model is not available.
Actual inference is only verified when the model can be loaded.
"""

import os
import pytest
from pathlib import Path


def yolo_available() -> bool:
    """Check if ultralytics is installed and a model can be loaded."""
    try:
        from backend.core.paths import resolve_project_path
        from backend.core.yolo_provider import configure_ultralytics_directory
        configure_ultralytics_directory()
        from ultralytics import YOLO
        model_path = resolve_project_path(os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"))
        # Try to instantiate — will auto-download yolov8n.pt if not present
        model = YOLO(str(model_path))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not yolo_available(), reason="YOLO model not available")
def test_yolo_inference_with_test_image():
    """
    Run actual YOLO inference on a test image and verify:
    1. Inference succeeds
    2. people_count is an integer >= 0
    3. OccupancyEvent is valid
    4. Detection metadata includes processing_time_ms
    5. Annotated image is created
    """
    from backend.core.yolo_provider import YOLOOccupancyProvider
    import cv2
    import numpy as np

    provider = YOLOOccupancyProvider(
        model_path=os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"),
        confidence=float(os.environ.get("YOLO_CONFIDENCE", "0.40")),
        device="cpu",
        output_dir="data/detections/test",
    )
    assert provider.is_ready(), "YOLO model failed to load"

    # Create a synthetic test image (blank 640x480)
    # This will produce 0 person detections but proves the pipeline works
    test_img = np.zeros((480, 640, 3), dtype=np.uint8)
    success, buffer = cv2.imencode(".jpg", test_img)
    assert success, "Failed to encode test image"

    event, metadata = provider.detect_from_image(
        buffer.tobytes(), "classroom_01", 40
    )

    # Validate OccupancyEvent
    assert event.zone_id == "classroom_01"
    assert isinstance(event.people_count, int)
    assert event.people_count >= 0
    assert event.capacity == 40
    assert event.occupancy_percentage >= 0.0

    # Validate metadata
    assert metadata["provider_source"] == "yolo_occupancy_provider"
    assert metadata["processing_time_ms"] > 0
    assert isinstance(metadata["confidences"], list)
    assert metadata["model_name"] is not None

    # A blank image should detect 0 people
    assert event.people_count == 0
    assert event.occupancy_state == "EMPTY"

    print(f"\n[YOLO Integration] people_count={event.people_count}, "
          f"time={metadata['processing_time_ms']:.1f}ms, "
          f"state={event.occupancy_state}")


@pytest.mark.skipif(not yolo_available(), reason="YOLO model not available")
def test_yolo_full_loop_classroom_01():
    """
    End-to-end: image → YOLO → OccupancyEvent → ZoneState → Optimizer → Control

    Uses a blank test image so detection is deterministic (0 people).
    Proves the downstream architecture still works with YOLOOccupancyProvider output.
    """
    import cv2
    import numpy as np
    from backend.core.yolo_provider import YOLOOccupancyProvider
    from backend.core.mock_providers import (
        MockTemperatureProvider, MockTariffProvider,
        MockEnergyProvider, MockBuildingControlProvider,
    )
    from backend.services.zone_state import ZoneStateService
    from backend.intelligence.service import RecommendationWorkflow
    from backend.services.control import ControlService
    from backend.core.events import EventTrace

    # Clear events
    EventTrace.clear()

    # Setup YOLO provider
    yolo_prov = YOLOOccupancyProvider(
        model_path=os.environ.get("YOLO_MODEL_PATH", "yolov8n.pt"),
        confidence=0.40,
        device="cpu",
    )

    # Create a blank test image → 0 people
    test_img = np.zeros((480, 640, 3), dtype=np.uint8)
    success, buffer = cv2.imencode(".jpg", test_img)
    assert success

    event, metadata = yolo_prov.detect_from_image(buffer.tobytes(), "classroom_01", 40)
    assert event.people_count == 0
    assert event.occupancy_state == "EMPTY"

    # Now use mock providers for the rest (temp, energy, control)
    tariff_prov = MockTariffProvider()
    temp_prov = MockTemperatureProvider()
    energy_prov = MockEnergyProvider(tariff_prov)
    control_prov = MockBuildingControlProvider()

    zone_state_svc = ZoneStateService(yolo_prov, temp_prov, energy_prov, control_prov)
    state = zone_state_svc.get_zone_state("classroom_01")
    assert state.occupancy_source == "yolo"

    # The intelligence workflow's mock advisory must pass the safety gate before control.
    decision = RecommendationWorkflow().recommend(state)
    assert decision.recommendation_kind == "intelligence"
    assert decision.intelligence_recommendation.provider == "mock_intelligence_provider"
    assert decision.validation.outcome == "VALIDATED"

    # Control accepts only a successful safety validation.
    control_svc = ControlService(control_prov)
    success = control_svc.apply_validated_recommendation(decision.validation, state)
    assert success is True

    # Verify event trace includes YOLO_DETECTION
    history = EventTrace.get_history("classroom_01")
    event_types = [e.event_type for e in history]
    assert "YOLO_DETECTION" in event_types
    assert "OCCUPANCY_DETECTED" in event_types
    assert "STATE_EVALUATED" in event_types
    assert "RECOMMENDATION_VALIDATED" in event_types
    assert "CONTROL_COMMAND" in event_types
    assert "HVAC_RESPONSE" in event_types
