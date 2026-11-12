"""The one place fonts come from: Segoe UI for UI and chat text, Consolas for clocks.

Fonts are shared ``CTkFont`` objects, one per (family, base size, weight, slant). The "Text size"
setting changes the size of every shared font in place, so every widget updates live. Sizes are given
at "Medium" (chat 15, UI 13); "Small" and "Large" scale all of them by chat size / 15."""
from __future__ import annotations

import customtkinter as ctk

from .. import config

FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"
_MIN_SIZE = 8

FontKey = tuple[str, int, str, str]


class FontBook:
    """Shared fonts of the running app (UI state, like CustomTkinter's own trackers)."""

    def __init__(self) -> None:
        self.text_size = "Medium"
        self._fonts: dict[FontKey, ctk.CTkFont] = {}

    @property
    def chat_px(self) -> int:
        return config.CHAT_TEXT_SIZES[self.text_size]

    def scaled(self, base: int) -> int:
        """A size given at "Medium", in the current text size."""
        return max(_MIN_SIZE, round(base * self.chat_px / config.CHAT_FONT_SIZE))

    def get(self, family: str, base: int, weight: str = "normal", slant: str = "roman") -> ctk.CTkFont:
        key = (family, int(base), weight, slant)
        shared = self._fonts.get(key)
        if shared is None:
            shared = ctk.CTkFont(family=family, size=self.scaled(base), weight=weight,  # type: ignore[arg-type]
                                 slant=slant)  # type: ignore[arg-type]
            self._fonts[key] = shared
        return shared

    def set_text_size(self, name: str) -> bool:
        """Switch Small / Medium / Large; every shared font is resized in place. True when it changed."""
        if name not in config.CHAT_TEXT_SIZES or name == self.text_size:
            return False
        self.text_size = name
        for (_family, base, _weight, _slant), shared in self._fonts.items():
            shared.configure(size=self.scaled(base))
        return True

    def reset(self) -> None:
        """Forget fonts bound to an old Tk root (a new app window is being built)."""
        self._fonts.clear()


BOOK = FontBook()


def font(size: int = config.UI_FONT_SIZE, weight: str = "normal", slant: str = "roman") -> ctk.CTkFont:
    return BOOK.get(FAMILY, size, weight, slant)


def chat(size: int = config.CHAT_FONT_SIZE, weight: str = "normal") -> ctk.CTkFont:
    """Chat text; the default is the bubble size (15 at Medium)."""
    return BOOK.get(FAMILY, size, weight)


def mono(size: int = config.UI_FONT_SIZE, weight: str = "normal") -> ctk.CTkFont:
    return BOOK.get(MONO_FAMILY, size, weight)


def text_size() -> str:
    return BOOK.text_size


def set_text_size(name: str) -> bool:
    return BOOK.set_text_size(name)


def step_text_size(current: str, step: int) -> str:
    """The next smaller (-1) or larger (+1) text size, clamped; 0 means the default."""
    if step == 0:
        return "Medium"
    names = list(config.TEXT_SIZE_NAMES)
    index = names.index(current) if current in names else names.index("Medium")
    return names[max(0, min(len(names) - 1, index + step))]
