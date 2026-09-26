import os
import cv2
from pathlib import Path
from typing import Optional
from backend.core.interfaces import CameraProvider
from backend.core.paths import resolve_project_path

class MockCameraProvider(CameraProvider):
    # Explicit zone → starting image index.
    # Using hash(zone_id) is NOT stable across Python processes because
    # PYTHONHASHSEED is randomized by default in Python 3.3+.
    # This explicit map guarantees the same zone always starts from the same
    # image regardless of process restart, Python version, or PYTHONHASHSEED.
    ZONE_START_INDEX: dict = {
        "classroom_01": 0,  # seq_000001.jpg (index 0 of seq files)
        "classroom_02": 1,  # seq_000002.jpg
        "lab_01":       2,  # seq_000003.jpg
        "lab_02":       3,  # seq_000004.jpg
    }

    def __init__(self, test_data_dir: str = "data/test_images"):
        self.test_data_dir = resolve_project_path(test_data_dir)
        self._image_paths = []
        self._zone_indices = {}
        if self.test_data_dir.exists():
            for ext in ('*.jpg', '*.jpeg', '*.png', '*.webp'):
                self._image_paths.extend(list(self.test_data_dir.rglob(ext)))
        
        # Filter out .part files
        self._image_paths = [p for p in self._image_paths if not p.name.endswith(".part")]
        
        # Sort to ensure deterministic file ordering across runs
        self._image_paths.sort()

    def get_frame(self, zone_id: str) -> Optional[bytes]:
        """Returns a frame from the test dataset, cycling through them per zone."""
        if not self._image_paths:
            print(f"[MockCameraProvider] No images found in {self.test_data_dir}")
            return None

        if zone_id not in self._zone_indices:
            # Explicit deterministic starting index — stable across restarts.
            # Falls back to index 0 for any unknown zone.
            start = self.ZONE_START_INDEX.get(zone_id, 0) % len(self._image_paths)
            self._zone_indices[zone_id] = start

        # Cycle through images for this zone
        idx = self._zone_indices[zone_id]
        img_path = self._image_paths[idx]
        self._zone_indices[zone_id] = (idx + 1) % len(self._image_paths)

        try:
            with open(img_path, "rb") as f:
                return f.read()
        except Exception as e:
            print(f"[MockCameraProvider] Error reading sample image ({type(e).__name__}).")
            return None


class RTSPCameraProvider(CameraProvider):
    @staticmethod
    def _timeout_ms(name: str, default: int = 5000) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            return default
        return min(max(value, 250), 30000)

    def get_frame(self, zone_id: str) -> Optional[bytes]:
        """Reads a frame from the zone's configured RTSP stream."""
        env_key = f"ZONE_CAMERA_{zone_id.upper()}"
        rtsp_url = os.getenv(env_key)

        if not rtsp_url:
            print(f"[RTSPCameraProvider] No RTSP stream configured for {zone_id}.")
            return None

        cap = None
        try:
            # OpenCV's FFmpeg backend supports bounded open/read operations.
            # The URL may contain credentials; never include it in diagnostics.
            open_timeout = self._timeout_ms("RTSP_OPEN_TIMEOUT_MS")
            read_timeout = self._timeout_ms("RTSP_READ_TIMEOUT_MS")
            cap = cv2.VideoCapture(
                rtsp_url,
                cv2.CAP_FFMPEG,
                [cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, open_timeout,
                 cv2.CAP_PROP_READ_TIMEOUT_MSEC, read_timeout],
            )
            if not cap.isOpened():
                print(f"[RTSPCameraProvider] Stream unavailable for {zone_id}.")
                return None

            ret, frame = cap.read()
            if not ret or frame is None:
                print(f"[RTSPCameraProvider] Frame capture failed for {zone_id}.")
                return None

            # Encode to JPEG bytes
            success, buffer = cv2.imencode('.jpg', frame)
            if not success:
                print(f"[RTSPCameraProvider] Frame encoding failed for {zone_id}.")
                return None

            return buffer.tobytes()

        except Exception as e:
            print(f"[RTSPCameraProvider] Capture failed for {zone_id} ({type(e).__name__}).")
            return None
        finally:
            if cap is not None:
                try:
                    cap.release()
                except Exception:
                    pass
