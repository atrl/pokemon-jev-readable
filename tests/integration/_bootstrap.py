"""Bootstrap standalone integration commands without modifying game state."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
POKEMON = ROOT / "pokemon"
sys.path.insert(0, str(POKEMON))
from paths import default_rom
DEFAULT_ROM = default_rom()
