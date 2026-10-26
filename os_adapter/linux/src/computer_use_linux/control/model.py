from dataclasses import dataclass


@dataclass(frozen=True)
class NativeInputStep:
    operation: object
    step: int
    command: dict
