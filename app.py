"""Backward-compatible launcher for the src-layout application."""
from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parent / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from cyber_doctor.app import start_gradio


if __name__ == "__main__":
    start_gradio()
