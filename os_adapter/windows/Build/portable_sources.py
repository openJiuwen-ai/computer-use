# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Portable native mechanism source groups; excludes COM/Win32 and workers.

Each historic mechanism source gains only its extracted contracts and pipeline
stages. Real desktop builds compile Sources recursively as one original target;
Workers/Capture remains a separate target.
"""

from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
EXPANSIONS = {
    "NativeRuntime.cs": (
        "Contracts",
        "Read/Query",
        "Read/NativeObservationGraph.cs",
        "Capture/Query",
        "Runtime/Resources",
        "Runtime/NativeWriteOperation.cs",
    ),
    "NativeExecution.cs": (
        "Contracts",
        "Control/Object",
        "Control/Click",
        "Control/SelectionInsert",
        "Runtime/NativeWriteOperation.cs",
    ),
    "NativeKeyboard.cs": (
        "Contracts",
        "Control/Keyboard/Preparation",
        "Control/Keyboard/TextDispatch",
        "Control/Keyboard/CharacterDispatch",
    ),
    "NativePointer.cs": (
        "Control/NativeElementHit.cs",
        "Control/NativePointerClickAdmission.cs",
        "Contracts/NativePointerGesture.cs",
    ),
}


def expanded_sources(sources, base=BASE):
    result = list(map(Path, sources))
    for source in tuple(result):
        for relative in EXPANSIONS.get(source.name, ()):
            path = base / "Sources" / relative
            result.extend(sorted(path.rglob("*.cs")) if path.is_dir() else (path,))
    return list(dict.fromkeys(result))
