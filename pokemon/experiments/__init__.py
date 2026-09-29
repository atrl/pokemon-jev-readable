"""Isolated, comparable Pokémon control experiments.

The existing runtime uses top-level module imports. Keep its import root
available without importing an emulator, a model, or optional ML dependencies.
"""

from pathlib import Path
import sys

_RUNTIME = str(Path(__file__).resolve().parents[1])
if _RUNTIME not in sys.path:
    sys.path.insert(0, _RUNTIME)
