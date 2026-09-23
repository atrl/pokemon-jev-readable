"""The complete physical input contract shared by emulator and JEV."""

BUTTONS = {
    "up": "Press UP: walk north, turn north, or move a menu cursor up.",
    "down": "Press DOWN: walk south, turn south, or move a menu cursor down.",
    "left": "Press LEFT: walk west, turn west, or move a menu cursor left.",
    "right": "Press RIGHT: walk east, turn east, or move a menu cursor right.",
    "a": "Press A: confirm, interact with the object ahead, or advance dialog.",
    "b": "Press B: cancel or return from the current menu.",
    "start": "Press START: enter the title menu or open/close the in-game menu.",
    "select": "Press SELECT: use the game's context-specific selection function.",
    "wait": "Release all buttons and let text, animation or a transition finish.",
}


DEFAULT_GAME_GOAL = (
    "Complete Pokemon Red Star's main story: defeat the Pokemon League Champion "
    "and reach the Hall of Fame. Treat exploration, navigation, team preparation "
    "and battles as subgoals serving that objective. Claim completion only from "
    "verified in-game evidence, never from action count, visited coordinates or confidence."
)
