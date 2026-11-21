"""TastyPop Studio — K-푸드 3D 쇼츠 원클릭 자동화 패키지."""
from pathlib import Path

__all__ = ["__version__"]

try:
    __version__ = (Path(__file__).resolve().parent.parent / "VERSION").read_text(encoding="utf-8").strip()
except Exception:
    __version__ = "1.0"
