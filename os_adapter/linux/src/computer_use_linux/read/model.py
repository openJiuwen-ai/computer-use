from dataclasses import dataclass


@dataclass(frozen=True)
class NativeQueryScope:
    operation: object
    operation_token: str
    target: dict
    request: dict
    kind: str
    launch: bool
    directory: bool
