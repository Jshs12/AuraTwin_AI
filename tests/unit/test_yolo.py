"""
Unit tests for YOLOOccupancyProvider and the occupancy provider abstraction.

These tests validate:
- Provider interface compatibility
- Mock provider still works after Phase 3 changes
- YOLO provider initialization and readiness checks
- Invalid image handling
- Missing model handling
- OccupancyEvent contract
"""

import pytest
from unittest.mock import patch, MagicMock
from backend.core.interfaces import OccupancyProvider
from backend.core.mock_providers import MockOccupancyProvider
from backend.schemas.events import OccupancyEvent


# -----------------------------------------------------------------------
# 1. Provider interface compatibility
# -----------------------------------------------------------------------
def test_mock_provider_implements_interface():
    """MockOccupancyProvider must satisfy OccupancyProvider ABC."""
    assert issubclass(MockOccupancyProvider, OccupancyProvider)


def test_yolo_provider_implements_interface():
    """YOLOOccupancyProvider must satisfy OccupancyProvider ABC."""
    from backend.core.yolo_provider import YOLOOccupancyProvider
    assert issubclass(YOLOOccupancyProvider, OccupancyProvider)


# -----------------------------------------------------------------------
# 2. Mock provider still works
# -----------------------------------------------------------------------
def test_mock_provider_returns_valid_event():
    provider = MockOccupancyProvider()
    event = provider.get_occupancy("classroom_01")
    assert isinstance(event, OccupancyEvent)
    assert event.zone_id == "classroom_01"
    assert event.people_count == 18


# -----------------------------------------------------------------------
# 3. YOLO provider initialization — missing model gracefully handled
# -----------------------------------------------------------------------
def test_yolo_provider_not_ready_with_bad_model():
    """If model path doesn't exist, provider should load but not be ready."""
    from backend.core.yolo_provider import YOLOOccupancyProvider

    provider = YOLOOccupancyProvider(
        model_path="nonexistent_model.pt",
        confidence=0.4,
        device="cpu",
    )
    assert provider.is_ready() is False
    assert provider.load_error


def test_yolo_provider_get_occupancy_without_image():
    """The interface method should return a default event when called without an image."""
    from backend.core.yolo_provider import YOLOOccupancyProvider

    provider = YOLOOccupancyProvider(
        model_path="nonexistent_model.pt",
        confidence=0.4,
        device="cpu",
    )
    event = provider.get_occupancy("classroom_01")
    assert isinstance(event, OccupancyEvent)
    assert event.zone_id == "classroom_01"
    assert event.people_count == 0
    assert event.occupancy_state == "UNKNOWN"


# -----------------------------------------------------------------------
# 4. YOLO provider — detect_from_image requires ready model
# -----------------------------------------------------------------------
def test_yolo_detect_raises_when_not_ready():
    """detect_from_image should raise RuntimeError if model not loaded."""
    from backend.core.yolo_provider import YOLOOccupancyProvider

    provider = YOLOOccupancyProvider(
        model_path="nonexistent_model.pt",
        confidence=0.4,
        device="cpu",
    )
    with pytest.raises(RuntimeError, match="YOLO model is not loaded"):
        provider.detect_from_image(b"fakebytes", "classroom_01", 40)


# -----------------------------------------------------------------------
# 5. Person filtering — only class 0 counted
# -----------------------------------------------------------------------
def test_person_class_id_is_zero():
    """Verify the PERSON_CLASS_ID constant is COCO class 0."""
    from backend.core.yolo_provider import YOLOOccupancyProvider
    assert YOLOOccupancyProvider.PERSON_CLASS_ID == 0


# -----------------------------------------------------------------------
# 6. OccupancyEvent validation — schema contract
# -----------------------------------------------------------------------
def test_occupancy_event_schema_fields():
    """OccupancyEvent must have all required fields."""
    event = OccupancyEvent(
        zone_id="test",
        people_count=5,
        capacity=40,
        occupancy_percentage=12.5,
        occupancy_state="LOW",
    )
    assert event.zone_id == "test"
    assert event.people_count == 5
    assert event.capacity == 40
    assert event.occupancy_percentage == 12.5
    assert event.occupancy_state == "LOW"


# -----------------------------------------------------------------------
# 7. API-level tests — occupancy provider mode
# -----------------------------------------------------------------------
def test_detect_endpoint_returns_503_when_yolo_not_loaded():
    """The /api/occupancy/detect endpoint returns 503 only when OCCUPANCY_PROVIDER=yolo
    but the model fails to load (e.g. bad path). When YOLO is healthy or mock is active,
    a valid image submission must not return 503.
    This test verifies the 503 guard does not fire for a healthy YOLO provider.
    A bad image (non-decodable bytes) in YOLO mode returns 400, not 503.
    """
    from tests.security_test_utils import make_client
    from backend.api.main import app, OCCUPANCY_PROVIDER_MODE, yolo_provider

    client = make_client()
    import io
    fake_img = io.BytesIO(b"fakeimage_not_real_jpeg")
    response = client.post(
        "/api/occupancy/detect",
        files={"file": ("test.jpg", fake_img, "image/jpeg")},
        data={"zone_id": "classroom_01"},
    )
    # With YOLO active and model loaded: bad bytes → 400 (invalid format), not 503.
    # With mock active: mock provider accepts any bytes and returns a result → 200.
    # Either way the response is NOT 503, which would only fire when YOLO is configured
    # but the model file itself failed to load.
    if OCCUPANCY_PROVIDER_MODE == "yolo" and (yolo_provider is None or not yolo_provider.is_ready()):
        assert response.status_code == 503
    else:
        assert response.status_code in (200, 400)


