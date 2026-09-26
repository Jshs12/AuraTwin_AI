import os
from pathlib import Path

from backend.core.paths import PROJECT_ROOT, resolve_project_path
from backend.core.yolo_provider import configure_ultralytics_directory


def test_relative_project_paths_ignore_current_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    assert resolve_project_path("yolov8n.pt") == (PROJECT_ROOT / "yolov8n.pt").resolve()
    assert resolve_project_path("data/test_images") == (PROJECT_ROOT / "data/test_images").resolve()


def test_ultralytics_config_uses_project_local_directory_by_default(monkeypatch):
    monkeypatch.delenv("YOLO_CONFIG_DIR", raising=False)

    config_dir = configure_ultralytics_directory()

    assert config_dir == (PROJECT_ROOT / "data/ultralytics").resolve()
    assert config_dir.is_dir()
    assert Path(os.environ["YOLO_CONFIG_DIR"]) == config_dir
