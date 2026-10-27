"""Explicit portable source expansion for the selected Windows mechanism."""

import importlib.util
from pathlib import Path

from repository import ROOT

BASE = ROOT / "os_adapter/windows"
spec = importlib.util.spec_from_file_location("windows_portable_sources", BASE / "Build/portable_sources.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def native_source(relative):
    if relative.startswith("Tests/"):
        return ROOT / "tests/native/windows" / relative.removeprefix("Tests/")
    return BASE / relative


def portableSources(command):
    sources, options = [], []
    for argument in command:
        path = Path(str(argument))
        if path.suffix == ".cs" and path.is_relative_to(BASE):
            sources.append(path)
        else:
            options.append(argument)
    return [*options, *(str(path) for path in module.expanded_sources(sources, BASE))]
