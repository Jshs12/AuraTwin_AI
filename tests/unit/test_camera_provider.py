from backend.core.camera import RTSPCameraProvider


def test_rtsp_provider_uses_bounded_ffmpeg_open_and_read_and_redacts_url(monkeypatch, capsys):
    import backend.core.camera as camera_module

    secret_url = "rtsp://demo-user:demo-password@camera.invalid/live"
    monkeypatch.setenv("ZONE_CAMERA_CLASSROOM_01", secret_url)
    monkeypatch.setenv("RTSP_OPEN_TIMEOUT_MS", "1200")
    monkeypatch.setenv("RTSP_READ_TIMEOUT_MS", "2200")
    seen = {}

    class UnavailableCapture:
        def isOpened(self):
            return False
        def release(self):
            seen["released"] = True

    def video_capture(*args):
        seen["args"] = args
        return UnavailableCapture()

    monkeypatch.setattr(camera_module.cv2, "VideoCapture", video_capture)
    assert RTSPCameraProvider().get_frame("classroom_01") is None
    output = capsys.readouterr().out
    assert "demo-password" not in output
    assert secret_url not in output
    args = seen["args"]
    assert args[0] == secret_url
    assert args[1] == camera_module.cv2.CAP_FFMPEG
    assert args[2] == [camera_module.cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 1200,
                       camera_module.cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2200]
    assert seen["released"] is True


def test_rtsp_provider_clamps_invalid_and_extreme_timeouts(monkeypatch):
    monkeypatch.setenv("RTSP_OPEN_TIMEOUT_MS", "bad")
    monkeypatch.setenv("RTSP_READ_TIMEOUT_MS", "999999")
    provider = RTSPCameraProvider()
    assert provider._timeout_ms("RTSP_OPEN_TIMEOUT_MS") == 5000
    assert provider._timeout_ms("RTSP_READ_TIMEOUT_MS") == 30000
