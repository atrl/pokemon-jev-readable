"""Single import bootstrap shared by the Python regression tests."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
POKEMON = ROOT / "pokemon"
sys.path.insert(0, str(POKEMON))
