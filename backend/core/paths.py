"""Portable paths rooted at the AuraTwin application directory."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def resolve_project_path(path: str | Path) -> Path:
    """Resolve relative application paths from the project root, not CWD."""
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = PROJECT_ROOT / candidate
    return candidate.resolve()