def test_occupancy_status_mock_mode():
    """The /api/occupancy/status endpoint reports the active provider correctly.
    When OCCUPANCY_PROVIDER=yolo (set via .env), reports 'yolo' + readiness.
    When OCCUPANCY_PROVIDER=mock, reports 'mock'.
    """
    from tests.security_test_utils import make_client
    from backend.api.main import app, OCCUPANCY_PROVIDER_MODE, yolo_provider
    import os

    client = make_client()
    response = client.get("/api/occupancy/status")
    assert response.status_code == 200
    data = response.json()
    # Provider matches the OCCUPANCY_PROVIDER env var (defaulting to 'mock' if not set)
    expected_provider = os.environ.get("OCCUPANCY_PROVIDER", "mock").lower()
    assert data["provider"] == expected_provider
    expected_ready = OCCUPANCY_PROVIDER_MODE != "yolo" or (yolo_provider is not None and yolo_provider.is_ready())
    assert data["ready"] is expected_ready


def test_detect_endpoint_rejects_invalid_extension():
    """The /api/occupancy/detect should reject unsupported file types."""
    from tests.security_test_utils import make_client
    from backend.api.main import app

    client = make_client()
    import io
    fake_file = io.BytesIO(b"notanimage")
    response = client.post(
        "/api/occupancy/detect",
        files={"file": ("test.txt", fake_file, "text/plain")},
        data={"zone_id": "classroom_01"},
    )
    assert response.status_code == 400
    assert "Unsupported file type" in response.json()["detail"]


def test_detect_endpoint_handles_malformed_image_truthfully():
    """Malformed bytes return 400 when YOLO can decode-check them; unavailable YOLO returns 503."""
    from tests.security_test_utils import make_client
    from backend.api.main import app, OCCUPANCY_PROVIDER_MODE, yolo_provider
    import io
    import os

    # Ensure yolo mode is active so it calls the real provider or mock provider decoding
    os.environ["OCCUPANCY_PROVIDER"] = "yolo"

    client = make_client()
    fake_file = io.BytesIO(b"not_a_valid_jpeg_or_png_file")
    response = client.post(
        "/api/occupancy/detect",
        files={"file": ("test.jpg", fake_file, "image/jpeg")},
        data={"zone_id": "classroom_01"},
    )
    
    if OCCUPANCY_PROVIDER_MODE == "yolo" and (yolo_provider is None or not yolo_provider.is_ready()):
        assert response.status_code == 503
    elif OCCUPANCY_PROVIDER_MODE == "yolo":
        assert response.status_code == 400
        assert "Invalid image format" in response.json()["detail"]
    else:
        # The mock provider intentionally does not decode image contents.
        assert response.status_code == 200


def test_inference_endpoint_does_not_echo_internal_exception(monkeypatch):
    from tests.security_test_utils import make_client
    from backend.api import main
    import io

    marker = "private-path-and-token-sentinel"
    def fail(*args, **kwargs):
        raise RuntimeError(marker)

    monkeypatch.setattr(main.occ_prov, "detect_from_image", fail)
    response = make_client().post(
        "/api/occupancy/detect",
        files={"file": ("test.jpg", io.BytesIO(b"some-image-bytes"), "image/jpeg")},
        data={"zone_id": "classroom_01"},
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "Occupancy inference failed. Check server diagnostics."
    assert marker not in response.text


def test_annotated_image_metadata_contains_no_filesystem_path(monkeypatch, tmp_path):
    import cv2
    import numpy as np
    from backend.core.yolo_provider import YOLOOccupancyProvider

    class Result:
        boxes = []
        def plot(self):
            return np.zeros((4, 4, 3), dtype=np.uint8)

    class Model:
        def __call__(self, image, **kwargs):
            return [Result()]

    provider = YOLOOccupancyProvider.__new__(YOLOOccupancyProvider)
    provider.model_path = str(tmp_path / "weights.pt")
    provider.output_dir = tmp_path / "detections"
    provider.output_dir.mkdir()
    provider.model = Model()
    provider.confidence = 0.4
    provider._last_detection = {}
    monkeypatch.setattr(cv2, "imdecode", lambda *args: np.zeros((4, 4, 3), dtype=np.uint8))
    monkeypatch.setattr(cv2, "imwrite", lambda *args: True)

    _, metadata = provider.detect_from_image(b"image", "classroom_01", 30)
    path = metadata["annotated_image_path"]
    assert path is not None
    assert path == path.rsplit("/", 1)[-1]
    assert str(tmp_path) not in path
