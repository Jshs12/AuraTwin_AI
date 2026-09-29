"""
YOLOOccupancyProvider — Real computer-vision occupancy provider.

Uses Ultralytics YOLO for person detection (class 0 only).
This is occupancy analytics — NOT facial recognition or identity tracking.

The provider counts the number of people visible in a given image frame
and returns a standard OccupancyEvent through the OccupancyProvider interface.
"""

import os
import uuid
import numpy as np
from pathlib import Path
from datetime import datetime
from backend.core.time import utc_now
from typing import Optional, Tuple, List

from backend.core.interfaces import OccupancyProvider
from backend.core.paths import PROJECT_ROOT, resolve_project_path
from backend.schemas.events import OccupancyEvent
from backend.core.events import EventTrace


def configure_ultralytics_directory() -> Path:
    """Use a writable, portable Ultralytics config location by default."""
    configured_dir = os.environ.get("YOLO_CONFIG_DIR")
    config_dir = resolve_project_path(configured_dir or "data/ultralytics")
    config_dir.mkdir(parents=True, exist_ok=True)
    os.environ["YOLO_CONFIG_DIR"] = str(config_dir)
    return config_dir


def _safe_load_error(error: Exception) -> str:
    message = str(error).strip()
    for key, value in os.environ.items():
        key_upper = key.upper()
        if value and len(value) >= 6 and any(marker in key_upper for marker in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
            message = message.replace(value, "<redacted>")
    message = message.replace(str(PROJECT_ROOT), "<project-root>")
    message = message.replace(str(Path.home()), "~")
    return f"{type(error).__name__}: {message[:300]}"


class YOLOOccupancyProvider(OccupancyProvider):
    """
    Real computer-vision occupancy provider using YOLO person detection.

    Configuration:
        YOLO_MODEL_PATH: Path to the YOLO model weights (default: yolov8n.pt)
        YOLO_CONFIDENCE: Inference confidence threshold (default: 0.40)
        YOLO_DEVICE: Device for inference — 'cpu', 'cuda', or 'auto' (default: auto)

    This provider does NOT perform:
        - Face recognition
        - Identity tracking
        - Demographic classification
    """

    PERSON_CLASS_ID = 0  # COCO class 0 = person

    def __init__(
        self,
        model_path: str = "yolov8n.pt",
        confidence: float = 0.40,
        device: str = "auto",
        output_dir: str = "data/detections",
    ):
        self.model_path = str(resolve_project_path(model_path))
        self.confidence = confidence
        self.device = device
        self.output_dir = resolve_project_path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.model = None
        self.load_error: Optional[str] = None
        self._last_detection: dict = {}  # zone_id -> OccupancyEvent
        self._load_model()

    def _load_model(self):
        """Attempt to load the YOLO model. Sets self.model to None on failure."""
        try:
            configure_ultralytics_directory()
            from ultralytics import YOLO

            device = self.device
            if device == "auto":
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"

            self.model = YOLO(self.model_path)
            self.model.to(device)
        except Exception as e:
            self.model = None
            self.load_error = _safe_load_error(e)
            print(f"[YOLOOccupancyProvider] Failed to load model: {self.load_error}")

    def is_ready(self) -> bool:
        """Check if the YOLO model is loaded and ready for inference."""
        return self.model is not None

    def get_occupancy(self, zone_id: str) -> OccupancyEvent:
        """
        Returns the last cached OccupancyEvent for the zone if available.

        If no detection has been performed yet for this zone, returns a
        default event with occupancy_state='UNKNOWN'.
        For real YOLO inference with an image, use detect_from_image() first.
        This method exists to satisfy the OccupancyProvider interface contract.
        """
        if zone_id in self._last_detection:
            return self._last_detection[zone_id]

        return OccupancyEvent(
            zone_id=zone_id,
            people_count=0,
            capacity=0,
            occupancy_percentage=0.0,
            occupancy_state="UNKNOWN",
            timestamp=utc_now(),
            source="yolo_occupancy_provider",
            simulated=False,
        )

    def detect_from_image(
        self,
        image_bytes: bytes,
        zone_id: str,
        zone_capacity: int,
    ) -> Tuple[OccupancyEvent, dict]:
        """
        Run YOLO person detection on a raw image.

        Args:
            image_bytes: Raw image file bytes (JPG/PNG/WEBP).
            zone_id: Target zone identifier.
            zone_capacity: Maximum capacity of the zone.

        Returns:
            Tuple of (OccupancyEvent, detection_metadata).
            detection_metadata contains:
                - people_count
                - confidences
                - processing_time_ms
                - annotated_image_path (or None)
                - model_name
                - provider_source
        """
        import time

        if not self.is_ready():
            raise RuntimeError(
                "YOLO model is not loaded. Check YOLO_MODEL_PATH configuration."
            )

        # Decode image
        import cv2

        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image. Supported formats: JPG, PNG, WEBP.")

        # Run inference
        start = time.perf_counter()
        results = self.model(img, conf=self.confidence, verbose=False)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        # Filter person detections only
        people_count = 0
        confidences: List[float] = []
        result = results[0]

        for box in result.boxes:
            cls_id = int(box.cls[0])
            if cls_id == self.PERSON_CLASS_ID:
                people_count += 1
                confidences.append(round(float(box.conf[0]), 4))

        # Save annotated image
        annotated_path = None
        try:
            annotated_img = result.plot()
            filename = f"{zone_id}_{uuid.uuid4().hex[:8]}.jpg"
            output_path = self.output_dir / filename
            if cv2.imwrite(str(output_path), annotated_img):
                # Return only the public static asset name, never a filesystem path.
                annotated_path = filename
        except Exception:
            annotated_path = None

        # Calculate occupancy
        capacity = max(zone_capacity, 1)
        percentage = (people_count / capacity) * 100.0

        if people_count == 0:
            state = "EMPTY"
        elif percentage < 30.0:
            state = "LOW"
        elif percentage < 70.0:
            state = "MEDIUM"
        else:
            state = "HIGH"

        event = OccupancyEvent(
            zone_id=zone_id,
            people_count=people_count,
            capacity=zone_capacity,
            occupancy_percentage=round(percentage, 2),
            occupancy_state=state,
            timestamp=utc_now(),
            source="yolo_occupancy_provider",
            simulated=False,
            observed_at=utc_now(),
        )

        # Cache for subsequent get_occupancy() calls
        self._last_detection[zone_id] = event

        # Log trace
        EventTrace.log_event(
            "YOLO_DETECTION",
            zone_id,
            "yolo_occupancy_provider",
            {
                "people_count": people_count,
                "confidences": confidences,
                "processing_time_ms": round(elapsed_ms, 2),
                "model": Path(self.model_path).name,
            },
        )

        metadata = {
            "people_count": people_count,
            "confidences": confidences,
            "processing_time_ms": round(elapsed_ms, 2),
            "annotated_image_path": annotated_path,
            "model_name": Path(self.model_path).name,
            "provider_source": "yolo_occupancy_provider",
        }

        return event, metadata
