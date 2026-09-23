"""Project-relative assets and prompts; independent of the working directory."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "pokemon" / "data"
PROMPTS = ROOT / "prompts"
ROM_NAME = "red-star-2020-08-18.gb"


def default_rom() -> Path:
    """Honor an existing legacy root ROM; otherwise use the restored roms copy."""
    legacy = ROOT / ROM_NAME
    return legacy if legacy.is_file() else ROOT / "roms" / ROM_NAME


def load_prompt(relative: str, *, single_line: bool = False) -> str:
    """Load UTF-8 text without silently trimming or changing the model prompt."""
    file = (PROMPTS / relative).resolve()
    if not file.is_relative_to(PROMPTS.resolve()):
        raise ValueError("Prompt path must stay inside prompts/")
    text = file.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError(f"Empty prompt: {relative}")
    return " ".join(text.splitlines()) if single_line else text
