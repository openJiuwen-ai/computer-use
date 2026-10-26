"""Session discovery hint shared by keyboard units; never an input authority."""

from dataclasses import dataclass


@dataclass
class NativeKeyboardRecipients:
    hint: tuple | None = None

    def clear(self):
        self.hint = None
