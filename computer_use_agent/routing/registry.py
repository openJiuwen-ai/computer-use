# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Path registry: declared routes, JSON file loading and OpenAPI import."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from .paths import ApiSpec, CliSpec, Granularity, PathKind, RiskClass, SdkSpec, TaskPath, VerifySpec

_VALID_KINDS = {kind.value for kind in PathKind}
_VALID_RISKS = {risk.value for risk in RiskClass}


class PathRegistry:
    def __init__(self):
        self._paths: dict[str, TaskPath] = {}

    def register(self, path: TaskPath) -> TaskPath:
        self._paths[path.id] = path
        return path

    def remove(self, path_id: str):
        self._paths.pop(path_id, None)

    def get(self, path_id: str) -> TaskPath | None:
        return self._paths.get(path_id)

    def all(self) -> list[TaskPath]:
        return list(self._paths.values())

    def query(self, *, app: str | None = None, keywords: tuple[str, ...] = ()) -> list[TaskPath]:
        """Filter by app and keyword overlap against path metadata (v1 deterministic matching)."""

        lowered = tuple(word.lower() for word in keywords)
        selected = []
        for path in self._paths.values():
            if not path.enabled:
                continue
            if app and path.app not in (app, "*"):
                continue
            if lowered and not any(
                word in path.intent.lower() or any(word in key.lower() for key in path.keywords) for word in lowered
            ):
                continue
            selected.append(path)
        return selected

    def load_file(self, file_path: Path | str) -> int:
        payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            paths = payload.get("paths", [])
        elif isinstance(payload, list):
            paths = payload
        else:
            raise TypeError("paths file must contain a list or a {'paths': [...]} object")
        if not isinstance(paths, list):
            raise TypeError("paths must be a list")
        count = 0
        for raw in paths:
            self.register(path_from_dict(raw))
            count += 1
        return count

    def import_openapi(self, spec: dict[str, Any], *, app: str, base_url: str | None = None, prefix: str | None = None):
        """Turn each operation into an api path with a shared http_status verifier.

        Returns the imported paths. Endpoints become multiple candidate routes
        for the same intent; metrics differentiate them over time.
        """

        base = (base_url or spec.get("servers", [{}])[0].get("url", "")).rstrip("/")
        imported = []
        for route, operations in (spec.get("paths") or {}).items():
            if not isinstance(operations, dict):
                continue
            for method, operation in operations.items():
                if method.lower() not in ("get", "post", "put", "patch", "delete", "head"):
                    continue
                if not isinstance(operation, dict):
                    continue
                operation_id = (
                    operation.get("operationId")
                    or f"{method}_{route.strip('/').replace('/', '_').replace('{', '').replace('}', '')}"
                )
                path_id = f"{prefix + '.' if prefix else ''}{app}.api.{operation_id}"
                summary = operation.get("summary") or operation.get("description") or operation_id
                parameters = {}
                required = []
                for parameter in operation.get("parameters") or []:
                    name = parameter.get("name")
                    if not name:
                        continue
                    parameters[name] = {"type": "string", "description": parameter.get("description", "")}
                    if parameter.get("required"):
                        required.append(name)
                template = base + route
                if path_id in self._paths:
                    path_id = f"{path_id}.{method.lower()}"
                path = TaskPath(
                    id=path_id,
                    kind=PathKind.API,
                    app=app,
                    intent=summary,
                    arguments={
                        "type": "object",
                        "properties": parameters,
                        "required": required,
                        "additionalProperties": False,
                    },
                    risk=RiskClass.WRITE if method.lower() in ("post", "put", "patch", "delete") else RiskClass.READ,
                    api=ApiSpec(
                        method=method.upper(),
                        url=template,
                        time_hint=1.5,
                        cost_hint=0.0002,
                    ),
                    verify=VerifySpec(kind="http_status", expect=200),
                )
                self.register(path)
                imported.append(path)
        return imported

    @staticmethod
    def check_preconditions(path: TaskPath) -> tuple[bool, str]:
        """Runtime eligibility evidence: CLI paths require their declared binary."""

        if path.cli and path.cli.binary and shutil.which(path.cli.binary) is None:
            return False, f"binary {path.cli.binary!r} not found"
        return True, ""


def path_from_dict(raw: dict[str, Any]) -> TaskPath:
    kind = raw.get("kind")
    if kind not in _VALID_KINDS:
        raise ValueError(f"path kind must be one of {_VALID_KINDS}, got {kind!r}")
    risk = raw.get("risk", RiskClass.READ.value)
    if risk not in _VALID_RISKS:
        raise ValueError(f"path risk must be one of {_VALID_RISKS}, got {risk!r}")
    api = raw.get("api")
    cli = raw.get("cli")
    sdk = raw.get("sdk")
    verify = raw.get("verify")
    return TaskPath(
        id=raw["id"],
        kind=PathKind(kind),
        app=raw.get("app", "*"),
        intent=raw.get("intent", ""),
        arguments=raw.get("arguments", {}),
        risk=RiskClass(risk),
        keywords=tuple(raw.get("keywords", ())),
        preconditions=tuple(raw.get("preconditions", ())),
        granularity=Granularity(raw.get("granularity", "route")),
        api=ApiSpec(**api) if api else None,
        cli=CliSpec(argv=tuple(cli["argv"]), **{k: v for k, v in cli.items() if k != "argv"}) if cli else None,
        sdk=SdkSpec(**sdk) if sdk else None,
        verify=VerifySpec(**verify) if verify else None,
        enabled=raw.get("enabled", True),
    )
